'use strict';
/**
 * Query helpers for the JSONL session store.
 *
 * Backs both Android HistoryActivity (via reading the same files directly) and
 * the embedded history_cli.js / Mac-side history.py. Pure read - mutations all
 * go through SessionStore in store.js.
 */
const fs = require('fs');
const path = require('path');
const readline = require('readline');

function listIndex(workspaceRoot, { limit = 50, since = null } = {}) {
  const indexFile = path.join(workspaceRoot, 'sessions', 'index.jsonl');
  if (!fs.existsSync(indexFile)) return [];
  const lines = fs.readFileSync(indexFile, 'utf8').split(/\r?\n/).filter(Boolean);
  const parsed = [];
  for (const l of lines) {
    try {
      const o = JSON.parse(l);
      if (since && Date.parse(o.started_at) < Date.parse(since)) continue;
      parsed.push(o);
    } catch (_) {}
  }
  parsed.sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
  return parsed.slice(0, limit);
}

function getSession(workspaceRoot, sessionId) {
  // Walk the index for the file path.
  const idx = listIndex(workspaceRoot, { limit: 10_000 });
  const entry = idx.find((e) => e.id === sessionId);
  if (!entry) return null;
  const file = path.join(workspaceRoot, entry.path);
  if (!fs.existsSync(file)) return null;
  const events = fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map((l) => {
    try { return JSON.parse(l); } catch (_) { return null; }
  }).filter(Boolean);
  return { entry, events };
}

function searchSessions(workspaceRoot, query, { limit = 20 } = {}) {
  const q = (query || '').toLowerCase();
  const idx = listIndex(workspaceRoot, { limit: 1000 });
  const out = [];
  for (const e of idx) {
    if ((e.first_user_line || '').toLowerCase().includes(q)) {
      out.push(e);
    } else {
      const file = path.join(workspaceRoot, e.path);
      if (!fs.existsSync(file)) continue;
      const text = fs.readFileSync(file, 'utf8');
      if (text.toLowerCase().includes(q)) out.push(e);
    }
    if (out.length >= limit) break;
  }
  return out;
}

module.exports = { listIndex, getSession, searchSessions };
