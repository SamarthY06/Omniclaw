'use strict';
/**
 * OpenClaw launcher for the embedded Android Node runtime.
 *
 * Two responsibilities, in order:
 *   1) Register every tool the OpenAI Realtime model can invoke
 *      (peer.delegate, ax.*, device.*) into the in-process registry. These
 *      registrations succeed even if step 2 fails - the user still gets a
 *      working Jarvis-layer-with-no-OpenClaw-extras experience.
 *   2) Optionally boot the `openclaw` npm package's gateway. This adds the
 *      package's own tool set (web fetch, file read/write, etc.) on top of
 *      ours. If the package isn't bundled or its native deps fail, the
 *      function returns gracefully so the rest of the runtime stays up.
 *
 * Why we require() instead of forking a child process: nodejs-mobile on
 * Android can't fork node binaries.
 */
const path = require('path');

const registry = require('./registry.js');
const { registerBuiltinTools } = require('./builtin_tools.js');
const { registerDeviceTools } = require('./device_tools.js');

let _gatewayHandle = null;

async function startOpenClaw({ workspace, role }) {
  // (1) Always-on registrations. These don't depend on the openclaw npm
  // package being installable or its native deps loading; they only need
  // our in-process registry which has no external deps.
  try {
    registerBuiltinTools();
    registerDeviceTools();
    console.log('[openclaw] registered ' + registry.size() + ' built-in tools: ' + registry.names().join(', '));
  } catch (e) {
    console.warn('[openclaw] built-in tool registration failed:', e && e.message);
  }

  // (2) Optional openclaw npm gateway. Wrapped in a try so a missing or
  // broken package never crashes the runtime. If the gateway exposes its
  // own tool registry and we can iterate it, mirror those into our registry
  // so they appear in tools.list alongside the built-ins.
  let openclaw;
  try {
    openclaw = require('openclaw');
  } catch (e) {
    console.log('[openclaw] package not present in this build (' + (e && e.code) + '); using built-in tools only');
    return { ok: true, gateway: null, builtin_only: true };
  }

  const cfgPath = path.join(workspace, 'openclaw.json');
  console.log('[openclaw] booting from workspace=' + workspace + ' config=' + cfgPath);

  const candidates = [
    () => typeof openclaw.start === 'function' && openclaw.start({ workspace, configPath: cfgPath }),
    () => typeof openclaw.bootstrap === 'function' && openclaw.bootstrap({ workspace, configPath: cfgPath }),
    () => typeof openclaw.run === 'function' && openclaw.run(['start', '--workspace', workspace]),
    () => typeof openclaw.cli === 'function' && openclaw.cli(['start', '--workspace', workspace]),
    () => typeof openclaw === 'function' && openclaw({ workspace, configPath: cfgPath }),
  ];
  for (const tryStart of candidates) {
    try {
      const result = await tryStart();
      if (result !== false && result !== undefined && result !== null) {
        _gatewayHandle = result && typeof result === 'object' ? result : openclaw;
        console.log('[openclaw] gateway started');
        // If the gateway has a listTools() method, mirror its tools into
        // our registry. This is best-effort: the openclaw npm API surface
        // changes between versions, so we probe defensively.
        try {
          const upstream = await _maybeListUpstream(_gatewayHandle);
          if (Array.isArray(upstream) && upstream.length > 0) {
            for (const t of upstream) {
              if (!t || !t.name) continue;
              if (registry.describe(t.name)) continue; // don't clobber built-ins
              registry.register(
                {
                  name: t.name,
                  description: t.description || '',
                  parameters: t.parameters || t.input_schema || { type: 'object', properties: {}, additionalProperties: false },
                },
                async (args) => _invokeUpstream(_gatewayHandle, t.name, args),
              );
            }
            console.log('[openclaw] mirrored ' + upstream.length + ' upstream tools into registry');
          }
        } catch (e) {
          console.warn('[openclaw] upstream tool mirror failed:', e && e.message);
        }
        return { ok: true, gateway: _gatewayHandle, builtin_only: false };
      }
    } catch (e) {
      console.log('[openclaw] start path threw: ' + (e && e.message));
    }
  }
  console.log('[openclaw] no compatible programmatic entrypoint found; running built-in tools only');
  return { ok: true, gateway: null, builtin_only: true, reason: 'no_entrypoint' };
}

async function _maybeListUpstream(gateway) {
  if (!gateway) return [];
  if (typeof gateway.listTools === 'function') return await gateway.listTools();
  if (gateway.tools && typeof gateway.tools.list === 'function') return await gateway.tools.list();
  if (gateway.registry && typeof gateway.registry.list === 'function') return await gateway.registry.list();
  return [];
}

async function _invokeUpstream(gateway, name, args) {
  if (!gateway) throw new Error('upstream_unavailable');
  if (typeof gateway.invoke === 'function') return await gateway.invoke(name, args);
  if (gateway.tools && typeof gateway.tools.invoke === 'function') return await gateway.tools.invoke(name, args);
  if (gateway.registry && typeof gateway.registry.invoke === 'function') return await gateway.registry.invoke(name, args);
  throw new Error('upstream_no_invoke_method');
}

function gateway() {
  return _gatewayHandle;
}

module.exports = { startOpenClaw, gateway, registry };
