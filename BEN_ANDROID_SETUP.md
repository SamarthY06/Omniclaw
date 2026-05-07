# Ben on Android — setup, build, sideload

This guide walks you from a clean Mac with the repo checked out to:

1. A signed-debug `Ben.apk` on your `~/Desktop`,
2. The APK installed on your phone,
3. The phone paired with your Mac peer,
4. End-to-end voice-driven WhatsApp + Teams flows working.

It is the user-facing companion to `omniclaw/INSTALL.md` (Mac side) and the
plan in `.cursor/plans/ben-android-symmetric-mesh_*.plan.md` (architecture).

---

## 0. What you need

* macOS 13+ with Xcode CLT (`xcode-select --install`).
* Java 21+ (the build's target):
  ```bash
  brew install openjdk@21
  echo 'export JAVA_HOME=/opt/homebrew/opt/openjdk@21' >> ~/.zshrc
  ```
* Node 22+ (for the embedded Node runtime build step):
  ```bash
  brew install node
  ```
* An Android phone running Android 12 (API 31) or higher. USB debugging on.

You do NOT need Android Studio. The bootstrap script downloads its own
Gradle and the Android command-line tools.

---

## 1. Build the APK

```bash
cd /path/to/Personal\ Assistant/android
./scripts/bootstrap.sh
```

What it does, in order:

1. Downloads Gradle 8.10.2 into `android/.gradle-toolchain/`.
2. Generates `gradlew` if missing.
3. If `ANDROID_HOME` isn't set, looks in the standard locations and falls
   back to `brew install --cask android-commandlinetools`. Sets
   `ANDROID_HOME` automatically.
4. Accepts SDK licenses + installs `platforms;android-35`,
   `build-tools;35.0.0`, `ndk;26.1.10909125`, and `cmake;3.22.1`.
5. Downloads `nodejs-mobile-v18.20.4-android.zip` (Node 18 runtime for
   Android) and lays out `app/libnode/bin/<abi>/libnode.so` plus
   `app/libnode/include/node/`.
6. Runs `npm install --omit=dev` inside `app/src/main/assets/node` so the
   embedded runtime has `ws` (and `openclaw` if available on the registry).
7. Runs `./gradlew assembleDebug` which builds:
   - our small JNI shim `libbennode.so` (from `src/main/cpp/native-lib.cpp`)
     against the bundled libnode headers, for arm64-v8a, armeabi-v7a, x86_64.
   - the Kotlin app, packaging both `libnode.so` and `libbennode.so`.
8. Copies the APK to two places:
   - `~/Desktop/Ben.apk` — stable filename, always points at the most recent
     build. This is the one to drag into WhatsApp / Drive when sharing.
   - `android/dist/Ben-<versionName>-<gitShortSHA>[-dirty]-<YYYYMMDD-HHMM>.apk`
     — versioned archive that survives subsequent builds. Append a row to
     `android/dist/CHANGELOG.md` describing what changed. The `.apk` files
     in `dist/` are gitignored (each is ~85 MB); only the changelog is
     tracked.

Expected duration on first run: ~10–15 min (mostly SDK + npm download).
Re-runs are <30 s thanks to Gradle's incremental build.

If you want a release build later (signed with your own keystore):

```bash
./gradlew assembleRelease
```

---

## 2. Sideload onto the phone

You have three install paths. Pick whichever you can actually use; they all
end at the same APK on the phone.

### 2A. WiFi + QR code (no cable, recommended)

Both the Mac and the phone need to be on the same WiFi network.

```bash
.venv/bin/python android/scripts/serve_apk.py
```

This starts a tiny HTTP server on the Mac and prints a scannable QR code in
the terminal. On the phone:

1. Open the camera (or any QR scanner) and scan the QR.
2. Tap the URL. The browser will download `Ben.apk`.
3. Open the downloaded file from the notification or the Files app and tap
   "Install".
4. Once the install finishes, come back to the Mac terminal and press
   `Ctrl+C` to stop the server.

If the phone says "connection refused", your Mac firewall is blocking
inbound traffic on the chosen port. `System Settings -> Network -> Firewall`,
turn it off for the install, re-run the script.

If the install dialog silently closes (Play Protect on Android 14 / Samsung
One UI Auto Blocker), see "Sideload on Android 14 / One UI" below.

### 2B. Google Drive / cloud download (no cable, no LAN)

1. Upload `~/Desktop/Ben.apk` to Drive (or Dropbox / iCloud / your own S3).
2. On the phone, open Drive (NOT a WhatsApp message), download the file.
3. Open the download from Files -> Install.

Same Auto Blocker / Play Protect notes apply.

### 2C. USB + adb (cable, for repeat installs)

The cleanest path if you have a data-capable USB-C cable, since adb bypasses
both Play Protect and Samsung Auto Blocker.

1. Enable Developer Options: tap "Build number" 7 times in `Settings >
   About phone`.
2. `Settings > System > Developer options`: enable USB debugging.
3. Plug the phone in; on the prompt allow USB debugging from this Mac.
4. ```bash
   export PATH="${ANDROID_HOME:-/opt/homebrew/share/android-commandlinetools}/platform-tools:$PATH"
   adb uninstall com.ben || true
   adb install -r ~/Desktop/Ben.apk
   ```

### Sideload on Android 14 / Samsung One UI

If the install dialog silently dismisses with no error, OR you see a "Google
Play Protect: App blocked to protect your device" dialog with no "Install
anyway" button:

1. **Samsung Auto Blocker**: `Settings -> Security and privacy -> Auto Blocker`
   -> turn the master switch OFF.
2. **Google Play Protect**: open Play Store -> tap profile picture ->
   `Play Protect` -> gear icon -> turn off "Scan apps with Play Protect".
3. **Install unknown apps**: `Settings -> Apps -> Special access ->
   Install unknown apps` -> select the source app you're using (your browser
   for path 2A, Drive for path 2B) -> "Allow from this source".
4. Re-download the APK with a fresh filename (Drive caches the previous
   blocked download).
5. Re-enable Auto Blocker and Play Protect after the install completes;
   they will not retro-uninstall Ben.

After install, when you go to enable Ben's Accessibility Service, Android 14
may grey out the toggle with "Restricted setting". Fix:
`Settings -> Apps -> Ben -> three-dot menu (top right) -> "Allow restricted
settings"`. One-time per app.

---

### Onboarding

Open the Ben app. Onboarding has 3 steps:

* **Permissions**: Tap "Grant Accessibility" -> enable Ben's accessibility
  service (this is what lets the agent click and read other apps). Tap
  "Battery exemption" -> grant ignore-battery-optimizations so the wake
  listener stays running 24/7. Tap "Check offline language pack" -> if
  SpeechRecognizer says "no offline support", install Google's offline
  language pack from `Settings > General management > Voice Input`.
* **API key & wake phrase**: Paste your OpenAI API key, leave wake phrase
  as "Ben" (or change it).
* **Pair**: Tap "Scan QR" and point the camera at the QR you generate on
  the Mac (next step).

The always-on services (wake listener, embedded Node, accessibility bridge)
do NOT start until you finish step 3. This is intentional - it stops the
runaway-wake-on-ambient-noise loop that bit us pre-2026-05-07.

---

## 3. Pair phone <-> Mac

On the Mac (one-time):

```bash
.venv/bin/python omniclaw/tools/peer_cli.py pair show --qr
```

This prints a `jarvis://pair?...` URL plus a QR code in the terminal. The
phone's QR scanner consumes it, persists the secret in
EncryptedSharedPreferences, and the embedded Node opens both a peer client
to your Mac AND a peer server for the Mac to call into.

Verify pairing succeeded:

```bash
# From Mac:
.venv/bin/python omniclaw/tools/peer_cli.py ping
# -> {"ok": true, "rtt_ms": 23, ...}
```

```bash
# From phone (via adb):
adb shell "echo '{\"id\":1,\"method\":\"peer.ping\",\"params\":{\"ts_ms\":0}}' \
  | nc 127.0.0.1 18792"
# -> JSON-RPC 2.0 response with {"ok": true}
```

---

## 4. Mac wake word ("Ben") via launchd

```bash
sed -i '' \
  -e "s|__REPO_PARENT__|$(pwd)|g" \
  -e "s|__PYTHON_BIN__|$(.venv/bin/python -c 'import sys; print(sys.executable)')|g" \
  -e "s|__HOME__|$HOME|g" \
  omniclaw/launchd/ai.ben.wakeword.plist

cp omniclaw/launchd/ai.ben.wakeword.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/ai.ben.wakeword.plist
```

First run pops permission dialogs:

* Microphone (the python3 binary).
* Speech Recognition (one-time consent).

The listener uses `SFSpeechRecognizer(requiresOnDeviceRecognition=True)` so
audio NEVER leaves the Mac until the wake phrase fires. After that, the
existing `start_voice.sh` in `omniclaw/voice/` opens the OpenClaw realtime
entry. Logs at `~/.jarvis/wakeword.log`.

To pause/resume:

```bash
launchctl unload ~/Library/LaunchAgents/ai.ben.wakeword.plist  # pause
launchctl load   ~/Library/LaunchAgents/ai.ben.wakeword.plist  # resume
```

---

## 5. Acceptance tests

Both run from the repo root with `./.venv/bin/python -m pytest`:

* `omniclaw/tests/test_acceptance_phone_solo.py` — runs only when `adb` sees
  a device with `com.ben` installed. It injects the prompt
  *"Ben, send Pragati Biradar on WhatsApp: on my way"*, waits for the
  agent's session to end, pulls the JSONL trace, then independently OCR's an
  `adb screencap` to verify the literal *"on my way"* text actually
  appeared in the WhatsApp conversation.
* `omniclaw/tests/test_acceptance_cross_device.py` — same idea but with
  *"Ben, ask my Mac for the last 5 messages on Teams"*. Asserts the phone
  routed through `peer_cli.task.run`, asserts NO browser tool was used, and
  asserts the assistant returned a structured list of ~5 entries.

Both tests skip cleanly if either side isn't ready, so you can run the full
suite from any machine without breaking CI.

---

## 6. Troubleshooting

* **APK build fails with "could not find libnode.so"** — re-run
  `./scripts/fetch-nodejs-mobile.sh` manually. It expects the per-ABI files
  at `app/libnode/bin/{arm64-v8a,armeabi-v7a,x86_64}/libnode.so` and the
  headers at `app/libnode/include/node/node.h`.
* **`installation failed: INSTALL_FAILED_USER_RESTRICTED`** — your phone is
  blocking sideload. `Settings > Apps > Special access > Install unknown
  apps` and allow your file manager / `adb`.
* **Wake word never fires** — on a real device, take three steps:
  1. Open the app's first-run "Mic Test" (Home tab) and confirm the live
     transcription view shows your voice.
  2. If the live view stays blank, the offline language pack isn't
     installed; the onboarding step 1 button takes you straight there.
  3. If it shows transcripts but doesn't fire, your wake phrase is too
     fuzzy; pick a word the recognizer doesn't confuse with common words
     (e.g. "Hey Ben" rather than just "Ben").
* **Mac wake word never fires** — `tail -f ~/.jarvis/wakeword.log`. If you
  see `on-device recognition not supported`, you need the offline language
  pack: System Settings -> Accessibility -> Spoken Content.
* **Peer ping returns `not_paired`** — the phone hasn't completed the QR
  step, or the secret got rotated. Re-run `peer_cli.py pair show --qr` on
  Mac and re-scan.
* **Browser keeps appearing in agent traces** — that's a regression. Open
  an issue with the trace; `AGENTS.md` says browser is forbidden.

---

## 7. What ships in the APK

| Component | Location | Purpose |
| --- | --- | --- |
| Embedded Node 18 (libnode.so) | jniLibs from `app/libnode/bin/<abi>/` | runs the OpenClaw gateway, peer client/server, JSON-RPC bridge |
| JNI shim (libbennode.so) | built from `src/main/cpp/native-lib.cpp` via CMake | bridges Kotlin -> `node::Start(argc, argv)` |
| Embedded JS payload | `assets/node/` (index.js + src/ + workspace_bootstrap/) | OpenClaw launcher, peer code, RPC bridge |
| AccessibilityService | `BenAccessibilityService.kt` | tree, click, type, swipe, screenshot |
| MediaProjection | `BenScreencapService.kt` | full-screen PNG screenshots |
| ML Kit OCR | `AndroidOcr.kt` | on-device text recognition (free) |
| Wake word | `BenWakewordService.kt` | always-on `SpeechRecognizer`, fuzzy phrase match |
| Voice loop | `BenVoiceService.kt` | OpenAI Realtime WSS for STT/TTS/LLM |
| Peer protocol | `assets/node/src/peer/*.js` | wire-compatible JS port of Python peer |
| Session store | `assets/node/src/session/*.js` | append-only JSONL transcript store |
| Onboarding / Settings / History | Kotlin UIs under `com/ben/*` | end-user flows |

Total APK size: ~85 MB. The bulk is the bundled libnode.so (Node 18 + V8 +
ICU) shipped for all three ABIs.

---

## 8. Permission matrix (v0.1.2)

Ben groups permissions into three classes:

* **Manifest-only** — declared in `AndroidManifest.xml`, granted at install time.
  No user prompt.
* **Runtime** — Android prompts the first time a tool needs it. Ben uses a
  transparent `PermissionGateActivity` so the prompt does NOT block the
  conversation; the model receives `permission_not_granted` and asks the
  user to allow + retry.
* **Special access** — granted from system Settings, not via a dialog. Ben
  walks the user through these in onboarding step 1.

| Permission | Class | Why Ben needs it | Granted in |
| --- | --- | --- | --- |
| `RECORD_AUDIO` | runtime | wake word listener + Realtime conversation | onboarding step 1 |
| `INTERNET` | manifest | OpenAI Realtime WSS, peer mesh | install |
| `FOREGROUND_SERVICE` | manifest | host service for always-on wake word | install |
| `FOREGROUND_SERVICE_MICROPHONE` | manifest | microphone foreground anchor | install |
| `POST_NOTIFICATIONS` | runtime | the single "Ben is listening" notification | first launch |
| `WAKE_LOCK` | manifest | keeps embedded Node alive across screen-off | install |
| `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` | special | stops Doze killing the wake listener | onboarding step 1 |
| **AccessibilityService** | special | reads UI tree, performs taps, types text | onboarding step 1 (`Settings -> Accessibility -> Ben`) |
| **MediaProjection** | runtime modal | one-shot screenshot consent for screen capture | first time `ui.screenshot` runs |
| `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` | runtime | `device.get_location` tool | first invocation, via PermissionGateActivity |
| `READ_CONTACTS` | runtime | `device.get_contacts` tool | first invocation |
| `CALL_PHONE` | runtime | `device.place_call` tool | first invocation |
| `CAMERA` | runtime | QR scanner during pairing only | onboarding step 3 |
| no clipboard permission needed | n/a | `device.clipboard_get` / `clipboard_set` | always allowed |
| no battery permission needed | n/a | `device.battery_status` | always allowed |

What Ben deliberately does NOT request: SMS, call log, microphone background
access (microphone is foreground only via the always-on FGS), storage,
notification listener.

---

## 9. Tool surface the agent has access to

This is what the OpenAI Realtime model sees when it picks tools. Mirrors the
Mac side feature-for-feature; the model is taught the standard on-phone flow
(launch -> read tree -> click -> type) in its system prompt.

| Tool | Sensitivity | Purpose |
| --- | --- | --- |
| `peer.delegate(task)` | S0 | Hand a natural-language task off to the paired Mac (full vision-driven OpenClaw). |
| `device.get_location()` | S1 | Last-known GPS / network fix. |
| `device.get_contacts(query?)` | S1 | Search the address book. |
| `device.place_call(number\|name)` | S1 | Dial via `Intent.ACTION_CALL`. |
| `device.launch_app(package\|label)` | S0 | Resolve label -> package -> launch. |
| `device.clipboard_get` / `clipboard_set` | S0 | Read / write clipboard. |
| `device.battery_status` | S0 | Battery percentage, charging state, source. |
| `ui.focus_app(package)` | S0 | Foreground an app. |
| `ui.read_screen()` | S0 | Dump current AccessibilityNode tree (≤200 nodes). |
| `ui.click({text\|ax_id})` | S1 | Tap by visible text or accessibility id. |
| `ui.click_at(x, y)` | S1 | Tap pixel coords (for Compose / WebView). |
| `ui.type(text)` | S1 | Type into the focused input. |
| `ui.scroll(direction)` | S0 | Page-style vertical scroll. |
| `ui.swipe(x1,y1, x2,y2)` | S0 | Free-form gesture. |
| `ui.screenshot()` | S0 | Capture screen via MediaProjection. |
| `ui.screen_size()` | S0 | Pixel dimensions. |
| `vision.locate_text(target)` | S0 | On-device ML Kit OCR, returns click coords. |
| `vision.read_screen(question)` | S2 | Sends screenshot + question to gpt-4o (image leaves device). |

Sensitivity:

* **S0** — purely on-device, no privacy implications.
* **S1** — touches user data or performs an action on the device.
* **S2** — image / data leaves the device for an external API call.

Verified end-to-end with `assets/node/test/automation_simulation.test.js`,
which drives the full "open WhatsApp -> find Pragati -> type 'Hi' -> Send"
flow against mocked AccessibilityService + ML Kit handlers, plus an
Electron-style "AX tree empty -> vision.locate_text fallback" scenario
proving the Mac-equivalent vision path.
