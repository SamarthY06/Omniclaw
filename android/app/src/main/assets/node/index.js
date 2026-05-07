'use strict';
/**
 * Embedded Node entrypoint, started by NodeBridgeService.kt via nodejs-mobile.
 *
 * Responsibilities:
 *   1. Start the inbound JSON-RPC server on 127.0.0.1:18792 (Kotlin -> Node).
 *      Used for session lifecycle notifications from BenVoiceService.kt.
 *   2. Boot the peer client (TS-compiled, in src/peer/), if a peer has been
 *      paired (read via the kotlin_rpc.secrets.peer call).
 *   3. Initialize the SessionStore so JSONL transcripts get persisted.
 *
 * Errors here log to Android logcat via console.* (nodejs-mobile redirects).
 */
const path = require('path');
const fs = require('fs');

const NODE_ROOT = process.env.BEN_NODE_ROOT || __dirname;
const WORKSPACE = process.env.BEN_WORKSPACE || path.join(__dirname, 'workspace_bootstrap');
const RPC_PORT = parseInt(process.env.BEN_RPC_PORT || '18791', 10);
const ROLE = process.env.BEN_DEVICE_ROLE || 'android';

console.log('[ben-node] hello from embedded node v' + process.versions.node);
console.log('[ben-node] NODE_ROOT=' + NODE_ROOT);
console.log('[ben-node] WORKSPACE=' + WORKSPACE);
console.log('[ben-node] kotlin RPC port=' + RPC_PORT);

(async function main() {
  try {
    const { startInboundRpc } = require('./src/bridge/inbound_rpc.js');
    const { ensureWorkspaceLayout } = require('./src/util/bootstrap.js');
    const { startPeerIfPaired } = require('./src/peer/start.js');
    const { startVoicePipeline } = require('./src/session/start.js');
    const { startOpenClaw } = require('./src/openclaw/launcher.js');

    ensureWorkspaceLayout(WORKSPACE);
    await startInboundRpc({ workspace: WORKSPACE });
    await startPeerIfPaired({ kotlinRpcPort: RPC_PORT, workspace: WORKSPACE, role: ROLE });
    await startVoicePipeline({ workspace: WORKSPACE });
    // OpenClaw boots last so the rest of the runtime is up if it fails.
    await startOpenClaw({ workspace: WORKSPACE, role: ROLE });

    console.log('[ben-node] runtime ready');
  } catch (err) {
    console.error('[ben-node] fatal startup error', err && err.stack ? err.stack : err);
  }
})();
