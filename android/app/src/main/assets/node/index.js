'use strict';
/**
 * Embedded Node entrypoint, started by NodeBridgeService.kt via nodejs-mobile.
 *
 * Responsibilities (booted IN ORDER, each in its own try/catch so one
 * failure can't take the rest of the runtime down):
 *
 *   1. ensureWorkspaceLayout    -- create ~/.ben workspace dirs.
 *   2. startInboundRpc          -- 127.0.0.1:18792 server. Kotlin ->
 *                                  Node lifecycle pings, tools.list,
 *                                  tools.invoke, peer.pair_now,
 *                                  peer.pair_status, session.context.
 *   3. startPeerIfPaired        -- WSS client + server to the Mac peer.
 *                                  May fail (Mac offline / not paired);
 *                                  must NOT block the rest of boot.
 *   4. startVoicePipeline       -- transcript persistence helpers used
 *                                  by BenVoiceService.
 *   5. startOpenClaw            -- registers the device.* / ui.* / vision.* /
 *                                  memory.* tool registry. Booted last
 *                                  because it's the heaviest and the
 *                                  most likely to fail (eg. on a fresh
 *                                  install with no openclaw npm package).
 *
 * Failure isolation: each subsystem boots inside its own try/catch.
 * Pre-fix, a single throw in startPeerIfPaired (eg. "secrets not yet
 * persisted") aborted the IIFE, leaving the inbound RPC server up but
 * the OpenClaw tool registry uninitialised - which made every subsequent
 * tools.list reply with an empty array and the user got a "I don't have
 * any tools" reply for hours until they restarted the app.
 *
 * Errors here log to Android logcat via console.* (nodejs-mobile redirects).
 */
const path = require('path');

const NODE_ROOT = process.env.BEN_NODE_ROOT || __dirname;
const WORKSPACE = process.env.BEN_WORKSPACE || path.join(__dirname, 'workspace_bootstrap');
const RPC_PORT = parseInt(process.env.BEN_RPC_PORT || '18791', 10);
const ROLE = process.env.BEN_DEVICE_ROLE || 'android';

console.log('[ben-node] hello from embedded node v' + process.versions.node);
console.log('[ben-node] NODE_ROOT=' + NODE_ROOT);
console.log('[ben-node] WORKSPACE=' + WORKSPACE);
console.log('[ben-node] kotlin RPC port=' + RPC_PORT);

async function bootSubsystem(name, fn) {
  try {
    await fn();
    console.log('[ben-node] ' + name + ' OK');
  } catch (e) {
    // Per-subsystem failure isolation: log + carry on. The other
    // subsystems are still useful even if one is dead. The Realtime
    // model's "TOOL RULE" prompt tells it to apologise for missing
    // capabilities rather than refuse the whole conversation.
    console.error('[ben-node] ' + name + ' FAILED:', e && e.stack ? e.stack : e);
  }
}

(async function main() {
  let inboundRpc, ensureWorkspaceLayout, startPeerIfPaired, startVoicePipeline, startOpenClaw;
  try {
    inboundRpc = require('./src/bridge/inbound_rpc.js');
    ensureWorkspaceLayout = require('./src/util/bootstrap.js').ensureWorkspaceLayout;
    startPeerIfPaired = require('./src/peer/start.js').startPeerIfPaired;
    startVoicePipeline = require('./src/session/start.js').startVoicePipeline;
    startOpenClaw = require('./src/openclaw/launcher.js').startOpenClaw;
  } catch (e) {
    // Top-level require failure means the JS bundle itself is broken
    // (corrupted assets, missing node_modules). Nothing we can do
    // here - log and exit gracefully so the JNI side can show the
    // bridge as offline.
    console.error('[ben-node] FATAL: module loading failed:', e && e.stack ? e.stack : e);
    return;
  }

  await bootSubsystem('workspace', () => ensureWorkspaceLayout(WORKSPACE));
  await bootSubsystem('inbound_rpc', () => inboundRpc.startInboundRpc({ workspace: WORKSPACE }));
  await bootSubsystem('peer', () => startPeerIfPaired({
    kotlinRpcPort: RPC_PORT,
    workspace: WORKSPACE,
    role: ROLE,
  }));
  await bootSubsystem('voice_pipeline', () => startVoicePipeline({ workspace: WORKSPACE }));
  await bootSubsystem('openclaw', () => startOpenClaw({ workspace: WORKSPACE, role: ROLE }));

  console.log('[ben-node] runtime ready');
})();
