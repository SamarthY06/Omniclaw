'use strict';
/**
 * Self-test for src/bridge/kotlin_rpc.js.
 *
 * Stands up a fake JSON-RPC server on the same port and protocol that
 * BenAccessibilityService / BenScreencapService / AndroidOcr expose, then
 * drives every method in kotlin_rpc.js to assert wire shape compatibility.
 *
 * Run with:  node test/kotlin_rpc.test.js
 */
const net = require('net');
const path = require('path');
const assert = require('node:assert');

let server;
let lastRequest = null;
let RPC_PORT;

function startFakeServer() {
  return new Promise((resolve) => {
    server = net.createServer((sock) => {
      let buf = '';
      sock.on('data', (chunk) => {
        buf += chunk.toString('utf8');
        let nl;
        while ((nl = buf.indexOf('\n')) !== -1) {
          const line = buf.slice(0, nl);
          buf = buf.slice(nl + 1);
          handleRpc(sock, line);
        }
      });
      sock.on('error', () => {});
    });
    server.listen(0, '127.0.0.1', () => {
      RPC_PORT = server.address().port;
      process.env.BEN_RPC_PORT = String(RPC_PORT);
      resolve();
    });
  });
}

function handleRpc(sock, line) {
  const req = JSON.parse(line);
  lastRequest = req;
  let result;
  switch (req.method) {
    case 'ping': result = { pong: true, echo: req.params.echo }; break;
    case 'ax.tree': result = { ok: true, root: { ax_id: '0:0', children: [] }, count: 1, generation: 1 }; break;
    case 'ax.click_at': result = { ok: true, x: req.params.x, y: req.params.y }; break;
    case 'ax.click': result = { ok: true }; break;
    case 'ax.type': result = { ok: true }; break;
    case 'ax.swipe':
    case 'ax.scroll': result = { ok: true }; break;
    case 'ax.focus':
    case 'ax.launch_app': result = { ok: true, package: req.params.package }; break;
    case 'ax.screen_size': result = { ok: true, width: 1080, height: 2400 }; break;
    case 'ax.screenshot': result = { ok: true, path: '/tmp/x.png', width: 1080, height: 2400 }; break;
    case 'ocr.recognize_text':
      result = {
        ok: true, image_width: 1080, image_height: 2400,
        items: [{ text: 'BLR - Team', confidence: 0.95, bbox: { x: 100, y: 200, w: 300, h: 60 } }],
      };
      break;
    case 'secrets.peer':
      result = { device_id: 'mac-1', host: '192.168.1.5', port: 18790, secret_b64: 'AAAA', own_device_id: 'and-1' };
      break;
    case 'secrets.openai':
      result = { key: 'sk-test' };
      break;
    case 'secrets.set_peer':
      result = { ok: true };
      break;
    default:
      sock.write(JSON.stringify({ id: req.id, error: { message: 'unknown', code: -1 } }) + '\n');
      return;
  }
  sock.write(JSON.stringify({ id: req.id, result }) + '\n');
}

async function run() {
  await startFakeServer();
  // Re-require kotlin_rpc.js AFTER the server is up so the port env var was read.
  const kotlin = require(path.join(__dirname, '..', 'src', 'bridge', 'kotlin_rpc.js'));

  const ping = await kotlin.ping('hi');
  assert.strictEqual(ping.pong, true);
  assert.strictEqual(ping.echo, 'hi');

  const tree = await kotlin.ax.tree();
  assert.strictEqual(tree.ok, true);
  assert.strictEqual(lastRequest.method, 'ax.tree');

  const click = await kotlin.ax.clickAt(120, 220, 'com.whatsapp');
  assert.strictEqual(click.ok, true);
  assert.deepStrictEqual(lastRequest.params, { x: 120, y: 220, app: 'com.whatsapp' });

  const typeR = await kotlin.ax.type('hello', '0:5');
  assert.strictEqual(typeR.ok, true);
  assert.deepStrictEqual(lastRequest.params, { text: 'hello', ax_id: '0:5' });

  const launch = await kotlin.ax.launchApp('com.whatsapp');
  assert.strictEqual(launch.package, 'com.whatsapp');

  const size = await kotlin.ax.screenSize();
  assert.strictEqual(size.width, 1080);
  assert.strictEqual(size.height, 2400);

  const shot = await kotlin.ax.screenshot('/tmp/x.png', 'com.whatsapp');
  assert.strictEqual(shot.path, '/tmp/x.png');

  const ocr = await kotlin.ocr.recognizeText('/tmp/x.png');
  assert.strictEqual(ocr.ok, true);
  assert.strictEqual(ocr.items[0].text, 'BLR - Team');

  const sp = await kotlin.secrets.peer();
  assert.strictEqual(sp.host, '192.168.1.5');
  const so = await kotlin.secrets.openai();
  assert.strictEqual(so.key, 'sk-test');
  const ss = await kotlin.secrets.setPeer('mac-1', '192.168.1.5', 18790, 'AAAA');
  assert.strictEqual(ss.ok, true);

  console.log('kotlin_rpc.test PASS (10 methods)');
  server.close();
}

run().catch((e) => { console.error(e); server && server.close(); process.exit(1); });
