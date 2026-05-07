'use strict';
/**
 * Peer WS client. Direct port of omniclaw/peer/client.py.
 *
 * Public API:
 *   const c = new PeerClient({ deviceId, secret, endpoint });
 *   await c.connect();
 *   const result = await c.call('peer.ping', { ts_ms: Date.now() });
 *   // streaming task.run
 *   const { events, result } = c.stream('task.run', { run_id: 'r1', ... });
 */
const { signEnvelope, verifyEnvelope } = require('./crypto.js');
const { newEnvelope } = require('./types.js');

class RemoteError extends Error {
  constructor(code, detail) {
    super(detail ? code + ': ' + detail : code);
    this.code = code;
    this.detail = detail || '';
  }
}

class PeerClient {
  constructor({ deviceId, secret, endpoint, maxSkewMs = 60_000, connectTimeoutMs = 5000 }) {
    this.deviceId = deviceId;
    this.secret = secret;
    this.endpoint = endpoint;
    this.maxSkewMs = maxSkewMs;
    this.connectTimeoutMs = connectTimeoutMs;
    this._ws = null;
    this._pending = new Map();         // requestId -> { resolve, reject, method }
    this._eventQueues = new Map();     // runId -> { queue: [], resolvers: [], finished: false }
  }

  async connect() {
    if (this._ws) return;
    const { WebSocket } = require('ws');
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.endpoint);
      const t = setTimeout(() => { try { ws.terminate(); } catch (_) {}; reject(new Error('connect_timeout')); }, this.connectTimeoutMs);
      ws.on('open', () => { clearTimeout(t); this._ws = ws; this._wireUp(); resolve(); });
      ws.on('error', (err) => { clearTimeout(t); reject(err); });
    });
  }

  async close() {
    if (this._ws) {
      try { this._ws.close(); } catch (_) {}
      this._ws = null;
    }
    for (const p of this._pending.values()) p.reject(new Error('client_closed'));
    this._pending.clear();
  }

  _wireUp() {
    this._ws.on('message', (raw) => {
      let env;
      try { env = JSON.parse(raw.toString('utf8')); } catch (_) { return; }
      const verify = verifyEnvelope(env, this.secret, { maxSkewMs: this.maxSkewMs });
      if (!verify.ok) return;
      if (env.kind === 'res') this._dispatchResult(env);
      else if (env.kind === 'event') this._dispatchEvent(env);
    });
    this._ws.on('close', () => {
      for (const p of this._pending.values()) p.reject(new Error('connection_closed'));
      this._pending.clear();
      this._ws = null;
    });
  }

  _dispatchResult(env) {
    const p = this._pending.get(env.id);
    if (!p) return;
    this._pending.delete(env.id);
    if (env.method === 'error') {
      p.reject(new RemoteError(env.params.code || 'unknown', env.params.detail || ''));
      return;
    }
    p.resolve(env.params);
  }

  _dispatchEvent(env) {
    const runId = env.params && env.params.run_id;
    if (!runId) return;
    const q = this._eventQueues.get(runId);
    if (!q) return;
    if (q.resolvers.length > 0) {
      const r = q.resolvers.shift();
      r({ value: env.params, done: false });
    } else {
      q.queue.push(env.params);
    }
  }

  call(method, params, { timeoutMs = 10_000 } = {}) {
    if (!this._ws) throw new Error('not_connected');
    const env = newEnvelope({ kind: 'req', method, params, deviceId: this.deviceId });
    signEnvelope(env, this.secret);
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => {
        if (this._pending.has(env.id)) {
          this._pending.delete(env.id);
          reject(new Error('rpc_timeout: ' + method));
        }
      }, timeoutMs);
      this._pending.set(env.id, {
        resolve: (v) => { clearTimeout(t); resolve(v); },
        reject: (e) => { clearTimeout(t); reject(e); },
        method,
      });
      try { this._ws.send(JSON.stringify(env)); } catch (e) {
        clearTimeout(t); this._pending.delete(env.id); reject(e);
      }
    });
  }

  /** stream() returns { events: AsyncIterable, result: Promise<dict> }. */
  stream(method, params, { timeoutMs = 60_000 } = {}) {
    if (!this._ws) throw new Error('not_connected');
    const runId = params && params.run_id;
    if (!runId) throw new Error('stream() requires params.run_id');
    const env = newEnvelope({ kind: 'req', method, params, deviceId: this.deviceId });
    signEnvelope(env, this.secret);
    const queue = { queue: [], resolvers: [], finished: false };
    this._eventQueues.set(runId, queue);

    const finalize = () => {
      queue.finished = true;
      this._eventQueues.delete(runId);
      // Wake any iterator that's blocked waiting for a next event.
      while (queue.resolvers.length > 0) {
        const r = queue.resolvers.shift();
        r({ value: undefined, done: true });
      }
    };
    const result = new Promise((resolve, reject) => {
      const t = setTimeout(() => {
        if (this._pending.has(env.id)) {
          this._pending.delete(env.id);
          finalize();
          reject(new Error('rpc_timeout: ' + method));
        }
      }, timeoutMs);
      this._pending.set(env.id, {
        resolve: (v) => { clearTimeout(t); finalize(); resolve(v); },
        reject: (e) => { clearTimeout(t); finalize(); reject(e); },
        method,
      });
      try { this._ws.send(JSON.stringify(env)); } catch (e) { clearTimeout(t); finalize(); reject(e); }
    });

    const events = {
      [Symbol.asyncIterator]() {
        return {
          next() {
            if (queue.queue.length > 0) {
              return Promise.resolve({ value: queue.queue.shift(), done: false });
            }
            if (queue.finished) return Promise.resolve({ value: undefined, done: true });
            return new Promise((res) => { queue.resolvers.push(res); });
          },
        };
      },
    };

    return { events, result };
  }
}

module.exports = { PeerClient, RemoteError };
