'use strict';
/**
 * Append-only JSONL session store, identical wire format to Mac-side
 * omniclaw/tools/history.py.
 *
 *   <workspace>/sessions/YYYY/MM/DD/sess_<ulid>.jsonl   per-session transcript
 *   <workspace>/sessions/index.jsonl                    one row per ENDED session
 *   <workspace>/sessions/_live/<sessionId>.json         live preview, updated
 *                                                       per-turn (debounced 500ms),
 *                                                       deleted on session end.
 *
 * The live preview is the v0.1.2 incremental-history fix. Previously the
 * History UI only saw a session after it ended (the index.jsonl row), so
 * mid-session turns were invisible. The History reader can list both
 * index.jsonl AND _live/*.json to show in-progress conversations.
 *
 * Privacy default: audio NOT stored. The settings toggle in Android maps to
 * the BEN_STORE_AUDIO env which we read on each append; if false we never
 * call appendAudio.
 */
const fs = require('fs');
const path = require('path');

const _stores = new Map();

const LIVE_SNAPSHOT_DEBOUNCE_MS = 500;

class SessionStore {
  constructor(workspaceRoot) {
    this.workspaceRoot = workspaceRoot;
    // sessionId -> { path, startedAtMs, firstUserLine, toolsUsed, turns,
    //                liveTimer, lastAssistantLine, livePath }
    this.activeSessions = new Map();
  }

  start(sessionId, device, wakeWord = 'Ben') {
    const startedAt = Date.now();
    const d = new Date(startedAt);
    const ymd = d.getUTCFullYear() + '/' + pad(d.getUTCMonth() + 1) + '/' + pad(d.getUTCDate());
    const dir = path.join(this.workspaceRoot, 'sessions', ymd);
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, 'sess_' + sessionId + '.jsonl');
    const liveDir = path.join(this.workspaceRoot, 'sessions', '_live');
    fs.mkdirSync(liveDir, { recursive: true });
    const livePath = path.join(liveDir, sessionId + '.json');
    const session = {
      path: file,
      livePath,
      startedAtMs: startedAt,
      device,
      wakeWord,
      firstUserLine: '',
      lastAssistantLine: '',
      toolsUsed: new Set(),
      turns: 0,
      liveTimer: null,
    };
    this.activeSessions.set(sessionId, session);
    appendLine(file, { type: 'session.started', ts: new Date(startedAt).toISOString(), device, wake_word: wakeWord, session_id: sessionId });
    // Snapshot synchronously on start so the UI immediately sees an entry.
    this._writeLiveSnapshot(sessionId, session);
  }

  appendUserText(sessionId, text) {
    const s = this.activeSessions.get(sessionId); if (!s) return;
    if (!s.firstUserLine) s.firstUserLine = (text || '').slice(0, 240);
    s.turns++;
    appendLine(s.path, { type: 'user.text', ts: new Date().toISOString(), text });
    this._scheduleLiveSnapshot(sessionId);
  }

  appendAssistantText(sessionId, text) {
    const s = this.activeSessions.get(sessionId); if (!s) return;
    s.lastAssistantLine = (text || '').slice(0, 240);
    s.turns++;
    appendLine(s.path, { type: 'assistant.text', ts: new Date().toISOString(), text });
    this._scheduleLiveSnapshot(sessionId);
  }

  appendToolCall(sessionId, name, subcommand, args) {
    const s = this.activeSessions.get(sessionId); if (!s) return;
    s.toolsUsed.add(name);
    appendLine(s.path, { type: 'tool.call', ts: new Date().toISOString(), name, subcommand, args });
    this._scheduleLiveSnapshot(sessionId);
  }

  appendToolResult(sessionId, name, ok, summary) {
    const s = this.activeSessions.get(sessionId); if (!s) return;
    appendLine(s.path, { type: 'tool.result', ts: new Date().toISOString(), name, ok, summary });
    this._scheduleLiveSnapshot(sessionId);
  }

  end(sessionId, reason) {
    const s = this.activeSessions.get(sessionId);
    if (!s) return;
    const endedAt = Date.now();
    appendLine(s.path, {
      type: 'session.ended', ts: new Date(endedAt).toISOString(),
      reason, duration_ms: endedAt - s.startedAtMs, session_id: sessionId,
    });
    const indexFile = path.join(this.workspaceRoot, 'sessions', 'index.jsonl');
    appendLine(indexFile, {
      id: sessionId,
      started_at: new Date(s.startedAtMs).toISOString(),
      ended_at: new Date(endedAt).toISOString(),
      device: s.device,
      first_user_line: s.firstUserLine,
      tools_used: Array.from(s.toolsUsed),
      path: path.relative(this.workspaceRoot, s.path),
      duration_ms: endedAt - s.startedAtMs,
    });
    if (s.liveTimer) clearTimeout(s.liveTimer);
    try { fs.unlinkSync(s.livePath); } catch (_) { /* fine, already gone */ }
    this.activeSessions.delete(sessionId);
  }

  _scheduleLiveSnapshot(sessionId) {
    const s = this.activeSessions.get(sessionId); if (!s) return;
    if (s.liveTimer) return; // already scheduled within the debounce window
    s.liveTimer = setTimeout(() => {
      s.liveTimer = null;
      this._writeLiveSnapshot(sessionId, s);
    }, LIVE_SNAPSHOT_DEBOUNCE_MS);
  }

  _writeLiveSnapshot(sessionId, s) {
    const snapshot = {
      id: sessionId,
      live: true,
      started_at: new Date(s.startedAtMs).toISOString(),
      device: s.device,
      wake_word: s.wakeWord,
      turns: s.turns,
      first_user_line: s.firstUserLine,
      last_assistant_line: s.lastAssistantLine,
      tools_used: Array.from(s.toolsUsed),
      path: path.relative(this.workspaceRoot, s.path),
      updated_at: new Date().toISOString(),
    };
    try {
      // Atomic write via tmp + rename so a crashed mid-write never leaves a
      // truncated JSON visible to the History reader.
      const tmp = s.livePath + '.tmp';
      fs.writeFileSync(tmp, JSON.stringify(snapshot, null, 0), 'utf8');
      fs.renameSync(tmp, s.livePath);
    } catch (e) {
      // Non-fatal: live preview is a UX nicety, never block the session on it.
      console.warn('[session.store] live snapshot write failed:', e && e.message);
    }
  }
}

function appendLine(file, obj) {
  fs.appendFileSync(file, JSON.stringify(obj) + '\n', 'utf8');
}
function pad(n) { return n < 10 ? '0' + n : '' + n; }

function sessionStore(workspaceRoot) {
  if (!_stores.has(workspaceRoot)) _stores.set(workspaceRoot, new SessionStore(workspaceRoot));
  return _stores.get(workspaceRoot);
}

module.exports = { sessionStore, SessionStore, LIVE_SNAPSHOT_DEBOUNCE_MS };
