'use strict';
/**
 * Peer WS server. Direct port of omniclaw/peer/server.py.
 *
 * Per-connection loop:
 *   1. Read JSON envelopes off the WS,
 *   2. Verify HMAC + replay window,
 *   3. Dispatch to handlers[method] (async fn),
 *   4. Send back a "res" envelope (or "task.event" envelopes for streamed runs).
 *
 * Uses the `ws` npm package, which is bundled into assets/node/node_modules
 * by scripts/fetch-nodejs-mobile.sh -> npm install --omit=dev.
 */
const http = require('http');
const { signEnvelope, verifyEnvelope } = require('./crypto.js');
const { newEnvelope, SCHEMA_VERSION } = require('./types.js');

class PeerServer {
  /**
   * @param {object} opts
   * @param {string} opts.deviceId
   * @param {Buffer} opts.secret               // raw bytes
   * @param {Object<string, AsyncFunction>} opts.handlers  // method -> async (params, ctx) => result
   * @param {string} [opts.host]               // default 0.0.0.0
   * @param {number} [opts.port]               // default 18790
   * @param {number} [opts.maxSkewMs]          // default 60_000
   */
  constructor(opts) {
    this.deviceId = opts.deviceId;
    this.secret = opts.secret;
    this.handlers = opts.handlers;
    this.host = opts.host || '0.0.0.0';
    this.port = opts.port || 18790;
    this.maxSkewMs = opts.maxSkewMs || 60_000;
    this._wss = null;
    this._http = null;
    this._actualPort = null;
  }

  async start() {
    const { WebSocketServer } = require('ws');
    return new Promise((resolve, reject) => {
      this._http = http.createServer();
      this._http.on('error', (err) => reject(err));
      this._http.listen(this.port, this.host, () => {
        this._actualPort = this._http.address().port;
        this._wss = new WebSocketServer({ server: this._http });
        this._wss.on('connection', (ws) => this._handleConnection(ws));
        resolve();
      });
    });
  }

  async stop() {
    if (this._wss) {
      await new Promise((res) => this._wss.close(() => res()));
      this._wss = null;
    }
    if (this._http) {
      await new Promise((res) => this._http.close(() => res()));
      this._http = null;
    }
  }

  get actualPort() { return this._actualPort || this.port; }

  _handleConnection(ws) {
    ws.on('message', async (raw) => {
      let env;
      try {
        env = JSON.parse(raw.toString('utf8'));
      } catch (e) {
        return this._sendError(ws, null, 'parse_error', String(e && e.message ? e.message : e));
      }
      const verify = verifyEnvelope(env, this.secret, { maxSkewMs: this.maxSkewMs });
      if (!verify.ok) {
        return this._sendError(ws, env.id, 'auth_failed', verify.reason || '');
      }
      if (env.kind !== 'req') return; // ignore stray res/event
      const handler = this.handlers[env.method];
      if (!handler) {
        return this._sendError(ws, env.id, 'unknown_method', env.method);
      }
      const ctx = {
        method: env.method,
        requestId: env.id,
        peerDeviceId: env.auth.device_id,
        emitEvent: (eventPayload) => this._sendEvent(ws, eventPayload),
      };
      let result;
      try {
        result = await handler(env.params || {}, ctx);
      } catch (e) {
        return this._sendError(ws, env.id, 'handler_error', e && e.message ? e.message : String(e));
      }
      this._sendResult(ws, env.id, env.method, result || {});
    });
    ws.on('error', () => {});
  }

  _sendResult(ws, requestId, method, result) {
    const env = newEnvelope({ kind: 'res', method, params: result, deviceId: this.deviceId, requestId });
    signEnvelope(env, this.secret);
    try { ws.send(JSON.stringify(env)); } catch (_) {}
  }

  _sendEvent(ws, payload) {
    const env = newEnvelope({ kind: 'event', method: 'task.event', params: payload, deviceId: this.deviceId });
    signEnvelope(env, this.secret);
    try { ws.send(JSON.stringify(env)); } catch (_) {}
  }

  _sendError(ws, requestId, code, detail) {
    const env = newEnvelope({
      kind: 'res', method: 'error',
      params: { code, detail, request_id: requestId },
      deviceId: this.deviceId, requestId,
    });
    signEnvelope(env, this.secret);
    try { ws.send(JSON.stringify(env)); } catch (_) {}
  }
}

module.exports = { PeerServer, SCHEMA_VERSION };
