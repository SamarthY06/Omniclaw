# Android Debug & Cleanup Brief — paste into Cursor

You are working on a personal-assistant project at
`/Users/samarthyadannavar/Desktop/Personal/Personal Assistant/`. The Mac
side (`omniclaw/`) works and is golden — **do not modify anything inside
`omniclaw/`**. The Android side (`android/`) is broken end-to-end and is
the only thing you fix in this pass.

Before doing anything else, read these in full:

- `GOAL.md` — product vision and non-negotiables
- `USE_CASES.md` — what the assistant must do
- `BEN_ANDROID_SETUP.md` — current Android build/install/run guide
- `ARCHITECTURE_AND_VISION.md` — architecture, known drawbacks, target state
- `android/README.md` (if present)
- `android/app/src/main/AndroidManifest.xml`
- `android/app/build.gradle.kts` and `android/build.gradle.kts`
- `android/app/src/main/assets/node/package.json` (the embedded Node side)
- `android/app/src/main/assets/node/index.js`
- Every file under `android/app/src/main/java/com/ben/`

Do not write any code in the first pass. The first pass is **diagnosis
only** — produce a written report under `android/DIAGNOSIS.md` that walks
through each numbered task below, says what you found, and proposes the
smallest possible patch. Wait for my approval before changing source files.

---

## Hard constraints

1. **Do not modify any file outside `android/`** (Mac side is frozen).
2. Keep the multi-device peer-mesh design. The cross-device demo (Mac says
   something → Android executes it, or vice versa) is the whole point of
   this project. Do not propose collapsing to single-device.
3. Voice provider stays **OpenAI Realtime** for now. Do not switch to
   Groq/Cartesia/Deepgram. The only thing you may do for voice is make
   the model id and voice name configurable from a single config source
   so a future swap is one line.
4. The pairing wire protocol must remain compatible with the current Mac
   side. The QR code is generated on Mac; Android scans it. You may
   change Android's parser to accept additional schemes, but you must
   keep accepting whatever the Mac side currently emits.
5. Replace `gpt-4o` / `gpt-4o-mini` vision references with OpenAI's
   *current* latest vision-capable model. Look up the OpenAI model
   catalog at run time (or read the current OpenAI Models docs) rather
   than guessing — pick the latest GA non-preview model that supports
   image input. Centralize the model name in **one** config constant so
   future swaps are a single-line change. Do not hard-code the model
   name in more than one place.
6. Add a cost ledger (see Task 9).
7. Add a top-level `LICENSE` file (Apache-2.0). Add SPDX headers only
   if trivial — do not invasively annotate every source file in this pass.
8. Remove user-facing "Jarvis" naming on Android (see Task 10). The
   product name on Android remains **"Ben"** for now — that's already
   the convention in `BenAccessibilityService`, `BenForegroundService`,
   etc.

---

## Investigation tasks

For each numbered task below, write a section in `android/DIAGNOSIS.md`
with these subheadings:

- **Hypothesis** — what you think is wrong before looking
- **Evidence** — what you actually found in the files, in logs from
  `adb logcat`, in the manifest, in build output, in the running app
- **Root cause** — your conclusion
- **Smallest patch** — the minimum change that fixes it (file paths +
  diff sketch, not full code)
- **Risks** — what could break elsewhere

### 1. Does the build even produce a working APK today?

Hypothesis: the `bootstrap.sh` flow described in `BEN_ANDROID_SETUP.md`
fails on a clean machine because of missing SDK / NDK / ABI mismatches /
`nodejs-mobile` packaging.

Verify:

- Run `cd android && ./gradlew assembleDebug --stacktrace` (or whatever
  the bootstrap script calls) and capture exit code + last 200 lines.
- Confirm `compileSdk`, `targetSdk`, `minSdk`, NDK version, and
  Kotlin/AGP versions in `android/app/build.gradle.kts` match a working
  AGP + Gradle pair. Note any AGP-vs-Gradle incompatibilities.
- Confirm the `abiFilters` block actually includes `arm64-v8a` and that
  the matching `libnode.so` exists at the path the bundler looks for.
  Where does `libnode.so` come from — is it bundled in the repo, downloaded
  by a Gradle task, or expected to be installed by the user? Verify which.
- If the build relies on an `npm install` inside `assets/node/` at build
  time, confirm that step exists in the Gradle build graph (it usually
  doesn't, which is a common cause of "the APK is built but Node can't
  find `openclaw` at runtime").
- After install on a device, run `adb shell pm list packages | grep ben`
  and confirm the APK installed. Run `adb shell dumpsys package com.ben`
  and check that the AccessibilityService is *declared* in the manifest
  even before the user enables it.

Success criteria: a clean `assembleDebug` succeeds, produces an APK that
installs and launches without immediately crashing.

### 2. Does `nodejs-mobile` actually start inside the foreground service?

Hypothesis: `libnode.so` either isn't loaded, or it loads but
`require('openclaw')` fails because the npm dependencies were never
installed into `assets/node/node_modules/`.

Verify:

- Confirm `android/app/src/main/assets/node/` contains a `node_modules/`
  directory with `openclaw/` and `ws/` actually present. If not, find
  where they're supposed to be installed — bootstrap script, Gradle task,
  first-run download? Document the answer.
- Read `android/app/src/main/cpp/*.cpp` and `CMakeLists.txt` (or whatever
  hosts the JNI shim). Confirm `System.loadLibrary("node")` is called
  exactly once, and on which thread.
- Add a temporary log line at the very top of `assets/node/index.js`
  (`console.log("NODE_BOOTED")`) and check `adb logcat | grep -i node`
  after launching the app. Does Node start at all? If yes, does it print
  anything from `index.js`? If `index.js` is reached, does it get past
  `require('openclaw')` without throwing?
- Look for any thrown errors in logcat: `adb logcat *:E ReactNativeJS:V
  Node:V` and similar. Don't filter too aggressively until you've seen
  the whole startup output.
- Check the `engines` field in `assets/node/package.json` (currently
  `node >=20`) vs the actual bundled `libnode.so` version. **`nodejs-mobile`
  ships Node 18**. If `openclaw` requires Node 20 features (top-level
  await in CJS, structured-clone in workers, fetch builtin, etc.) it
  will crash. Document the version mismatch.

Success criteria: the embedded Node process boots, loads `openclaw`,
and prints a "ready" log line within 5 seconds of `BenForegroundService`
starting.

### 3. Is `BenAccessibilityService` actually bound by the OS?

Hypothesis: the service is declared but never enabled by the user (no
onboarding step pushes them into Settings → Accessibility), or the
`accessibility_service_config.xml` is malformed, or the manifest lacks
the BIND_ACCESSIBILITY_SERVICE permission attribute.

Verify:

- Read `android/app/src/main/res/xml/accessibility_service_config.xml`
  (or wherever the config lives). Confirm
  `android:accessibilityEventTypes`, `android:packageNames`,
  `android:accessibilityFeedbackType`, and `android:canRetrieveWindowContent`
  are set sanely. `packageNames` empty means "all packages"; that's
  usually what we want.
- Confirm the manifest entry for `BenAccessibilityService`:
  ```
  <service
      android:name=".service.BenAccessibilityService"
      android:permission="android.permission.BIND_ACCESSIBILITY_SERVICE"
      android:exported="false">
      <intent-filter>
          <action android:name="android.accessibilityservice.AccessibilityService"/>
      </intent-filter>
      <meta-data
          android:name="android.accessibilityservice.accessibilityService"
          android:resource="@xml/accessibility_service_config"/>
  </service>
  ```
  Anything missing? Anything spelled wrong?
- On the device, manually walk Settings → Accessibility → Installed
  services → Ben → enable. After enabling, run
  `adb shell settings get secure enabled_accessibility_services` and
  confirm `com.ben/.service.BenAccessibilityService` appears.
- Does `PermissionGateActivity` (or whichever activity handles the
  onboarding flow) deep-link the user to Accessibility settings via
  `Settings.ACTION_ACCESSIBILITY_SETTINGS`? Or does it require them to
  navigate manually? If manual, that's a UX bug — fix it.
- Once enabled, log every `onAccessibilityEvent` for 30 seconds.
  Confirm events fire when you tap around other apps. If no events,
  the service isn't actually running.

Success criteria: tapping a button in WhatsApp produces a logged
`TYPE_VIEW_CLICKED` event inside `BenAccessibilityService` within 100 ms.

### 4. Is the foreground service surviving Doze / battery saver / OEM kills?

Hypothesis: `BenForegroundService` starts, then dies within minutes on
Xiaomi/OnePlus/Realme/Samsung devices because of MIUI/OxygenOS aggressive
kill behavior, missing battery exemption, or wrong `foregroundServiceType`.

Verify:

- Manifest's `BenForegroundService` entry — does it declare
  `android:foregroundServiceType="microphone|specialUse"`? On Android
  14+, mic capture requires `microphone` type; on Android 14+ for
  always-on services, `specialUse` with a justification string is
  required.
- Does the app request `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` and
  actually walk the user through granting it during onboarding?
- For OEM-specific kills, do `OnboardingActivity` and any
  `PermissionGateActivity` detect manufacturer (`android.os.Build.MANUFACTURER`)
  and present a step-by-step walkthrough for the killing OEMs? If not,
  this is the single biggest reason "Ben works for 10 min then dies."
  Add at minimum a manufacturer-detection step and a "long-press app icon
  → app info → battery → unrestricted" walkthrough.
- Run the app, then `adb shell dumpsys deviceidle force-idle` (puts
  device in Doze). Wait 60 s. Run `adb shell ps -A | grep com.ben` —
  is the process still alive? Run `adb shell dumpsys jobscheduler | grep
  com.ben` — are scheduled jobs being deferred?

Success criteria: foreground service stays alive for ≥1 hour with screen
off on a Pixel device and ≥30 min on a Xiaomi/OnePlus/Samsung device
with the OEM walkthrough completed.

### 5. Wake-word pipeline

Hypothesis: `SpeechRecognizer` is being used for always-on wake, which it
isn't designed for. It will throttle, miss wakes, and produce false
positives. *Do not replace the engine in this pass* — but diagnose
whether the current implementation works *at all*, and flag the engine
choice as future work.

Verify:

- Read `BenWakewordService.kt` and `WakePhraseMatcher.kt`. Confirm the
  `SpeechRecognizer` instance is started, and when results return, the
  matcher's fuzzy threshold is reasonable.
- Run the app, say "Ben" 20 times with varied tone/volume. Log how many
  match. Anything under 16/20 is broken.
- Run the app, *don't* say the wake word, leave the device for 5
  minutes. Log how many false positives fire. Anything over 1 is broken.
- Confirm `SpeechRecognizer.isRecognitionAvailable(this)` returns true
  on the test device and that `EXTRA_PREFER_OFFLINE` is set so audio
  isn't being shipped to Google.
- Confirm the service auto-restarts after every recognition cycle (the
  `SpeechRecognizer` API ends each session after a short silence). If
  there's no restart loop, the wake listener dies after the first
  utterance.

Success criteria: 18/20 wakes match, <1 false positive per 5 minutes
idle. Flag: file `android/FUTURE_WAKEWORD.md` recommending replacement
with openWakeWord / Picovoice Porcupine, but do not implement.

### 6. Pairing flow

Hypothesis: `OnboardingActivity` + the QR scanner + the deep-link handler
either don't open the camera, don't decode the Mac's QR, don't persist
the shared secret, or write the secret somewhere `BenSecrets.kt` can't
read.

Verify:

- Confirm `OnboardingActivity` requests CAMERA permission *before*
  opening the ZXing scanner. Many users see a black screen because the
  scanner is mounted before the permission grant returns.
- Confirm the deep-link intent filter in the manifest:
  ```
  <intent-filter android:autoVerify="false">
      <action android:name="android.intent.action.VIEW"/>
      <category android:name="android.intent.category.DEFAULT"/>
      <category android:name="android.intent.category.BROWSABLE"/>
      <data android:scheme="jarvis" android:host="pair"/>
      <data android:scheme="ben"    android:host="pair"/>  <!-- add this if missing -->
  </intent-filter>
  ```
  We accept **both** schemes for backward compat with the current Mac
  side (which emits `jarvis://pair?...`).
- Trace the path: QR scanned → URI parsed → secret/device_id/port
  extracted → `BenSecrets.put(...)` → `peer.json` written. At every
  step, log success/failure. The most common failure: the URL parser
  doesn't survive URL-encoded base64 secrets containing `+` and `=`.
- After scanning, confirm the secret is actually present in
  EncryptedSharedPreferences (you can dump via `adb shell run-as com.ben
  cat files/peer.json` if peer.json is plaintext, or write a temporary
  debug log line — *delete the log line before shipping*).

Success criteria: scan QR → device transitions to "paired" state → peer
client immediately attempts WS connect to the Mac peer IP/port from the
QR payload.

### 7. Peer transport (LAN + Tailscale)

Hypothesis: the JS peer client in `assets/node/src/peer/*.js` doesn't
match the wire format the Mac Python peer expects — schema version
mismatch, HMAC mismatch (off-by-one on the signed payload), or the
WebSocket library version differs in subtle behavior.

Verify:

- Read the Mac Python peer (`omniclaw/peer/server.py`, `client.py`,
  `protocol.py`) and the Android JS peer side-by-side. Confirm:
  - Same `SCHEMA_VERSION` constant
  - Same HMAC-SHA256 input string format (which fields, in which order,
    with which separators)
  - Same JSON-RPC 2.0 envelope (`jsonrpc`, `id`, `method`, `params`)
  - Same nonce/timestamp replay-protection rules
- With both sides running, capture the first 5 messages each direction
  with `tcpdump`/`wireshark` on the Mac (since the Android side is
  harder to packet-capture). Diff the structure against the Mac side's
  expected schema.
- For Tailscale fallback: confirm both devices appear in `tailscale
  status` and the Android side resolves the Mac's MagicDNS hostname.
  The Node `ws` library in some versions doesn't follow IPv6 link-local
  addresses correctly — confirm the connect uses the IPv4 form Tailscale
  hands out.
- mDNS discovery on LAN: confirm both sides advertise/listen on the
  same service type (e.g., `_ben-peer._tcp.local` or `_jarvis-peer._tcp.local`
  — pick one and be consistent). On Android use `NsdManager` from the
  Kotlin side (don't try to mDNS from inside the embedded Node — it
  doesn't have the right permissions).

Success criteria: from the Mac, `peer.ping({})` over the WSS link
returns `{ok: true, rtt_ms: <50}` to an Android device on the same LAN.
From the Mac with Android on cellular + Tailscale, same `peer.ping`
returns `{ok: true, rtt_ms: <300}`.

### 8. OpenAI Realtime voice session

Hypothesis: `BenVoiceService.kt` opens the WSS connection but either
the audio capture, the audio playback, or the WS session lifecycle is
wrong.

Verify:

- Confirm `BenVoiceService` is started by the foreground service (not
  by the activity), so it survives screen-off.
- Audio capture: which `AudioRecord` config? Realtime expects PCM16 at
  24 kHz mono. If you're sending 16 kHz or 48 kHz, the model still
  responds but quality is bad and latency rises. Confirm and fix.
- Audio playback: confirm the model's PCM16 24 kHz output is piped to
  `AudioTrack` configured for the same rate. Echo cancellation:
  `AcousticEchoCanceler.create(audioSessionId)` — is it being attached?
  Without it, the model hears itself and starts looping.
- WS lifecycle: when the Realtime session ends (server-side timeout,
  network blip), does the client *automatically* reconnect with backoff?
  Or does it die silently? Most likely cause of "voice doesn't work
  the second time."
- Make the model id and voice configurable in **one** place. Add
  `OpenAiConfig.kt` (or extend existing config) with:
  ```kotlin
  object OpenAiConfig {
      const val REALTIME_MODEL = "gpt-4o-realtime-preview"  // TODO: bump
      const val REALTIME_VOICE = "verse"
      const val VISION_MODEL = "gpt-5"  // see Task 9 — pick current latest
      const val CHAT_MODEL = "gpt-4o"   // ditto
  }
  ```
  Every other file imports these constants. No model strings inline anywhere
  else.

Success criteria: wake the device, say "what time is it" — model replies
in under 1.5 s with audible audio at full quality. Repeat the question
10 times in a row — every one succeeds (proves reconnection works).

### 9. Replace `gpt-4o` vision with OpenAI's current latest vision-capable model

Hypothesis: vision calls in `vision.read_screen` and any other tool that
sends screenshots are pinned to `gpt-4o` or `gpt-4o-mini`, which are no
longer the best/cheapest current option from OpenAI.

Tasks:

- Search the entire `android/` tree for `gpt-4o`, `gpt-4o-mini`,
  `4o-vision`, and any model-name literals. List every hit.
- Look up the OpenAI Models API page (`platform.openai.com/docs/models`)
  **at the time you run this** and identify the current latest GA
  (non-preview, non-deprecated) model that supports image input. As of
  this writing the likely candidates are in the `gpt-5`, `gpt-4.1`, or
  `o3` families — pick the cheapest one that handles screenshots well
  for *UI coordinate identification* (not the most expensive reasoning
  model; you don't need that for "where's the Send button"). Add a
  comment in `OpenAiConfig.VISION_MODEL` noting which docs page you
  consulted and the date.
- Centralize the model name in `OpenAiConfig.VISION_MODEL`. Every other
  reference becomes `OpenAiConfig.VISION_MODEL`.
- Add a runtime model-health check on app start: ping the model with a
  tiny image + prompt; if 404/model-not-found, log loudly and fall back
  to the next-cheapest known-working model (have a static fallback
  list, e.g. `[primary, "gpt-4o", "gpt-4o-mini"]`).
- Confirm the request body matches the current chat-completions schema
  for image inputs (`content: [{type: "text", ...}, {type: "image_url",
  image_url: {url: "data:image/jpeg;base64,..."}}]` — schema may have
  changed; check the docs).

Success criteria: vision tool calls work against the current latest
model, fall back to `gpt-4o` if the new model id is rejected, and the
model name is configurable from one file.

### 10. Cost ledger

Hypothesis: there is no cost meter today, so the user has no idea what
they're spending on each Realtime session, vision call, chat call.

Tasks:

- Create `android/app/src/main/java/com/ben/cost/CostLedger.kt`.
- Define `enum CallKind { REALTIME_AUDIO_IN, REALTIME_AUDIO_OUT,
  REALTIME_TEXT_IN, REALTIME_TEXT_OUT, VISION_IMAGE, VISION_TEXT_IN,
  CHAT_IN, CHAT_OUT, STT, TTS }`. Use the OpenAI per-token / per-minute
  pricing for each. Hard-code prices in `CostPrices.kt` (with a
  comment noting the source URL and date).
- Wrap every OpenAI HTTP/WS client so that each call computes its
  estimated cost from the response usage metadata (Realtime sends
  `response.done` with `usage`; chat-completions sends `usage`; vision
  is per-image at the current rate). Add the cost to the ledger.
- Persist running totals in `EncryptedSharedPreferences` (or a small
  Room DB if you prefer) — `daily_cents`, `monthly_cents`, plus a
  per-CallKind breakdown.
- Hard cap: read `daily_cap_usd` and `monthly_cap_usd` from
  `SettingsActivity`. If the next call would exceed the cap, **refuse
  the call**, log the refusal, and have the assistant speak "I've hit
  today's spending cap, $X.XX. Tap the cost screen to raise it."
- Expose a `CostFragment` (or a screen in the existing settings) that
  shows: today, this month, by call kind, and a chart of the last 30
  days.
- Reset daily totals at local midnight, monthly totals on the 1st.

Success criteria: every Realtime/Vision/Chat call increments the ledger,
SettingsActivity shows the live total, and the assistant refuses to
exceed the configured cap.

### 11. Naming cleanup — remove "Jarvis"

Tasks (Android only):

- Search `android/` for `Jarvis`, `JARVIS`, `jarvis`, and `jarvis://`.
  List every hit before changing anything.
- For user-facing strings (notifications, dialogs, onboarding copy,
  app label, channel names): replace with "Ben."
- For internal identifiers (deep-link scheme, mDNS service type, log
  tags, intent extras, EncryptedSharedPreferences keys):
  - **Keep `jarvis://pair` working** (the Mac side still emits it).
  - **Add `ben://pair` as an alias** in the manifest intent filter.
  - The deep-link handler accepts both schemes and treats them identically.
  - For mDNS service type, keep what the Mac side advertises (likely
    `_jarvis-peer._tcp.local` or similar). Document this in a "TODO:
    rename after coordinated Mac change" note in
    `android/MIGRATION_TODO.md`. Don't break LAN discovery in this pass.
  - For internal EncryptedSharedPreferences keys, leave them alone unless
    they're trivially renameable without data loss.
- Update README and onboarding copy to use "Ben" exclusively for the
  product name. The word "Jarvis" should appear nowhere in any
  user-visible surface.

Success criteria: a fresh install shows the word "Ben" everywhere a
user can see; the word "Jarvis" appears only in coordinated-rename TODOs.

### 12. Open-source license

Tasks:

- Add `LICENSE` at the repo root containing the standard **Apache
  License, Version 2.0** text (the exact text from
  https://www.apache.org/licenses/LICENSE-2.0.txt — do not paraphrase).
- Add `NOTICE` at the repo root with one line:
  `Ben — Copyright 2026 Samarth Yadannavar` (or your full legal name).
- Add `LICENSE-headers` paragraph in `CONTRIBUTING.md` (create if
  missing) stating: "Contributions are licensed under Apache-2.0. By
  submitting a pull request, you agree your contribution is licensed
  under the same terms."
- Do **not** add SPDX headers to every source file in this pass — that's
  a separate sweep. Just the root files.
- Add a `LICENSE` badge to the top of any README that exists.

Open question for me (the human) to decide:

- Does `omniclaw/` (Mac side) stay in this repo or split to a separate
  repo? If split, that's a separate task. If same repo, Apache-2.0 at the
  root covers it.

---

## Acceptance criteria for the full pass

After all 12 tasks are diagnosed and (where approved) patched, the
following must hold:

1. `./gradlew assembleDebug` succeeds on a clean clone.
2. Installed APK launches without crash.
3. Onboarding walks the user through: notification permission → mic
   permission → camera permission → battery optimization exemption →
   manufacturer-specific kill walkthrough (if applicable) → accessibility
   service enable → QR-code pairing with the Mac.
4. After onboarding, `BenForegroundService` stays alive for ≥30 minutes
   with screen off.
5. Embedded Node boots, loads `openclaw`, and logs "ready."
6. `BenAccessibilityService` is bound and receives events from other apps.
7. Wake word fires at least 18/20 attempts and false-positives <1 per
   5 min idle.
8. Peer client connects to the Mac via WSS on LAN within 3 s of pairing
   and over Tailscale within 10 s off-LAN.
9. `peer.delegate({task: "what's the battery level"})` from the Mac
   returns a valid response from the Android side within 5 s.
10. Voice round-trip via OpenAI Realtime works for at least 10 consecutive
    queries without manual reconnect.
11. Vision tool uses the current latest OpenAI vision-capable model, with
    automatic fallback to `gpt-4o` if the new model id is rejected.
12. Cost ledger increments on every paid API call and is visible in
    Settings.
13. No user-facing "Jarvis" strings; both `jarvis://pair` and
    `ben://pair` deep links work for backward compatibility.
14. `LICENSE` (Apache-2.0) + `NOTICE` exist at the repo root.

---

## Simulated end-to-end test cases

These are real-world voice-driven scenarios that exercise the whole
Android stack end-to-end: wake word -> STT -> planner (embedded Node)
-> accessibility and/or vision tools -> on-screen action -> TTS ->
cost ledger. They are run **manually on a real device** that has
already been paired with the Mac. They are smoke + acceptance
scenarios that map back to the 12 investigation tasks above; they
are not unit tests.

Run them in order on a fresh build. T01 must pass before any other
test because it establishes pairing and permission state. After T01
the rest can be run in any order.

The exact log-line strings quoted below ("logcat: `Node: tool=...`",
etc.) are **illustrative**. They describe the spirit of each pass
criterion, not necessarily the format the current code prints today.
Adapt grep patterns to match whatever the implementation actually
emits, but keep the pass criteria themselves unchanged.

### How to run

Prerequisites:

- One Android test device. A Pixel is the baseline; for T13 you also
  need at least one of {Xiaomi/MIUI, OnePlus/OxygenOS, Realme,
  Samsung/OneUI}.
- The Mac peer running and reachable on the same Wi-Fi LAN, plus
  Tailscale installed and authenticated on both ends for T10.
- Test accounts already signed in on the device for WhatsApp, Amazon,
  and Spotify. These tests do real things in real apps; do not run
  them on a production account.
- ADB connected over USB or wireless: `adb devices` lists the device.
- A wall clock or `time` for the latency budgets.

Recommended logcat filter. Open in a separate terminal before each
test and clear between runs:

```
adb logcat -c
adb logcat -s Ben:V Node:V BenForegroundService:V \
              BenAccessibilityService:V BenVoiceService:V \
              BenWakeword:V CostLedger:V PeerClient:V
```

Mac peer CLI commands referenced below use the same surface defined
in Task 7 of this brief: `peer.ping(...)`, `peer.delegate({task: ...})`.
Use whichever transport the Mac side exposes (CLI, REPL, or admin
endpoint). The test only cares about request -> response.

Pass/fail recording template, one line per test in your run log:

```
TXX | YYYY-MM-DD HH:MM | device=<pixel|xiaomi|...> | net=<wifi|cellular> | result=<pass|fail> | notes=<...>
```

If a test fails, attach the full filtered logcat from `adb logcat -c`
through the trigger to the failure point, plus a screenshot from
`adb exec-out screencap -p > /tmp/fail.png`.

### Test fixtures

To keep these tests deterministic, set up these fixtures once on the
test device before T04 onwards:

- **Test contact**: a contact named `Mom` mapped to a known test
  phone number you control (or yourself). T04 sends a real WhatsApp
  message to this contact.
- **Test product**: the search query `iPhone 15 case` on Amazon. T05
  adds the first organic result to the cart and removes it on cleanup.
- **Test track**: `Bohemian Rhapsody` on Spotify. T06 starts playback
  and pauses on cleanup.
- **Cost caps for T15**: note your current `daily_cap_usd` and
  `monthly_cap_usd` from Settings; T15 temporarily drops the daily
  cap to `0.01` and restores it on cleanup.
- **Vision-model debug switch for T08**: a debug-build-only override
  (BuildConfig flag, dev menu, or `adb shell am broadcast` extra)
  that lets you point `OpenAiConfig.VISION_MODEL` at an arbitrary
  string at runtime. If no such switch exists yet, file it as a
  needed test affordance and skip T08 until it lands.

### Coverage matrix

Each test maps back to the investigation tasks numbered 1-12 above:

- Task 1 (build/APK) -> T01 precondition. A build that won't install
  fails T01 outright.
- Task 2 (Node boots) -> T01 readiness check; every test thereafter
  depends on Node being up.
- Task 3 (accessibility bound) -> T04, T05, T06, T11.
- Task 4 (foreground survival) -> T12, T13.
- Task 5 (wake word) -> T02, T04, T12, T14.
- Task 6 (pairing) -> T01.
- Task 7 (peer transport) -> T01, T09, T10, T11.
- Task 8 (Realtime voice) -> T02, T03, every voice-driven test.
- Task 9 (vision model) -> T05, T07, T08.
- Task 10 (cost ledger) -> T02, T05, T15.
- Task 11 (no "Jarvis" UI) -> T01 (deep-link + UI inspection
  sub-step).
- Task 12 (LICENSE + NOTICE) -> the Repo-audit block at the end of
  this section.

### T01 - Cold-start pairing and smoke

**Covers tasks**: 1, 2, 3, 4 (briefly), 6, 7, 11.

**Preconditions**: device wiped of any prior `com.ben` install
(`adb uninstall com.ben` if present). Mac peer running and showing
a fresh pairing QR.

**Trigger**:

1. `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
2. Launch the app from the launcher.
3. Walk through every onboarding screen as a real user would.
4. Scan the QR shown by the Mac peer.
5. Wait for Ben's "ready" TTS line.
6. From a second terminal: `peer.ping({})` from the Mac.
7. Run both `adb shell am start -a android.intent.action.VIEW -d
   'jarvis://pair?dummy=1'` and the same with `'ben://pair?dummy=1'`.
   Both must launch the Ben app onto its pairing/onboarding handler;
   no "Activity not found" error.

**Expected end-to-end flow** (timing budgets are wall-clock from
each step's start):

1. App icon labelled "Ben" appears in the launcher within 5 s of
   install.
2. First-launch onboarding prompts in order: notification permission
   -> microphone -> camera -> battery-optimization exemption -> (if
   `Build.MANUFACTURER` is one of the killing OEMs) the OEM-kill
   walkthrough -> accessibility-service enable (deep-linked to
   `Settings.ACTION_ACCESSIBILITY_SETTINGS`) -> QR scan.
3. After the accessibility step, `adb shell settings get secure
   enabled_accessibility_services` includes
   `com.ben/.service.BenAccessibilityService`.
4. After QR scan, logcat shows `PeerClient: connected to <mac-ip>:<port>`
   within 3 s.
5. Within 5 s of pairing, logcat shows Node booting and `openclaw`
   loading; Ben speaks the "ready" line.
6. `peer.ping({})` from the Mac returns `{ok: true, rtt_ms: <50}`.
7. Both deep-link URLs resolve into the Ben app.
8. Walk every visible UI surface (launcher label, foreground-service
   notification, all onboarding screens, every Settings screen, every
   dialog). The literal string "Jarvis" appears nowhere user-visible.

**Pass criteria**: every numbered step is observably true within its
budget. Specifically: peer ping rtt < 50 ms on LAN, Node boot to
"ready" < 5 s, both deep-link schemes resolve, zero "Jarvis"
strings in any UI surface.

**Cleanup**: leave the device paired. Every subsequent test depends
on this state.

### T02 - Wake plus simple voice round-trip

**Covers tasks**: 5, 8, 10.

**Preconditions**: T01 passed within the last 24 h. Device unlocked,
screen on, foreground service running. Note the current `daily_cents`
from Settings -> Cost.

**Trigger**: say `"Ben, what time is it?"` once at conversational
volume from ~50 cm.

**Expected end-to-end flow**:

1. Wake-word fires within 300 ms of the word "Ben".
2. `BenVoiceService` opens the Realtime WSS within 200 ms of wake.
3. Audio capture starts at PCM16 24 kHz mono (Task 8 requirement).
4. Model returns first audio frame within 1.0 s of the question
   ending.
5. `AudioTrack` plays the answer at 24 kHz; the spoken time matches
   your phone's actual clock within +-1 minute.
6. Within 1 s of `response.done`, `CostLedger` increments the
   matching `REALTIME_*` kinds.
7. Settings -> Cost shows a positive delta vs. the value you noted.

**Pass criteria**: full round-trip from end-of-question to first
audible syllable < 1.5 s. Cost-ledger delta > 0 and matches the
per-`CallKind` prices in `CostPrices.kt` within +-10%.

**Cleanup**: none.

### T03 - Voice reconnection stress

**Covers tasks**: 8.

**Preconditions**: T02 passing.

**Trigger**: run this sequence, waiting ~5 s between queries for the
previous response to finish. Do not press any UI button between
queries; do not toggle the screen; do not speak any non-wake utterance
in between (so we isolate the wake -> realtime cycle):

1. "Ben, what time is it?"
2. "Ben, what's the date?"
3. "Ben, what day of the week?"
4. "Ben, how many minutes until the hour?"
5. "Ben, what's two plus two?"
6. "Ben, name a colour."
7. "Ben, name another colour."
8. "Ben, count to three."
9. "Ben, what time is it?"
10. "Ben, are you still there?"

**Expected end-to-end flow**: each of the 10 queries returns a
coherent audio answer. After the model's per-session timeout
(typically ~30 s of silence on the WSS) `BenVoiceService` should
**automatically** reopen the session for the next wake, with backoff,
and no manual intervention.

**Pass criteria**: 10 of 10 queries answered without any of:

- a manual app re-foreground,
- a missed wake,
- a "voice not available" TTS line,
- a logcat severity-E line from `BenVoiceService`.

**Cleanup**: none.

### T04 - WhatsApp message via voice

**Covers tasks**: 3, 5, 8, 9.

**Preconditions**: WhatsApp installed and signed in. Test contact
`Mom` saved in Contacts and visible in the WhatsApp chat list.
Accessibility service enabled. Foreground service running.

**Trigger**: say `"Ben, open WhatsApp and send Mom 'on my way'"`.

**Expected end-to-end flow**:

1. Wake fires (<= 300 ms).
2. Realtime model parses the intent and the planner calls the
   "open app + accessibility-driven UI action" tool.
3. Within 2 s, WhatsApp is foregrounded - confirm with
   `adb shell dumpsys window | grep mCurrentFocus`.
4. `BenAccessibilityService` walks the chat list, finds the row whose
   contact label matches "Mom" (fuzzy match acceptable for casing or
   nicknames), and taps it within 2 s of WhatsApp foregrounding.
5. The chat opens; the accessibility service identifies the message
   input by content-description / class, taps it, and types the
   literal `on my way` (no quotes, no extra punctuation).
6. The vision tool (using `OpenAiConfig.VISION_MODEL`) is invoked
   once to confirm the Send button location.
7. Send is tapped; the message appears in the chat as the most
   recent outgoing bubble within 1 s.
8. Ben speaks a confirmation, e.g. "Sent 'on my way' to Mom."

**Pass criteria**: end-to-end from wake to sent message < 12 s.
Message text matches exactly (no typos, no autocorrect drift). No
other WhatsApp chats opened or modified.

**Cleanup**: open the chat manually, long-press the test message,
delete-for-everyone (or delete-for-me if the contact already received
it).

### T05 - Amazon add-to-cart

**Covers tasks**: 3, 8, 9, 10.

**Preconditions**: Amazon app installed and signed in. Default address
set. Cart starts empty (open Amazon -> Cart -> confirm 0 items;
remove any leftover items from previous runs).

**Trigger**: say `"Ben, open Amazon, search iPhone 15 case, and add
the first result to my cart"`.

**Expected end-to-end flow**:

1. Wake fires.
2. Planner identifies a multi-step task: open Amazon -> tap search
   -> type query -> tap first organic result -> tap "Add to Cart".
3. Amazon foregrounded within 2 s.
4. Search field located via accessibility (preferred) or vision
   fallback, tapped, and the literal string `iPhone 15 case` typed.
5. The "Search" / magnifier IME action is invoked.
6. Results list loads. The vision tool identifies the first organic
   product tile - **not** a "Sponsored" ad slot above it. The
   planner must skip "Sponsored" labels.
7. First organic product tile tapped; product detail page loads.
8. Vision tool used again to locate the orange "Add to Cart" button.
9. Button tapped. Cart-counter badge transitions from 0 -> 1 within
   2 s.
10. Ben speaks "Added the first iPhone 15 case to your cart."
11. Cost ledger includes at least 2 `VISION_IMAGE` increments
    (steps 6 and 8).

**Pass criteria**: full task <= 25 s from wake. Cart count is exactly
1 (not 2 - proves no double-tap). The added item is genuinely an
iPhone 15 case (open the cart manually and visually confirm). Cost
delta on this scenario alone is > 0 and itemised on the cost screen.

**Cleanup**: open Amazon -> Cart -> remove the added item.

### T06 - Spotify play

**Covers tasks**: 3, 8, 9.

**Preconditions**: Spotify installed and signed in (free tier OK if
your account allows on-demand for the test track; otherwise use
premium). Audio output device available (phone speaker or paired
Bluetooth).

**Trigger**: say `"Ben, play Bohemian Rhapsody on Spotify"`.

**Expected end-to-end flow**:

1. Wake fires.
2. Spotify foregrounded <= 2 s.
3. Search tab tapped, query `Bohemian Rhapsody` typed.
4. Top "Songs" result tapped (vision distinguishes a Song row from
   an Artist / Album / Playlist row).
5. Playback starts - `adb shell dumpsys media_session | grep state`
   shows `state=PLAYING`, and the system "Now playing" notification
   appears in the shade.
6. Ben speaks "Playing Bohemian Rhapsody."

**Pass criteria**: from wake to first audible note <= 8 s. The track
playing is genuinely Bohemian Rhapsody (not a cover, not a parody -
listen for the opening "Is this the real life...").

**Cleanup**: pause playback (`adb shell input keyevent 85`).

### T07 - Read-screen vision

**Covers tasks**: 8, 9.

**Preconditions**: open Settings -> About phone manually before the
trigger, so vision has a definite, identifiable screen to describe.

**Trigger**: say `"Ben, what's on the screen?"`.

**Expected end-to-end flow**:

1. Wake fires.
2. Planner calls `vision.read_screen`.
3. The model id sent on the wire equals `OpenAiConfig.VISION_MODEL`
   exactly. Verify by reading `OpenAiConfig.kt` and matching the
   value seen in logcat.
4. Vision call returns within 3 s (model latency, not local
   bottleneck).
5. Ben describes the screen and at minimum mentions "About phone"
   (or "Settings" + "About") and one concrete field visible on that
   screen, e.g. Android version, device name, or model number.

**Pass criteria**: description is correct (not a hallucination),
vision-call latency <= 3 s, and the model id seen on the wire is
**not** `gpt-4o` or `gpt-4o-mini`. It must be the current latest GA
vision-capable model selected per Task 9.

**Cleanup**: none.

### T08 - Vision model fallback

**Covers tasks**: 9.

**Preconditions**: T07 passing. The debug-build-only override for
`OpenAiConfig.VISION_MODEL` exists (see Test fixtures). If not, file
it as a test-affordance gap and skip this test.

**Trigger**:

1. Use the debug switch to set `OpenAiConfig.VISION_MODEL` to the
   literal string `"not-a-real-model-2026"`.
2. Open Settings -> About phone again.
3. Say `"Ben, what's on the screen?"`.

**Expected end-to-end flow**:

1. First vision call goes out with `model=not-a-real-model-2026`.
2. OpenAI returns 404 / `model_not_found`. Logcat shows the error.
3. Fallback chain kicks in - per Task 9: `[primary, "gpt-4o",
   "gpt-4o-mini"]`. The retry uses `gpt-4o` and is logged as a
   fallback (not a fresh call).
4. The retry succeeds; Ben answers as in T07.

**Pass criteria**: at least one 404 logged, a fallback log line
follows, and the user-visible answer arrives within 6 s total
(slower than T07 because of the extra round-trip is acceptable).

**Cleanup**: reset the debug switch so `VISION_MODEL` is back to the
production constant. Re-run T07 to confirm production value is
restored.

### T09 - Cross-device delegate (LAN)

**Covers tasks**: 7.

**Preconditions**: both Mac and Android paired and on the same Wi-Fi
SSID. T01 ping passing.

**Trigger**: from the Mac (CLI / REPL / admin endpoint - whichever
the Mac peer exposes), invoke:

```
peer.delegate({task: "what's the battery level"})
```

Do **not** speak to the Android device. The trigger is purely from
the Mac side over the peer link.

**Expected end-to-end flow**:

1. Mac sends the JSON-RPC envelope over WSS within 50 ms of CLI
   invocation. Android's `PeerClient` logs the inbound delegate.
2. Android's planner runs the task locally - likely via
   `BatteryManager` or accessibility. No vision, no Realtime audio,
   no TTS. This is silent.
3. Response sent back over the same WSS. Mac receives a JSON-RPC
   result with a numeric or textual battery level.

**Pass criteria**:

- Round-trip <= 5 s end-to-end (per Task 7 success criterion).
- The reported level matches `adb shell dumpsys battery | grep
  level` within +-1%.
- HMAC verifies on both sides - no `protocol error: bad signature`
  on either log.

**Cleanup**: none.

### T10 - Cross-device delegate (Tailscale, off-LAN)

**Covers tasks**: 7.

**Preconditions**: T09 passing on LAN. Tailscale up and authenticated
on both Mac and Android; both visible in `tailscale status` from the
Mac. Android disconnected from Wi-Fi and on cellular only - confirm
with `adb shell dumpsys connectivity | grep ActiveNetwork`.

**Trigger**: same as T09 - `peer.delegate({task: "what's the battery
level"})` from the Mac.

**Expected end-to-end flow**: identical to T09, but the WSS connect
on the Android side resolves the Mac via Tailscale MagicDNS rather
than the LAN IP. The IPv4 form (100.x.x.x) is used, **not**
link-local IPv6 (per the Task 7 note about the `ws` library's IPv6
behaviour).

**Pass criteria**: round-trip <= 10 s (per Task 7 success criterion
for off-LAN). Same correctness checks as T09.

**Cleanup**: re-join the test Wi-Fi SSID.

### T11 - Reverse delegate (Android -> Mac)

**Covers tasks**: 7 (multi-device peer-mesh in both directions).

**Preconditions**: Mac peer running and accepting `peer.delegate`
inbound. Mac on the same LAN as Android, or both on Tailscale.

**Trigger**: say `"Ben, on my Mac open Safari and go to
news.ycombinator.com"`.

**Expected end-to-end flow**:

1. Wake fires; Realtime model parses the intent.
2. Planner recognises "on my Mac" as a delegation target and emits a
   `peer.delegate` over the WSS to the Mac peer.
3. Mac peer accepts, executes (Safari foregrounded on Mac, URL
   loaded), and returns success <= 5 s on LAN.
4. Android receives the result; Ben speaks "Done - opened Safari on
   your Mac."

**Pass criteria**: Safari is actually open on the Mac at
news.ycombinator.com within 8 s of the spoken trigger. No vision or
accessibility activity on the Android device beyond the normal
listening overlay - the heavy lifting happens on the Mac.

**Cleanup**: close the Safari tab on the Mac.

### T12 - Doze survival and wake-from-sleep

**Covers tasks**: 4, 5, 8.

**Preconditions**: Pixel device. T01 passed. Battery-optimisation
exemption granted for `com.ben` (verify with
`adb shell dumpsys deviceidle whitelist | grep com.ben`).

**Trigger**:

1. Press power to lock the device. Set it screen-down on a desk.
2. Note the time.
3. Wait 30 minutes without touching the device.
4. At t+30 min, with the device still locked and screen off, say
   `"Ben, what time is it?"` from ~50 cm.
5. Mid-test (around t+15 min) without disturbing the device, run
   `adb shell ps -A | grep com.ben` and `adb shell dumpsys activity
   services com.ben` to confirm the foreground service is still
   alive.

**Expected end-to-end flow**:

1. Mid-test ADB checks show `BenForegroundService` still running,
   `BenWakewordService` still listening, with non-zero recent CPU
   usage.
2. The wake word fires at t+30 min within 500 ms of speaking
   (slightly looser than T02's 300 ms because the radio may need to
   wake).
3. Voice round-trip completes; the answered time matches actual
   wall-clock at t+30 min within +-1 minute.

**Pass criteria**: no service restart logged in between (no
`BenForegroundService: onCreate` between t+0 and t+30 min). Wake
fires on the first attempt at t+30 min. Round-trip <= 2 s end-to-end
(allowing 500 ms slack for Doze unwind).

**Cleanup**: none.

### T13 - OEM kill walkthrough survival

**Covers tasks**: 4.

**Preconditions**: test device is one of {Xiaomi/MIUI, OnePlus/OxygenOS,
Realme, Samsung/OneUI}. T01 passed. Re-run T01 onboarding on this
specific device so you complete the manufacturer-specific walkthrough
(per Task 4: long-press app icon -> app info -> battery ->
"Unrestricted").

**Trigger**:

1. After the OEM walkthrough, open recents and swipe Ben away. (Yes,
   explicitly. We are testing that Ben survives the user removing it
   from recents, which is what kills it on these OEMs by default.)
2. Lock the device.
3. Wait 30 minutes.
4. Say `"Ben, what time is it?"`.

**Expected end-to-end flow**: identical to T12 - wake fires, voice
round-trip succeeds. Logcat from a pre-test `adb logcat -c` should
not contain a foreground-service restart in the 30-min window.

**Pass criteria**: same as T12 but on the named OEM device. Per Task
4's success criterion, >= 30 min on Xiaomi / OnePlus / Samsung is
the bar. A failure here on a tested OEM means the OEM walkthrough
copy needs more steps for that vendor - record the failing
manufacturer + OS version in `android/MIGRATION_TODO.md`.

**Cleanup**: none.

### T14 - Wake-word reliability

**Covers tasks**: 5.

**Preconditions**: quiet room (background noise < 40 dB if you have
a meter; otherwise: no music, no TV, no fan). Phone screen off,
locked, foreground service running. Stand 1-1.5 m from the device.

**Trigger**:

Phase A (true-positive). Say "Ben" 20 times across this
distribution:

- 5x quiet conversational tone,
- 5x normal conversational tone,
- 5x louder / projected,
- 5x mixed - half drawn-out ("Behhhn"), half clipped ("Ben!").

Wait ~3 s between attempts so each attempt enters its own
recogniser cycle (the `SpeechRecognizer` API ends each session after
silence - this also tests Task 5's auto-restart loop).

Phase B (false-positive). Leave the device alone and silent for 5
minutes immediately after Phase A. Do not speak, do not move
furniture, do not let the dog into the room.

Count from logcat: `BenWakeword: matched score=...` lines for both
phases.

**Pass criteria** (per Task 5 thresholds):

- Phase A: >= 18 of 20 wakes registered.
- Phase B: <= 1 false-positive wake in 5 minutes.
- The recogniser auto-restarts after each utterance: any
  `BenWakeword: stopped` line is followed within 1 s by a matching
  `started` line.

**Cleanup**: none.

### T15 - Cost ledger: increment and cap refusal

**Covers tasks**: 10.

**Preconditions**: cost screen exists in Settings (per Task 10).
Note current `daily_cents`, `monthly_cents`, and the per-`CallKind`
breakdown - write them down or screenshot the screen.

**Trigger**, Phase A (increment):

1. Run T02 five times back-to-back ("Ben, what time is it?" x5).
2. Run T07 twice ("Ben, what's on the screen?" x2 with Settings
   open).
3. Re-open Settings -> Cost.

**Trigger**, Phase B (cap refusal):

4. In Settings -> Cost, lower `daily_cap_usd` to `0.01`.
5. Open any app to give vision something to look at.
6. Say `"Ben, what's on the screen?"`.

**Expected end-to-end flow**:

Phase A:

- Each Realtime call (x5) increments `REALTIME_AUDIO_IN`,
  `REALTIME_AUDIO_OUT`, `REALTIME_TEXT_IN`, `REALTIME_TEXT_OUT` per
  the prices in `CostPrices.kt`. Logcat shows at least 5 x 4 = 20
  `CostLedger: +<n>` increments (or one aggregate per response,
  depending on implementation - both are acceptable as long as
  totals match).
- Each vision call (x2) increments `VISION_IMAGE` and
  `VISION_TEXT_IN` per the per-image rate. 2 x 2 = 4 increments.
- The total delta on `daily_cents` equals the sum of the per-line
  deltas within +-10% (rounding).

Phase B:

- Wake fires. Planner sees the call would push `daily_cents` over
  `daily_cap_usd * 100`. `CostLedger` logs a `refuse` line including
  the kind, estimated cents, current daily total, and the cap.
- No vision HTTP request goes out - verify in logcat: no outbound
  `vision.read_screen request` after the `refuse` line.
- Ben speaks **the literal line specified at line 407 of this
  brief**: "I've hit today's spending cap, $X.XX. Tap the cost
  screen to raise it." (with `$X.XX` filled in to the running daily
  total).

**Pass criteria**: Phase A deltas reconcile to +-10% of expected
prices; Phase B refusal happens before any paid HTTP/WS request, and
the spoken line matches the brief verbatim.

**Cleanup**: restore your original `daily_cap_usd` from the
preconditions note.

### Repo audit (covers Task 12)

Not a runtime test, but required by acceptance criterion #14:

```
ls -1 LICENSE NOTICE
head -1 LICENSE   # must contain "Apache License"
wc -l LICENSE     # Apache-2.0 verbatim is ~202 lines; anything
                  # wildly smaller is suspect (paraphrased / wrong)
cat NOTICE        # one line, the Ben copyright
```

**Pass criteria**: both files exist at the repo root, `LICENSE` is
the Apache-2.0 text verbatim from the apache.org canonical source
(per Task 12), `NOTICE` has the one-line Ben copyright.

---

## Out of scope for this pass (do not start, just note in a TODO file)

Add `android/FUTURE_WORK.md` listing each of these with a one-line
rationale:

- Replacing `nodejs-mobile` with a Rust core (per `ARCHITECTURE_AND_VISION.md` §7).
- Replacing `SpeechRecognizer` wake-word with openWakeWord / Porcupine.
- On-device VLM fallback (Gemma 3n / MediaPipe LLM) before cloud vision.
- Learned-flow replay (per `ARCHITECTURE_AND_VISION.md` §5.2).
- Replacing UDP-multicast wake arbiter with peer-link rendezvous.
- Coordinated `jarvis://` → `ben://` rename on Mac side.
- iOS support.
- Memory tree (per OpenHuman's `memory/tree/`).
- TokenJuice tool-output compaction.
- Battery-aware scheduler gate.

These are intentionally deferred. Do not touch them in this pass.

---

## Deliverables for this pass

1. `android/DIAGNOSIS.md` — one section per numbered task above, in the
   format specified.
2. `android/MIGRATION_TODO.md` — anything you found that needs the Mac
   side to also change (currently zero things, since Mac is locked).
3. `android/FUTURE_WORK.md` — the out-of-scope list above, plus any
   other items you discovered.
4. Code patches **only after** I approve the diagnosis.
5. `LICENSE` + `NOTICE` at the repo root (this one item you may do
   without further approval).

Start with task 1 and proceed numerically. Stop and report after the
diagnosis pass before writing any code.
