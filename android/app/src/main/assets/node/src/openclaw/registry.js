'use strict';
/**
 * Pure-JS tool registry shared between the OpenClaw launcher and the inbound
 * JSON-RPC server.
 *
 * Why a custom registry instead of openclaw's own:
 *   * The `openclaw` npm package's tool API is in flux; pinning to it directly
 *     would couple us to whatever version ships with the APK on a given build.
 *   * Several of our tools (peer.delegate, device.*) are Android/peer-mesh
 *     specific and don't exist upstream.
 *   * We need to expose tools to the OpenAI Realtime API in its function-tool
 *     JSON-Schema dialect, which is a small superset of OpenClaw's; doing the
 *     translation in one place is cleaner.
 *
 * Public API:
 *   register({name, description, parameters}, async handler(args) -> any)
 *   list()  -> Array<{type:'function', name, description, parameters}>
 *   invoke(name, args) -> Promise<{ok, result?, error?, ...}>
 *
 * Errors thrown by handlers are caught and returned as {ok:false, error:...}
 * so the caller (BenVoiceService -> Realtime model) sees a uniform shape.
 *
 * Tools register themselves at module-load time. The launcher imports the
 * built-in modules, which in turn call register() at top level.
 */

const tools = new Map();

function register(definition, handler) {
  if (!definition || !definition.name) {
    throw new Error('register: missing definition.name');
  }
  if (typeof handler !== 'function') {
    throw new Error('register: handler must be a function for ' + definition.name);
  }
  if (tools.has(definition.name)) {
    console.log('[tools] re-registering ' + definition.name);
  }
  tools.set(definition.name, { definition, handler });
}

function unregister(name) {
  return tools.delete(name);
}

/**
 * Returns the tool list in OpenAI Realtime function-tool format. The Realtime
 * API expects:
 *   { type: 'function', name, description, parameters: <JSON-Schema> }
 *
 * If a tool has no parameters declared, we ship an empty object schema so the
 * model knows the tool is callable with no args.
 */
function list() {
  return Array.from(tools.values()).map(({ definition }) => ({
    type: 'function',
    name: definition.name,
    description: definition.description || '',
    parameters: definition.parameters || { type: 'object', properties: {}, additionalProperties: false },
  }));
}

function describe(name) {
  const entry = tools.get(name);
  if (!entry) return null;
  return entry.definition;
}

async function invoke(name, args) {
  const entry = tools.get(name);
  if (!entry) {
    return { ok: false, error: 'unknown_tool:' + name };
  }
  try {
    const result = await Promise.resolve(entry.handler(args || {}));
    // If a handler explicitly returns its own envelope (with `ok` key) we
    // pass it through so handlers can flag retryable errors like
    // `permission_not_granted` without throwing.
    if (result && typeof result === 'object' && Object.prototype.hasOwnProperty.call(result, 'ok')) {
      return result;
    }
    return { ok: true, result };
  } catch (e) {
    const msg = e && e.message ? e.message : String(e);
    return { ok: false, error: msg };
  }
}

function size() {
  return tools.size;
}

function names() {
  return Array.from(tools.keys());
}

function clear() {
  tools.clear();
}

module.exports = { register, unregister, list, describe, invoke, size, names, clear };
