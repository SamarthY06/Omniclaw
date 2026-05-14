# Android → Mac MIGRATION TODO

> Items here require **coordinated changes on the Mac side
> (`omniclaw/`)** that the Android first-pass diagnosis cannot fix
> alone. Owner: whoever maintains the Mac peer daemon.
>
> See `android/DIAGNOSIS.md` for the rationale on each. Order is from
> "blocks the most user value" to "nice to have".

---

## 1. Implement `task.run` handler on the Mac peer daemon

**Why**: Android's `peer.delegate` (the only cross-device handoff path,
exposed to the Realtime model in `BenVoiceService.kt:393-396`) is
currently calling `peer.run_task` on the Mac, which doesn't exist.
The diagnosis report (Task 7) recommends two options for the Android
side:

- (a) Tunnel via `tools.invoke({tool_name:'mac_delegate'})` for v0.
- (b) Rename to `task.run` once Mac exposes a handler.

Either way, the long-term right answer is **(b)**, because Mac's
`omniclaw/peer/server.py` already supports streamed
`task.event` envelopes and `omniclaw/proto/types.py:241-261` already
defines `TaskRunParams` / `TaskResult` / `LifecycleEvent` /
`AssistantEvent` / `ToolEvent`. The piece that's missing is purely the
**handler registration**.

**File to edit on the Mac side**:
`omniclaw/peer/daemon.py:124-129`. Change the `_make_handlers` return
to:

```python
def _make_handlers(self) -> dict[str, Any]:
    return {
        "peer.hello": self._on_hello,
        "peer.ping": self._on_ping,
        "tools.invoke": self._on_tools_invoke,
        "task.run": self._on_task_run,   # NEW
    }
```

And add `_on_task_run(self, params, ctx)` that:

1. Validates `params` against `TaskRunParams` (already in
   `proto/types.py`).
2. Hands off to whatever the Mac-side OpenClaw agent loop is named
   (likely `omniclaw.openclaw.run_intent(intent, args, deadline_ms)`
   — needs verification by the Mac maintainer).
3. Streams `LifecycleEvent` (`status='started'` → `'thinking'` →
   `'tool_call'` → `'completed'`/`'failed'`) and `AssistantEvent`
   (text deltas) and `ToolEvent` envelopes via `ctx.emit_event(...)`
   as the agent runs.
4. Returns a final `TaskResult` with `status, output, error,
   tokens_in, tokens_out, cost_usd`.

The `tokens_in/tokens_out/cost_usd` fields of `TaskResult` should be
populated and propagated back to Android — Android's CostLedger (once
implemented; see DIAGNOSIS Task 10) needs them to attribute spend
correctly to `CallKind.PEER_DELEGATE`.

**Acceptance**:
- `peer_cli.py task.run "open Notes"` from a Mac terminal works
  end-to-end.
- An Android voice request "what's on my calendar today, on my Mac"
  returns the actual answer, not `peer_call_failed:unknown_method`.

**Estimated complexity**: Medium. The plumbing (server.py supports
streamed handlers, types are defined) is done. The unknown is the
agent-loop API surface on the Mac side.

---

## 2. Mirror the Android-side schema/HMAC drift guard

**Why**: Android's `assets/node/src/peer/types.js:9-11` declares
`SCHEMA_VERSION=1, SCHEMA_MIN=1, SCHEMA_MAX=1`. Mac
`omniclaw/proto/types.py:11-13` is the same. If either side bumps
without coordinating, signature verification still passes (because
schema_version is in `signed_dict`) but field semantics diverge.

**Action**: When Mac next ships a schema change, both sides must bump
together. Add a CI step that diff-checks
`assets/node/src/peer/types.js` against
`omniclaw/proto/types.py` for `SCHEMA_VERSION`, `SCHEMA_MIN`,
`SCHEMA_MAX` agreement.

**Estimated complexity**: Trivial.

---

## 3. mDNS service type — coordinated rename

**Why**: Both sides currently use the unqualified service name
`'jarvis'`. The Bonjour/DNS-SD spec requires the form
`_<service>._<proto>.local.` (e.g. `_ben-peer._tcp.local.`). The
Android-side discovery module silently no-ops without `bonjour-service`
installed, so this isn't a blocker today, but if discovery is ever
turned on it'll fail spec validation on stricter mDNS implementations.

**Action**: Coordinate a rename on both sides:
- `assets/node/src/peer/discovery.js:14`:
  `SERVICE_TYPE = '_ben-peer._tcp.local.'`.
- `omniclaw/peer/discovery.py`: same constant.
- For one release, `browseOnce` should accept either the old
  unqualified name or the new fully-qualified one for backward compat.

**Estimated complexity**: Small.

---

## 4. Pairing URI scheme — `ben://` co-existence with `jarvis://`

**Why**: Diagnosis Task 11. The Android side will add a `ben://pair`
deep-link alias in this repo's first patch round. For the user-facing
QR / shareable link to actually be a `ben://` URL (not `jarvis://`),
the Mac side has to *generate* the new scheme.

**File to edit on the Mac side**:
- `omniclaw/peer/pair.py:54`: change `URI_SCHEME = "jarvis://pair"`
  to a constant pair `(EMIT_URI_SCHEME = "ben://pair", LEGACY_URI_SCHEMES =
  ("jarvis://pair",))`.
- `payload_to_uri` emits `EMIT_URI_SCHEME`.
- `payload_from_uri` accepts either.

**Estimated complexity**: Trivial.

---

## 5. `~/.jarvis/` filesystem locations

**Why**: Mac `omniclaw/peer/daemon.py` references
`~/.jarvis/peer.sock`, `~/.jarvis/peer.log`, and `omniclaw/peer/pair.py`
references `PEER_DIR = ~/.jarvis/peer/`. These live on the Mac so
Android can't change them, but they're part of the same rebrand.

**Action**: At the next Mac point release, add a one-time symlink
migration (`~/.ben/` → `~/.jarvis/`) and switch the constants. Keep
the old paths as fallback for one major version.

**Estimated complexity**: Small (path changes + one-time migration).

---

## 6. Cost-ledger token-count propagation

**Why**: Diagnosis Task 10 specs an Android-side `CostLedger` that
attributes spend per `CallKind`. `CallKind.PEER_DELEGATE` should
attribute the cost of the *Mac-side* agent run (which spends OpenAI
tokens too) back to the Android user who initiated the request.

**Action**: When item #1 (`task.run` handler on Mac) lands, the final
`TaskResult` envelope must include `tokens_in / tokens_out / cost_usd`
as defined in `omniclaw/proto/types.py:138-141`. Android reads these
fields and rolls them into `CostLedger.record(CallKind.PEER_DELEGATE,
units=cost_usd_in_micros)`.

**Estimated complexity**: Small (already part of the `TaskResult`
schema; just needs to be populated by `_on_task_run`).

---

## 7. Mac handler for `handoff.screen` from Android

**Why**: USE_CASES.md §13 ("OTP", "Payment info entry", "Aadhaar /
SSN / passport numbers") explicitly require *handoff to the user*,
not assistant action. Android's `assets/node/src/peer/handlers.js:71`
exposes a `handoff.screen` stub on the phone side. There is no
equivalent on the Mac side, so a Mac-initiated cross-device run that
hits a sensitive screen on the phone can't trigger a "user, please
look at your phone" handoff.

This is a longer-term plumbing item, partly enabled by item #1.

**Action**: When `task.run` lands and the Mac-side agent reaches a
sensitive Android screen via `peer.delegate`-back-from-Mac, it should
emit a `handoff.screen` to the *Android* side, which would already
work because Android handles it (line 71). The Mac side's
agent loop needs to know to *emit* this when it sees an OTP/PIN
screen.

**Estimated complexity**: Medium. Requires sensitivity-tier
classification in the Mac agent loop.

---

## Summary

The single highest-impact item is **#1**. It unblocks every
cross-device use case in `USE_CASES.md`. Items 2-7 are quality / polish
that can land independently after #1.
