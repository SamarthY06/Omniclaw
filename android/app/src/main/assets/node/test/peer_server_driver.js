#!/usr/bin/env node
'use strict';
/**
 * Test driver: starts a PeerServer with a known shared secret on a random port
 * and prints "READY <port>" once it's listening. Used by
 * omniclaw/tests/test_ts_peer_interop.py to drive the JS server from Python.
 *
 * CLI: node peer_server_driver.js <secret_b64url> <device_id> [<port>]
 */
const path = require('path');
const { PeerServer } = require(path.join(__dirname, '..', 'src', 'peer', 'server.js'));

const secretB64 = process.argv[2];
const deviceId = process.argv[3] || 'js-test-server';
const port = parseInt(process.argv[4] || '0', 10);

if (!secretB64) {
  console.error('usage: peer_server_driver.js <secret_b64url> [device_id] [port]');
  process.exit(2);
}

const secret = Buffer.from(secretB64, 'base64url');

const handlers = {
  'peer.hello': async (params) => ({
    schema_version: 1,
    device_id: deviceId,
    role: 'android',
    caps: ['tool:test', 'tool:echo'],
    schema_min: 1,
    schema_max: 1,
  }),
  'peer.ping': async (params) => ({
    sent_ts_ms: params.ts_ms,
    recv_ts_ms: Date.now(),
    peer_ts_ms: Date.now(),
  }),
  'tools.invoke': async (params) => ({
    ok: true,
    output: { echo: params, source: 'js' },
  }),
  'task.run': async (params, ctx) => {
    ctx.emitEvent({ run_id: params.run_id, type: 'lifecycle', status: 'started' });
    ctx.emitEvent({ run_id: params.run_id, type: 'assistant', text_delta: 'hello from js', final: true });
    ctx.emitEvent({ run_id: params.run_id, type: 'lifecycle', status: 'completed' });
    return { run_id: params.run_id, status: 'completed', output: { ok: true } };
  },
  'memory.read': async () => ({ items: [] }),
  'memory.upsert': async (params) => ({ accepted: (params.items || []).length, rejected: [] }),
  'handoff.screen': async () => ({ acknowledged: true, user_action_started: false }),
};

(async () => {
  const server = new PeerServer({ deviceId, secret, handlers, host: '127.0.0.1', port });
  await server.start();
  // Print READY <port> on a single line so the parent can scrape it.
  console.log('READY ' + server.actualPort);

  process.on('SIGINT', async () => { await server.stop(); process.exit(0); });
  process.on('SIGTERM', async () => { await server.stop(); process.exit(0); });
})().catch((e) => { console.error('driver failed:', e && e.stack ? e.stack : e); process.exit(1); });
