#!/usr/bin/env node
'use strict';
/**
 * Test driver: connects to a PeerServer (running anywhere - typically a Python
 * one started by pytest), runs a fixed RPC sequence, and prints a single-line
 * JSON result on stdout. Used by test_ts_peer_interop.py to validate the
 * JS->Python direction.
 *
 * CLI: node peer_client_driver.js <secret_b64url> <endpoint> <device_id>
 */
const path = require('path');
const { PeerClient } = require(path.join(__dirname, '..', 'src', 'peer', 'client.js'));

const secretB64 = process.argv[2];
const endpoint = process.argv[3];
const deviceId = process.argv[4] || 'js-test-client';
if (!secretB64 || !endpoint) {
  console.error('usage: peer_client_driver.js <secret_b64url> <endpoint> [device_id]');
  process.exit(2);
}
const secret = Buffer.from(secretB64, 'base64url');

(async () => {
  const c = new PeerClient({ deviceId, secret, endpoint });
  await c.connect();
  const ping = await c.call('peer.ping', { ts_ms: Date.now() });
  const hello = await c.call('peer.hello', {
    schema_version: 1, device_id: deviceId, role: 'android', caps: ['t'],
  });
  const inv = await c.call('tools.invoke', {
    tool_name: 'echo', args: { hi: 1 }, deadline_ms: 5000,
  });
  const events = [];
  let result = null;
  try {
    const stream = c.stream('task.run', {
      run_id: 'r1', intent: 'noop', args: {},
      allow_remote_tools: false, deadline_ms: 5000,
    });
    const drainP = (async () => { for await (const ev of stream.events) events.push(ev); })();
    result = await stream.result;
    await drainP;
  } catch (e) {
    console.error('[client] task.run failed:', e && e.message);
  }
  await c.close();
  console.log(JSON.stringify({ ping, hello, inv, events, result }));
})().catch((e) => {
  console.error('[client] driver failed:', e && e.stack ? e.stack : e);
  process.exit(1);
});
