'use strict';
/**
 * Start a peer client + server pair on the phone, using credentials persisted
 * in BenSecrets (read via the Kotlin RPC bridge).
 *
 * Behaviour mirrors omniclaw/peer/daemon.py - we run BOTH a server (so the Mac
 * can call into us) and a client (so we can call into the Mac). Same secret on
 * both sides.
 */
const { PeerServer } = require('./server.js');
const { PeerClient } = require('./client.js');
const kotlin = require('../bridge/kotlin_rpc.js');

let _client = null;
let _server = null;

async function startPeerIfPaired({ workspace, role }) {
  let secrets;
  try {
    secrets = await kotlin.secrets.peer();
  } catch (e) {
    console.warn('[peer] kotlin secrets not available yet:', e && e.message);
    return;
  }
  if (!secrets || !secrets.secret_b64 || !secrets.host) {
    console.log('[peer] not paired - skipping peer client/server boot');
    return;
  }
  const ownDeviceId = secrets.own_device_id || ('android-' + Date.now());
  const secret = Buffer.from(secrets.secret_b64, 'base64url');

  // Server: handlers for the methods the Mac may call on us.
  const handlers = require('./handlers.js')({ workspace, role });
  _server = new PeerServer({
    deviceId: ownDeviceId,
    secret,
    handlers,
    host: '0.0.0.0',
    port: 18790,
  });
  try {
    await _server.start();
    console.log('[peer] server up on 0.0.0.0:' + _server.actualPort);
  } catch (e) {
    console.warn('[peer] server start failed:', e && e.message);
  }

  // Client: persistent connection to the Mac.
  const endpoint = 'ws://' + secrets.host + ':' + (secrets.port || 18790);
  _client = new PeerClient({ deviceId: ownDeviceId, secret, endpoint });
  try {
    await _client.connect();
    const pong = await _client.call('peer.ping', { ts_ms: Date.now() });
    console.log('[peer] connected to mac, ping rtt=', Date.now() - (pong.sent_ts_ms || Date.now()));
  } catch (e) {
    console.warn('[peer] client connect failed:', e && e.message);
  }
}

function repair() {
  // Re-run startup with whatever's now in BenSecrets.
  if (_client) try { _client.close(); } catch (_) {}
  if (_server) try { _server.stop(); } catch (_) {}
  _client = null; _server = null;
  startPeerIfPaired({ workspace: process.env.BEN_WORKSPACE, role: process.env.BEN_DEVICE_ROLE || 'android' })
    .catch((e) => console.warn('[peer] repair failed:', e && e.message));
}

function client() { return _client; }

module.exports = { startPeerIfPaired, repair, client };
