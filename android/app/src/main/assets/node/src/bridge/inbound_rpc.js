'use strict';
/**
 * 127.0.0.1:18792 newline-JSON-RPC server.
 *
 * Listeners:
 *   session.started        { session_id, device }
 *   session.ended          { session_id, reason }
 *   session.activity       { reason }
 *   session.user_text      { session_id, text }
 *   session.assistant_text { session_id, text }
 *   peer.pair_now          ()  -> kicks the peer client to (re)connect using the
 *                                  secrets currently stored in BenSecrets.
 *
 * Why we need this in addition to the Kotlin->Node-via-RPC pattern from
 * NodeBridgeService: lifecycle/transcription events are PUSHED from Kotlin, so
 * they need a server endpoint. The other direction (Node->Kotlin RPC for
 * AccessibilityService etc.) lives in src/bridge/kotlin_rpc.js.
 */
const net = require('net');
const { sessionStore } = require('../session/store.js');
const { sessionTimer } = require('../session/lifecycle.js');
// Tool registry. Required lazily inside tools.list/tools.invoke so that an
// import-time failure here doesn't block the rest of the RPC server from
// starting (session.* RPCs must keep working even if openclaw fails to
// load).
const DEFAULT_PORT = 18792;

function startInboundRpc({ workspace, port } = {}) {
  // `port` is an optional override - tests pass 0 to bind a free port. The
  // production caller (index.js) leaves it undefined, which uses 18792.
  const bindPort = (typeof port === 'number') ? port : DEFAULT_PORT;
  return new Promise((resolve) => {
    const server = net.createServer((socket) => {
      let buf = '';
      socket.on('data', (chunk) => {
        buf += chunk.toString('utf8');
        let nl;
        while ((nl = buf.indexOf('\n')) !== -1) {
          const line = buf.slice(0, nl);
          buf = buf.slice(nl + 1);
          handleLine(socket, line, workspace);
        }
      });
      socket.on('error', () => {});
    });
    server.listen(bindPort, '127.0.0.1', () => {
      const actual = server.address().port;
      console.log('[inbound_rpc] listening on 127.0.0.1:' + actual);
      resolve(server);
    });
  });
}

function handleLine(socket, line, workspace) {
  let req;
  try { req = JSON.parse(line); } catch (e) {
    return reply(socket, null, { error: { message: 'parse_error' } });
  }
  const id = req.id;
  const method = req.method || '';
  const params = req.params || {};
  // Tool RPCs are the one async branch; everything else is sync. Promise-
  // returning handlers reply when their promise settles.
  if (method === 'tools.list') {
    let registry;
    try { registry = require('../openclaw/registry.js'); }
    catch (e) {
      console.warn('[inbound_rpc] tools.list registry load failed:', e && e.message);
      return reply(socket, id, { result: { tools: [] } });
    }
    let tools = [];
    try { tools = registry.list(); }
    catch (e) {
      console.warn('[inbound_rpc] tools.list call failed:', e && e.message);
      tools = [];
    }
    return reply(socket, id, { result: { tools } });
  }
  if (method === 'tools.invoke') {
    let registry;
    try { registry = require('../openclaw/registry.js'); }
    catch (e) { return reply(socket, id, { result: { ok: false, error: 'registry_load_failed:' + (e && e.message) } }); }
    const name = params.name || '';
    if (!name) return reply(socket, id, { result: { ok: false, error: 'name_required' } });
    Promise.resolve(registry.invoke(name, params.args || {}))
      .then((envelope) => reply(socket, id, { result: envelope }))
      .catch((err) => reply(socket, id, { result: { ok: false, error: (err && err.message) ? err.message : String(err) } }));
    return;
  }
  // session_context: bundle USER.md + the most-recent memory facts so Kotlin
  // can prepend them to the Realtime sysPrompt at session start without
  // having to make N round-trips. Returns { user_facts, memory: {matches, total} }.
  if (method === 'session.context') {
    Promise.resolve()
      .then(async () => {
        const ctx = { user_facts: '', user_facts_present: false, memory: { matches: [], total: 0 } };
        try {
          const mem = require('../openclaw/memory_tools.js');
          const facts = await mem._memoryUserFacts();
          if (facts && facts.ok) {
            ctx.user_facts = facts.facts || '';
            ctx.user_facts_present = !!facts.present;
          }
          const limit = parseInt(params.memory_limit, 10);
          const search = await mem._memorySearch({
            query: '',
            limit: Number.isFinite(limit) ? limit : 8,
          });
          if (search && Array.isArray(search.matches)) {
            ctx.memory = search;
          }
        } catch (e) {
          console.warn('[inbound_rpc] session.context build failed:', e && e.message);
        }
        return ctx;
      })
      .then((ctx) => reply(socket, id, { result: ctx }))
      .catch((err) => reply(socket, id, { error: { message: (err && err.message) ? err.message : String(err) } }));
    return;
  }
  try {
    let result = {};
    switch (method) {
      case 'session.started':
        sessionStore(workspace).start(params.session_id, params.device || 'phone');
        sessionTimer().reset();
        result = { ok: true };
        break;
      case 'session.ended':
        sessionStore(workspace).end(params.session_id, params.reason || 'silence_180s');
        sessionTimer().stop();
        result = { ok: true };
        break;
      case 'session.activity':
        sessionTimer().markActivity(params.reason || 'unknown');
        result = { ok: true };
        break;
      case 'session.user_text':
        sessionStore(workspace).appendUserText(params.session_id, params.text || '');
        sessionTimer().markActivity('user_text');
        result = { ok: true };
        break;
      case 'session.assistant_text':
        sessionStore(workspace).appendAssistantText(params.session_id, params.text || '');
        sessionTimer().markActivity('assistant_text');
        result = { ok: true };
        break;
      case 'peer.pair_now': {
        // Re-trigger the peer client so it picks up the new secret.
        const startMod = require('../peer/start.js');
        startMod.repair();
        result = { ok: true };
        break;
      }
      case 'peer.pair_status': {
        // Return the live state of the peer client. This is the call the
        // Kotlin PairingActivity polls after peer.pair_now to verify the
        // handshake actually completed before showing "paired" to the user.
        // Result shape:
        //   { ok: true, paired: bool, last_error?: string, endpoint?: string }
        // We do an active liveness probe via peer.ping when the client
        // exists - this catches the case where the WSS opened but the
        // remote daemon is now unresponsive. Done async so the RPC reply
        // doesn't block the inbound socket loop.
        const startMod = require('../peer/start.js');
        const client = startMod.client && startMod.client();
        if (!client) {
          // Defer the reply slightly so handleLine's switch doesn't double
          // -reply via the catch-all `reply(socket, id, { result })` below.
          reply(socket, id, { result: { ok: true, paired: false, last_error: 'peer_client_not_started' } });
          return;
        }
        Promise.resolve()
          .then(async () => {
            try {
              await client.call('peer.ping', { ts_ms: Date.now() }, { timeoutMs: 3000 });
              return { ok: true, paired: true };
            } catch (e) {
              return {
                ok: true,
                paired: false,
                last_error: (e && e.message) ? e.message : String(e),
              };
            }
          })
          .then((status) => reply(socket, id, { result: status }))
          .catch((err) => reply(socket, id, { result: { ok: true, paired: false, last_error: (err && err.message) || String(err) } }));
        return;
      }
      default:
        return reply(socket, id, { error: { message: 'unknown_method:' + method } });
    }
    reply(socket, id, { result });
  } catch (e) {
    reply(socket, id, { error: { message: e && e.message ? e.message : String(e) } });
  }
}

function reply(socket, id, payload) {
  try {
    socket.write(JSON.stringify(Object.assign({ id }, payload)) + '\n');
  } catch (_) {}
}

module.exports = { startInboundRpc };
