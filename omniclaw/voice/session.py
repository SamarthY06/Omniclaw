"""Shared session lifecycle + JSONL store for the Mac side of Ben.

Mirror of android/app/src/main/assets/node/src/session/{lifecycle,store}.js.
Keep these in sync: the History tab on the phone reads JSONL files written
by EITHER device (after a peer transfer) so the schema must match.

Public surface:

    SessionTimer(cutoff_ms=180_000, on_timeout=...)
        .start()            -> begin counting down
        .stop()             -> halt; on_timeout is NOT called
        .reset()            -> reset countdown without firing
        .mark_activity(why) -> reset countdown; reasons let us debug
                               who's keeping the session warm
                               (vad / audio_delta / tool / user_text / ...)

    SessionStore(workspace_root)
        .start(session_id, device, wake_word="Ben")
        .append_user_text(session_id, text)
        .append_assistant_text(session_id, text)
        .append_tool_call(session_id, name, subcommand, args)
        .append_tool_result(session_id, name, ok, summary)
        .end(session_id, reason="silence_180s")

JSONL layout:

    <workspace>/sessions/YYYY/MM/DD/sess_<id>.jsonl     <- per-session events
    <workspace>/sessions/index.jsonl                    <- one line per ended session

Schemas exactly mirror the Android side.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SILENCE_CUTOFF_MS = 180_000
CHIME_MS = 200


# ---------------------------------------------------------------------------
# SessionTimer
# ---------------------------------------------------------------------------

class SessionTimer:
    """Inactivity timer with `mark_activity` reset semantics.

    Threading: methods are safe to call from any thread. The on_timeout callback
    runs from a daemon timer thread, so do whatever locking you need yourself.
    """

    def __init__(
        self,
        cutoff_ms: int = SILENCE_CUTOFF_MS,
        on_timeout: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.cutoff_ms = cutoff_ms
        self.on_timeout = on_timeout or (lambda _ev: None)
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._last_activity_ms = 0
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._last_activity_ms = _now_ms()
            self._reschedule_locked()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def reset(self) -> None:
        with self._lock:
            self._last_activity_ms = _now_ms()

    def mark_activity(self, reason: str) -> None:
        with self._lock:
            if not self._running:
                self._running = True
            self._last_activity_ms = _now_ms()
            self._reschedule_locked()

    def _reschedule_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.cutoff_ms / 1000.0, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if not self._running:
                return
            idle_ms = _now_ms() - self._last_activity_ms
            if idle_ms < self.cutoff_ms:
                self._reschedule_locked()
                return
            self._running = False
            self._timer = None
        try:
            self.on_timeout({"idle_ms": idle_ms, "reason": "silence_cutoff"})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------

@dataclass
class _ActiveSession:
    path: Path
    started_at_ms: int
    device: str
    first_user_line: str = ""
    tools_used: set[str] = field(default_factory=set)


class SessionStore:
    """Append-only JSONL persistence. One process per workspace at a time.

    There is no locking across processes. If you want truly concurrent
    writers you'd need a file lock; in practice the wake-word LaunchAgent
    is the only writer and it serializes per-session.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root)
        self._sessions: dict[str, _ActiveSession] = {}
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------

    def start(self, session_id: str, device: str, wake_word: str = "Ben") -> None:
        started_at_ms = _now_ms()
        d = datetime.fromtimestamp(started_at_ms / 1000.0, tz=timezone.utc)
        ymd = f"{d.year}/{d.month:02d}/{d.day:02d}"
        directory = self.workspace_root / "sessions" / ymd
        directory.mkdir(parents=True, exist_ok=True)
        file = directory / f"sess_{session_id}.jsonl"
        with self._lock:
            self._sessions[session_id] = _ActiveSession(
                path=file, started_at_ms=started_at_ms, device=device,
            )
        _append_line(file, {
            "type": "session.started",
            "ts": _iso(started_at_ms),
            "device": device,
            "wake_word": wake_word,
            "session_id": session_id,
        })

    def append_user_text(self, session_id: str, text: str) -> None:
        s = self._get(session_id)
        if s is None:
            return
        if not s.first_user_line and text:
            s.first_user_line = text[:240]
        _append_line(s.path, {
            "type": "user.text", "ts": _iso(_now_ms()), "text": text,
        })

    def append_assistant_text(self, session_id: str, text: str) -> None:
        s = self._get(session_id)
        if s is None:
            return
        _append_line(s.path, {
            "type": "assistant.text", "ts": _iso(_now_ms()), "text": text,
        })

    def append_tool_call(
        self, session_id: str, name: str, subcommand: str, args: dict[str, Any],
    ) -> None:
        s = self._get(session_id)
        if s is None:
            return
        s.tools_used.add(name)
        _append_line(s.path, {
            "type": "tool.call", "ts": _iso(_now_ms()),
            "name": name, "subcommand": subcommand, "args": args,
        })

    def append_tool_result(
        self, session_id: str, name: str, ok: bool, summary: str,
    ) -> None:
        s = self._get(session_id)
        if s is None:
            return
        _append_line(s.path, {
            "type": "tool.result", "ts": _iso(_now_ms()),
            "name": name, "ok": ok, "summary": summary,
        })

    def end(self, session_id: str, reason: str = "silence_180s") -> None:
        with self._lock:
            s = self._sessions.pop(session_id, None)
        if s is None:
            return
        ended_at_ms = _now_ms()
        _append_line(s.path, {
            "type": "session.ended", "ts": _iso(ended_at_ms),
            "reason": reason, "duration_ms": ended_at_ms - s.started_at_ms,
            "session_id": session_id,
        })
        index_file = self.workspace_root / "sessions" / "index.jsonl"
        index_file.parent.mkdir(parents=True, exist_ok=True)
        _append_line(index_file, {
            "id": session_id,
            "started_at": _iso(s.started_at_ms),
            "ended_at": _iso(ended_at_ms),
            "device": s.device,
            "first_user_line": s.first_user_line,
            "tools_used": sorted(s.tools_used),
            "path": str(s.path.relative_to(self.workspace_root)),
            "duration_ms": ended_at_ms - s.started_at_ms,
        })

    # -- internal -----------------------------------------------------------

    def _get(self, session_id: str) -> _ActiveSession | None:
        with self._lock:
            return self._sessions.get(session_id)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _append_line(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
