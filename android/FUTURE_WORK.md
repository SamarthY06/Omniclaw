# Android — FUTURE WORK

Items deliberately deferred. Each one is a real product gap surfaced
either by the brief's "Out of scope for this pass" list or by tracing
`USE_CASES.md` through the implementation during the diagnosis pass.
Don't lose them — track them as TODO issues; this file is the
single-source list.

Ordered roughly by user-visible value.

---

## 1. Standing-orders / autonomous loop (USE_CASES §11)

`USE_CASES.md` §11 lists five autonomous behaviors:
- Morning brief at 7am.
- Evening wrap at 9pm.
- 15-min-before-meeting heads-up.
- Continuous email triage.
- Bill watcher / birthday reminder / weekly summary / failed-task
  retry / stale-thing surfacing / location-based / battery-aware.

**What's missing**: A scheduler service. None of these fire today.

**Why deferred**: Requires a foreground `WorkManager`-style scheduler
*plus* a per-rule definition language *plus* a UI to manage them,
*plus* runs with no live wake. Different architecture from the current
"wake → session" model. Estimated 2-3 weeks of design + implementation.

---

## 2. Sensitivity-tier (S0/S1/S2/S3) enforcement layer

Spec is in `USE_CASES.md` §7-13. The implementation has zero hooks for
it. As of now, `device.place_call` (S1), `device.add_calendar_event`
(S1), `ui.click("Send")` (varies by app) all run with the same trust
level as `weather.current` (S0).

**What's needed**:
- A central `Sensitivity` enum mirroring `omniclaw/proto/types.py:16-20`
  on the Android side.
- Annotate every tool registration in `assets/node/src/openclaw/*` with
  its sensitivity.
- A pre-dispatch check in
  `assets/node/src/bridge/inbound_rpc.js:tools.invoke` that:
  - S0: execute, brief audible/visual confirmation.
  - S1: execute, post-completion notification.
  - S2: pause, voice-confirm with user, then execute.
  - S3: navigate to the screen, hand off, never auto-fill.
- Hard guard in `BenAccessibilityService.kt:typeText` that refuses
  any node where `isPassword=true` regardless of model intent
  (safety-critical, defense in depth).

**Why deferred**: This is a substantive UX design + implementation
that touches every tool. Out-of-scope for the bug-fix first pass but
**should be the very next product feature** after the Task 1-12
diagnosis-driven fixes land. Until then, the model's behavior on UPI /
OTP / Aadhaar screens is "best effort." See DIAGNOSIS.md "USE_CASES.md
reality check → UPI" for why this is dangerous.

---

## 3. Long-form UI flow narration

The current system prompt (`BenVoiceService.kt:474-479`) bans filler
phrases. That's correct for one-shot answers ("what's the weather"),
**wrong** for multi-step UI flows ("add to Amazon cart"). During a
15-second `device.launch_app → ui.read_screen → ui.click → ui.type →
ui.click → vision.locate_text → ui.click_at → ui.read_screen` chain,
the model sits silent and the user thinks Ben hung.

**What's needed**: Add a "narration rule" to the system prompt:
"During a multi-step tool chain, after every tool call that takes
>1.5 s, emit a single short progress line (e.g. 'searching now',
'adding to cart')." Plus runtime support: when a tool call exceeds
1.5 s, the bridge automatically emits a `response.create` with a
"thinking..." text fragment (no audio) so the model knows time has
passed.

**Why deferred**: Low-risk but couples the bridge to the Realtime
API more tightly. Worth doing once peer.delegate (Task 7) is fixed and
multi-step flows actually run.

---

## 4. Persistent durable memory storage

`memory.set` / `memory.get` / `memory.search` / `memory.user_facts` are
mentioned in the system prompt and have a backing module
(`assets/node/src/openclaw/memory_tools.js` — not read in this audit).
Whether the storage is genuinely durable across app restarts, app
updates, and clean reinstalls, and whether the search ranking is good,
is **untested in this audit**.

**What's needed**: A short reality-check pass on `memory_tools.js`:
- Is storage filesystem-based? Encrypted at rest?
- Backup behavior on app reinstall (intentional vs. accidental).
- Indexing strategy beyond fuzzy substring (the brief implies
  embedding-based but no evidence in the file tree).
- Dedup / overwrite semantics.

**Why deferred**: Not in the brief's 12 tasks. Should be a follow-up
audit pass.

---

## 5. Push-to-Talk (no wake-word) input modality

Currently the only entry point is wake-word. The user-facing
"HomeFragment" UI has a chat surface but no "tap to talk" button.

**What's needed**: A floating action button on `HomeFragment` that
calls `BenVoiceService.startService(ACTION_START_FROM_USER)`. Already
plumbed (line 736); just no UI.

**Why deferred**: Cosmetic; the wake-word path is the primary UX.

---

## 6. Background notification listener

For "Read me my unread WhatsApps from the family group, I'm cooking"
(USE_CASES §1) and similar push-style requests, we need a
`NotificationListenerService` to read notification text without
requiring the user to open WhatsApp.

**What's needed**: Add `BIND_NOTIFICATION_LISTENER_SERVICE` permission,
add a `BenNotificationListenerService` class, deep-link the user to
`Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS` in onboarding, and
expose a `device.read_unread_notifications(app?)` tool.

**Why deferred**: Adds another sensitive permission to the onboarding
funnel; needs a clear privacy story before adding. Brief doesn't
require it.

---

## 7. CallScreening / call-handling tools

USE_CASES §1 Calls includes "Decline this incoming call and text them
I'll call back in 10." Today we can `device.place_call` but can't
intercept incoming calls. Requires `CallScreeningService` + dialer
role grant.

**Why deferred**: Requires the user to set Ben as the default dialer
or call-screening service. High friction for a small feature.

---

## 8. Health & fitness integration (USE_CASES §7)

"Log that I walked 5km today", "What's my step count today?", "Tell me
my heart rate average for last week." Requires
`com.google.android.gms.fitness` (Health Connect) integration.

**Why deferred**: New SDK surface, not in scope for the bug-fix pass.
Add as a v1.0 feature.

---

## 9. Smart home (USE_CASES §8)

"Turn off the bedroom lights", "Set the AC to 24 degrees", etc.
Requires Matter / Google Home SDK integration.

**Why deferred**: Same as #8.

---

## 10. CallKit-style cross-device call handoff (USE_CASES §1)

"Call mom on my phone, I'm on my Mac" — requires the Mac side to be
able to instruct the Android side to place a call. This is just a
`peer.delegate` call once #1 in `MIGRATION_TODO.md` lands and the Mac
side has a `place_phone_call_via_android` tool. So this is unblocked
once Mac has `task.run`.

**Why deferred**: Blocked on MIGRATION_TODO #1.

---

## 11. Onboarding offline-language-pack auto-install

`OnboardingActivity.kt:69-71` deep-links to
`Settings.ACTION_VOICE_INPUT_SETTINGS` so the user can install the
en-US offline pack. The user has to know what to tap. A more polished
flow would either (a) detect the pack's presence and skip the step
when it's already there, or (b) launch
`SpeechRecognizer.maybeInitiateOnDevice...` (the API has a hidden
trigger for the system to download the pack on demand).

**Why deferred**: Polish; the existing flow works.

---

## 12. APK signing config / release pipeline

`bootstrap.sh` only does `assembleDebug`. There's no signing config in
`build.gradle.kts`. For real distribution we need a signing config
(release keystore in `local.properties`), a release build path, R8
proguard rules (see DIAGNOSIS Task 1), and a CI surface that produces
signed APKs.

**Why deferred**: Distribution is currently sideload-via-WhatsApp.
When we move to Play Store / direct CDN delivery, this is a hard
requirement.

---

## 13. Field telemetry / crash reporting

No `Crashlytics`, no `OkHttp` interceptor for OpenAI errors, no
structured logging beyond `Log.i` / `Log.w`. Once we have real users,
we need:

- `Crashlytics` (or self-hosted Sentry) for native + Kotlin crashes.
- Structured event log for OpenAI usage (tokens, latency, model used,
  refusal counts).
- A user-facing "send debug report" button in Settings.

**Why deferred**: Premature optimization while user count is single
digits. Add when we have >50 users.

---

## 14. Tablet / foldable layouts

All `res/layout/*.xml` is phone-portrait only. No
`layout-sw600dp/`, no foldable hinge handling. Brief is phone-only;
this is a v2 feature.

---

## 15. Localization

Strings are English-only (`strings.xml`). The system prompt is
English-locked. No other locales planned.

**Why deferred**: Brief is English-only by design.

---

## Summary

Items 1, 2, 3 are user-visible and should be the next product features
after the Task 1-12 diagnosis-driven fixes ship. Items 4-7 are
medium-term. 8-15 are long-term / explicit future scope.
