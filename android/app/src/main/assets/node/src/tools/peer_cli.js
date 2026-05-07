#!/usr/bin/env node
'use strict';
/**
 * peer_cli.js - thin wrapper around the embedded peer client.
 *
 * Mirrors omniclaw/tools/peer_cli.py:
 *   peer_cli.js status
 *   peer_cli.js verify
 *   peer_cli.js caps
 *   peer_cli.js tools.invoke <method> --args '{...}'
 *   peer_cli.js task.run <id> --args '{...}'
 *
 * Used by the embedded agent and by automated tests to confirm the peer link
 * is healthy.
 */
const peer = require('../peer/start.js');
const kotlin = require('../bridge/kotlin_rpc.js');
const { PeerClient } = require('../peer/client.js');

main().catch((e) => emit({ ok: false, error: String(e && e.message ? e.message : e) }));

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const sub = args._[0];
  switch (sub) {
    case 'status': return await cmdStatus();
    case 'verify':
    case 'caps':
    case 'tools.invoke':
    case 'task.run':
      return await cmdRpc(sub, args);
    default: return emit({ ok: false, error: 'unknown_subcommand:' + sub });
  }
}

async function cmdStatus() {
  const c = peer.client();
  emit({ ok: true, paired: !!c, endpoint: c ? c.endpoint : null });
}

async function cmdRpc(sub, args) {
  let c = peer.client();
  if (!c) {
    // Spin up an ephemeral one
    const secrets = await kotlin.secrets.peer();
    if (!secrets || !secrets.host) return emit({ ok: false, error: 'not_paired' });
    const secret = Buffer.from(secrets.secret_b64, 'base64url');
    c = new PeerClient({
      deviceId: secrets.own_device_id || 'android-cli',
      secret,
      endpoint: 'ws://' + secrets.host + ':' + (secrets.port || 18790),
    });
    await c.connect();
  }
  let method;
  let params = {};
  if (args.args) try { params = JSON.parse(args.args); } catch (_) { params = {}; }
  if (sub === 'verify') method = 'peer.ping';
  else if (sub === 'caps') method = 'peer.hello';
  else if (sub === 'tools.invoke') method = 'tools.invoke';
  else if (sub === 'task.run') method = 'task.run';
  if (sub === 'verify') params = { ts_ms: Date.now() };
  if (sub === 'caps') params = { device_id: 'android-cli', role: 'android', caps: [] };
  const result = await c.call(method, params);
  emit({ ok: true, method, result });
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
