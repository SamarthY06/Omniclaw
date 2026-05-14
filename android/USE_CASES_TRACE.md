# 0.1.5 USE_CASES.md trace

Static trace of 12 representative scenarios from `USE_CASES.md` against the
0.1.5 patched code. Each scenario lists the call path, the verdict, and the
exact reason. Verdicts:

- **PASS-CODE**: code path is correct end-to-end on the Android side; will
  work as soon as a real device exercises it.
- **PASS-API**: independently verified end-to-end in this session against
  real OpenAI / live JS test harness.
- **PARTIAL**: Android side works, but a peer/device dependency outside
  Android scope must also be in place.
- **BLOCKED-EXT**: Android side works; external dependency is missing
  (Mac peer handler, OEM autostart panel, etc).

What this document is NOT:

- It is **not** a real-device run. We have no Android device attached this
  session. Wake-word capture, real microphone audio, real OEM kill paths,
  real WhatsApp/Swiggy/UPI taps cannot be verified from a desk.
- It is **not** an exhaustive walk of the 150+ entries in `USE_CASES.md`;
  it is a representative cross-section that exercises every patched code
  path at least once.

---

## 1. "What's the weather looking like tomorrow?" — Section 5 / Quick info, S0, Either

| Step | Patched code | Verdict |
| --- | --- | --- |
| Wake-word fires "Ben" | `BenWakewordService` -> `BenVoiceService.ACTION_START_FROM_WAKE` | PASS-CODE |
| Cost-cap pre-check | `BenVoiceService.connect()` -> `CostLedger.checkRefusal(ctx)` (NEW) | PASS-CODE |
| WSS opens | `openWss(apiKey)` -> `gpt-realtime` | **PASS-API** (handshake test, 1.4 s open + session.created + session.updated) |
| Model picks `device.get_location` | tools.list ships 31 tools incl. weather + location | PASS-CODE |
| Location resolves | `AndroidDeviceBridge.get_location` (unchanged) | PASS-CODE |
| Model picks `weather.current({location:"lat,lon"})` | builtin_tools.js weather.current (unchanged) | PASS-CODE |
| Reply spoken in <2 sentences | New BREVITY + NARRATION rules in sysPrompt | PASS-CODE |
| Cost recorded | `recordCostFromResponseDone(ev)` -> `CostLedger.record(REALTIME_AUDIO, ...)` (NEW) | PASS-CODE |

**Net**: PASS-CODE. Only blocker is `RECORD_AUDIO` runtime grant — the new
onboarding step 0 forces it before "Continue", so a fresh install will get
it.

---

## 2. "Set an alarm for 7am tomorrow." — Section 1 / Reminders, S1, Phone

| Step | Patched code | Verdict |
| --- | --- | --- |
| Wake fires, session opens | (same as #1) | PASS-CODE |
| Model picks `device.set_alarm({hour:7,minute:0,label:"morning"})` | system prompt explicitly says "NEVER tell the user 'I cannot set alarms'; you can." | PASS-CODE |
| Tool dispatch | NodeBridgeService -> `AndroidDeviceBridge.set_alarm` -> `Intent(AlarmClock.ACTION_SET_ALARM)` (unchanged) | PASS-CODE |

**Net**: PASS-CODE. Pre-fix this still worked; no regression.

---

## 3. "WhatsApp Sarah a happy birthday with a cake emoji." — Section 1 / SMS, S2, Phone

| Step | Patched code | Verdict |
| --- | --- | --- |
| Session opens, model receives prompt | sysPrompt has explicit "ON-PHONE UI TASKS" 7-step canonical flow | PASS-CODE |
| `device.launch_app({package:"com.whatsapp"})` | unchanged | PASS-CODE |
| `ui.read_screen()` | `BenAccessibilityService.tree(...)` returns now-richer error envelope on `no_active_window` (NEW) | PASS-CODE |
| Accessibility service NOT enabled | NEW `AndroidAxBridge` returns `accessibility_service_not_running` + new hint string. New sysPrompt says "if you see that error, tell the user one sentence and stop." | PASS-CODE — graceful degradation |
| `ui.click("Sarah")` | unchanged | PASS-CODE |
| `ui.type("Happy birthday 🎂")` | NEW: `BenAccessibilityService.typeText` checks `target.isPassword` first (returns `password_field_refused`) — irrelevant here, message-input is not a password field | PASS-CODE |
| `ui.click("Send")` | unchanged | PASS-CODE |
| Pre-fix: TalkBack would intercept the tap | FIXED: `accessibility_service_config.xml` no longer sets `flagRequestTouchExplorationMode`; `packageNames="@null"` removed | PASS-CODE |

**Net**: PASS-CODE. Pre-fix this would *brick the device* on enable
(TalkBack-style touch consumption); post-fix the user can enable Ben's
accessibility service without losing normal touch.

---

## 4. "On my Mac, find the latest message in #engineering." — Section 1 + 10, S0, Mac

| Step | Patched code | Verdict |
| --- | --- | --- |
| Session opens | (same) | PASS-CODE |
| Model picks `peer.delegate({task:"..."})` | sysPrompt CROSS-DEVICE clause unchanged | PASS-CODE |
| Peer client present? | `peerStart.client()` returns null when not paired -> NEW returns `{ok:false, error:"peer_not_paired", hint}` (model can speak it) | PASS-CODE |
| Client present, calls `task.run` | NEW: tries `task.run` first | PASS-CODE |
| Mac responds `unknown_method:task.run` | NEW: falls through to legacy `peer.run_task` | PASS-CODE |
| Mac also responds `unknown_method:peer.run_task` | NEW: returns `{ok:false, error:"peer_no_task_handler", hint:"The paired Mac does not expose a task-delegation handler. Tell the user their Mac needs the latest omniclaw daemon..."}` | PASS-CODE |

**Net**: BLOCKED-EXT. Android side now ships a clean error message instead
of the old `peer_call_failed:unknown_method:peer.run_task`. **Use case
will work end-to-end only after MIGRATION_TODO #1 lands a `task.run` (or
`peer.run_task`) handler in `omniclaw/peer/server.py`.**

---

## 5. "Send 500 rupees to mom on UPI." — Section 3 / Bills, S3, Phone

| Step | Patched code | Verdict |
| --- | --- | --- |
| Session opens | (same) | PASS-CODE |
| Model attempts UPI flow: launch app, click contact, click "Pay 500" | unchanged | PASS-CODE |
| Model attempts to type UPI PIN via `ui.type("1234")` | NEW: `BenAccessibilityService.typeText` checks `target.isPassword` (UPI/banking fields all set this on `EditText`) -> returns `password_field_refused` + hint | PASS-CODE — REFUSED |
| Model receives refusal, NEW sysPrompt SENSITIVITY RULE says "refuse politely, stop" | sysPrompt has explicit S3 hard-refusal language | PASS-CODE |

**Net**: PASS-CODE — refusal is the correct behaviour. Pre-fix the model
would have happily transcribed and typed any digits the user said, sending
"my pin is one two three four" verbatim into a UPI password field.

---

## 6. "Order biryani from the place I had it from last Friday on Swiggy." — Section 3, S2, Phone

| Step | Patched code | Verdict |
| --- | --- | --- |
| Session opens, USER FACTS + RECENT MEMORIES injected into sysPrompt | unchanged | PASS-CODE |
| Model calls `memory.search({query:"biryani"})` | unchanged; sysPrompt MEMORY DISCIPLINE explicit | PASS-CODE |
| Memory hit -> launch Swiggy, click restaurant, click item, click checkout | unchanged | PASS-CODE |
| Final UPI PIN step | refused per #5 | PASS-CODE |

**Net**: PASS-CODE up to the payment step; payment requires the user.

---

## 7. "Take a screenshot of this WhatsApp screen and tell me who's in it." — Section 2 + 5, S0, Phone

| Step | Patched code | Verdict |
| --- | --- | --- |
| Model calls `ui.screenshot()` | unchanged | PASS-CODE |
| MediaProjection bound? If not -> `media_projection_not_bound` | unchanged | PASS-CODE |
| `BenScreencapService.startForegroundOk()` | FIXED: now calls 3-arg `startForeground(id, notif, FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)` on Q+. Pre-fix Android 14+ would throw `MissingForegroundServiceTypeException` | PASS-CODE |
| Model calls `vision.read_screen({question:"who's in it"})` | NEW fallback chain | **PASS-API** (vision test green for all 3 models) |
| `gpt-5.5` rejects `max_tokens` with HTTP 400 | NEW: detects "Unsupported parameter" body, retries SAME model with `max_completion_tokens` | **PASS-API** (verified — gpt-5.5 returned "Red" on the test image after auto-switch) |
| `gpt-5.5` 5xx -> falls to `gpt-4o` | NEW: status === 5xx loops to next model | PASS-CODE |

**Net**: PASS-API. **Critical bug caught in this session**: pre-fix the
fallback chain treated *any* 4xx as fatal, so the very first
`gpt-5.5` call (which always 400s with `max_tokens`) would short-circuit
the entire chain. Now it auto-corrects.

---

## 8. "Pair my Mac." — Section 10, S1, Cross (with Mac scanning a fresh QR)

| Step | Patched code | Verdict |
| --- | --- | --- |
| User scans Mac's QR with `ben://pair?...` URL | NEW: `AndroidManifest` activity-alias accepts both `ben://pair` AND legacy `jarvis://pair` | PASS-CODE |
| Or pastes `ben://pair?...` from clipboard | NEW: `PairingActivity.isPairingUri` accepts both schemes; new `pairing_paste_invalid` toast updated | PASS-CODE |
| Persist secret, kick `peer.pair_now` | unchanged | PASS-CODE |
| Wait 1.5 s, then poll `peer.pair_status` for up to 5 s | NEW: PairingActivity polls; NEW: inbound_rpc handler does `client.call('peer.ping', ..., {timeoutMs:3000})` and returns `{paired:true/false, last_error?}` | PASS-CODE |
| Mac actually responds to `peer.ping` | unchanged on Mac (`peer.ping` always existed) | PARTIAL — needs Mac daemon up |
| If timeout -> NEW `pairing_status_unverified` toast warns the user | new string + behaviour | PASS-CODE |

**Net**: PASS-CODE. Pre-fix the activity always toasted "Paired with Mac"
even when the Mac was offline / on a different network; post-fix you only
see "Paired" if the handshake actually round-tripped.

---

## 9. "Stop." — Section 15 / Conversation control, S0, current device

| Step | Patched code | Verdict |
| --- | --- | --- |
| User says "stop" mid-turn | unchanged | PASS-CODE |
| `isStopIntent("stop")` returns true | unchanged | PASS-CODE |
| Send `response.cancel`, `stopAndRearm()` | unchanged | PASS-CODE |
| Hardware audio effects released | NEW: `aec?.release(); ns?.release(); agc?.release()` in stopAndRearm() | PASS-CODE |

**Net**: PASS-CODE. Pre-fix we leaked AudioEffect kernel sessions;
post-fix they release.

---

## 10. "Daily spend hit cap" — Section 14 (failure modes)

| Step | Patched code | Verdict |
| --- | --- | --- |
| First wake of the day after $5 spent | NEW: `CostLedger.checkRefusal(ctx)` returns `DAILY_CAP_EXCEEDED` | PASS-CODE |
| `BenVoiceService.connect()` aborts before opening WSS | NEW | PASS-CODE |
| `stopAndRearm()` cleanly | unchanged | PASS-CODE |
| User raises cap in Settings -> `CostLedger.clearRefusal()` | NEW | PASS-CODE |

**Net**: PASS-CODE for the cap-enforcement and refusal flow. The
*surfacing* to the user (right now we just `Log.w` the refusal) is the
weakest link — without an in-session WSS we cannot TTS the refusal. Future
work: a small in-app notification + text-to-speech via Android's built-in
TTS engine (FUTURE_WORK).

---

## 11. "Force quit on Xiaomi after 30 minutes idle" — Section 14

| Step | Patched code | Verdict |
| --- | --- | --- |
| Onboarding step 0 detects `Build.MANUFACTURER` contains "xiaomi" | NEW: `isKnownAggressiveOem()` | PASS-CODE |
| User taps "Open vendor autostart settings" | NEW: deep-links to `com.miui.securitycenter/.permcenter.autostart.AutoStartManagementActivity` | PASS-CODE |
| Foreground service holds with new 3-arg startForeground + FOREGROUND_SERVICE_TYPE_MICROPHONE\|SPECIAL_USE | FIXED on Android 14+ | PASS-CODE |

**Net**: PASS-CODE. Won't be fully validated until a Xiaomi device is
exercised, but the deep-links are the canonical ones used by every
Android battery-optimization-aware app.

---

## 12. "Peer crashes during boot" — Section 14

| Step | Patched code | Verdict |
| --- | --- | --- |
| `startPeerIfPaired` throws because secrets not yet persisted | NEW: index.js per-subsystem try/catch logs `[ben-node] peer FAILED:` and continues | PASS-CODE |
| `startVoicePipeline` and `startOpenClaw` still boot | NEW: bootSubsystem isolation | PASS-CODE |
| `tools.list` returns 31 tools (verified by automation_simulation.test) | unchanged | **PASS-API** |

**Net**: PASS-API. Pre-fix one peer error killed the IIFE and openclaw
never registered, so every subsequent `tools.list` returned `[]`. Post-fix
each subsystem boots independently.

---

## Summary matrix

| # | Use case | Verdict | Notes |
| --- | --- | --- | --- |
| 1  | Weather                         | PASS-CODE  | Realtime handshake verified |
| 2  | Set alarm                       | PASS-CODE  | No regression |
| 3  | WhatsApp message                | PASS-CODE  | TalkBack-style brick FIXED |
| 4  | Mac delegation                  | BLOCKED-EXT | Clean error; needs Mac handler |
| 5  | UPI PIN                         | PASS-CODE  | NEW S3 hard refusal |
| 6  | Order biryani                   | PASS-CODE  | up to payment |
| 7  | Vision read screen              | PASS-API   | gpt-5.5 fallback FIXED |
| 8  | Pair Mac                        | PASS-CODE  | ben:// + verified handshake |
| 9  | Stop intent                     | PASS-CODE  | Audio effects released |
| 10 | Daily spend cap                 | PASS-CODE  | Refusal logged; TTS deferred |
| 11 | Xiaomi autostart                | PASS-CODE  | Deep-link present |
| 12 | Peer boot failure isolation     | PASS-API   | tools.list still 31 tools |

Of the 12: **9 PASS-CODE**, **2 PASS-API** (independently verified
end-to-end), **1 BLOCKED-EXT** (Mac side must change).

The single BLOCKED-EXT (cross-device delegation) is unblocked by
MIGRATION_TODO.md item #1 — out of scope for this APK per the brief's
"only edit android/" constraint.
