#!/usr/bin/env node
'use strict';
/**
 * history_cli - browse session JSONL store.
 *   history_cli.js list [--limit 50] [--since 2026-05-01T00:00:00Z]
 *   history_cli.js show <session_id>
 *   history_cli.js search <query> [--limit 20]
 *
 * Same JSON output shape as omniclaw/tools/history.py so a future Mac native
 * UI can hit either side identically.
 */
const path = require('path');
const { listIndex, getSession, searchSessions } = require('../session/query.js');

const WORKSPACE = process.env.BEN_WORKSPACE
  || path.join(process.env.HOME || '/data/user/0/com.ben/files', 'openclaw', 'workspace');

const args = parseArgs(process.argv.slice(2));
const sub = args._[0];

try {
  switch (sub) {
    case 'list': {
      const out = listIndex(args.workspace || WORKSPACE, {
        limit: parseInt(args.limit || '50', 10),
        since: args.since || null,
      });
      emit({ ok: true, sessions: out });
      break;
    }
    case 'show': {
      const id = args._[1];
      const result = getSession(args.workspace || WORKSPACE, id);
      if (!result) emit({ ok: false, error: 'not_found' });
      else emit({ ok: true, entry: result.entry, events: result.events });
      break;
    }
    case 'search': {
      const q = args._[1] || '';
      const out = searchSessions(args.workspace || WORKSPACE, q, {
        limit: parseInt(args.limit || '20', 10),
      });
      emit({ ok: true, results: out });
      break;
    }
    default: emit({ ok: false, error: 'unknown_subcommand:' + sub });
  }
} catch (e) {
  emit({ ok: false, error: String(e && e.message ? e.message : e) });
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) { const k = a.slice(2); const n = argv[i + 1]; if (n !== undefined && !n.startsWith('--')) { out[k] = n; i++; } else out[k] = true; }
    else out._.push(a);
  }
  return out;
}
function emit(o) { process.stdout.write(JSON.stringify(o) + '\n'); }
