'use strict';
/**
 * Outbound JSON-RPC client to the Kotlin bridge (NodeBridgeService.kt @ 18791).
 *
 * Methods (mirroring the Kotlin handler map):
 *   ax.tree, ax.click, ax.click_at, ax.type, ax.swipe, ax.scroll,
 *   ax.focus, ax.launch_app, ax.screen_size, ax.screenshot
 *   ocr.recognize_text
 *   secrets.peer, secrets.set_peer, secrets.openai
 *   ping
 *
 * Each call opens a fresh TCP connection; the server is local + cheap, this
 * keeps reconnection logic trivial. Single-line newline-JSON-RPC, see
 * com.ben.bridge.JsonRpcServer.
 */
const net = require('net');

const HOST = '127.0.0.1';
const PORT = parseInt(process.env.BEN_RPC_PORT || '18791', 10);

let nextId = 0;

function call(method, params = {}, { timeoutMs = 5000 } = {}) {
  return new Promise((resolve, reject) => {
    const sock = net.createConnection({ host: HOST, port: PORT }, () => {
      const id = 'k' + (nextId++);
      const req = JSON.stringify({ id, method, params }) + '\n';
      sock.write(req);
    });
    let buf = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try { sock.destroy(); } catch (_) {}
      reject(new Error('kotlin_rpc timeout: ' + method));
    }, timeoutMs);
    sock.on('data', (chunk) => {
      buf += chunk.toString('utf8');
      const nl = buf.indexOf('\n');
      if (nl === -1) return;
      const line = buf.slice(0, nl);
      try {
        const parsed = JSON.parse(line);
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try { sock.end(); } catch (_) {}
        if (parsed.error) reject(new Error(parsed.error.message || 'rpc_error'));
        else resolve(parsed.result || {});
      } catch (e) {
        if (!settled) { settled = true; clearTimeout(timer); reject(e); }
      }
    });
    sock.on('error', (e) => {
      if (!settled) { settled = true; clearTimeout(timer); reject(e); }
    });
  });
}

module.exports = {
  ax: {
    tree: () => call('ax.tree'),
    click: (axId) => call('ax.click', { ax_id: axId }),
    clickAt: (x, y, app) => call('ax.click_at', Object.assign({ x, y }, app ? { app } : {})),
    type: (text, axId) => call('ax.type', Object.assign({ text }, axId ? { ax_id: axId } : {})),
    swipe: (x1, y1, x2, y2) => call('ax.swipe', { x1, y1, x2, y2 }),
    scroll: (x1, y1, x2, y2) => call('ax.scroll', { x1, y1, x2, y2 }),
    focus: (pkg) => call('ax.focus', { package: pkg }),
    launchApp: (pkg) => call('ax.launch_app', { package: pkg }),
    screenSize: () => call('ax.screen_size'),
    screenshot: (path, app) => call('ax.screenshot', Object.assign({}, path && { path }, app && { app })),
  },
  ocr: {
    recognizeText: (imagePath) => call('ocr.recognize_text', { image_path: imagePath }),
  },
  secrets: {
    peer: () => call('secrets.peer'),
    setPeer: (deviceId, host, port, secretB64) =>
      call('secrets.set_peer', { device_id: deviceId, host, port, secret_b64: secretB64 }),
    openai: () => call('secrets.openai'),
  },
  ping: (echo) => call('ping', { echo }),
};
