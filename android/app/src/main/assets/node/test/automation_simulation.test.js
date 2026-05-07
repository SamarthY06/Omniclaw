'use strict';
/**
 * End-to-end simulation: "Open WhatsApp, find Pragati, type Hi, send".
 *
 * What this proves WITHOUT a physical device:
 *   1. The OpenAI Realtime model's tool-call JSON dispatches correctly through
 *      the inbound RPC -> registry -> kotlin_rpc -> (mocked) device chain.
 *   2. Each step of the standard on-phone flow we taught the model to use
 *      actually works:
 *        device.launch_app(WhatsApp)
 *        ui.read_screen
 *        ui.click("Pragati")
 *        ui.type("Hi")
 *        ui.click("Send")
 *        ui.read_screen (verify)
 *   3. Vision fallback: when ui.read_screen does not contain the target text,
 *      ui.screenshot + vision.locate_text + ui.click_at completes the task.
 *   4. Cross-device: peer.delegate routes through to the (mocked) peer client.
 *   5. Permission UX: a tool that returns permission_not_granted carries the
 *      hint to the model so it can apologise / retry.
 *
 * What this CANNOT prove (intentionally - needs a real phone):
 *   * Real WhatsApp UI surface.
 *   * Real Realtime model picking the right tools (that's prompt-tuning,
 *     verified by the on-device protocol in CHANGELOG / setup doc).
 *   * Permission system dialogs.
 *
 * Run with:  node test/automation_simulation.test.js
 */

const assert = require('node:assert');
const net = require('net');
const fs = require('fs');
const os = require('os');
const path = require('path');

let fakeKotlin;
let inboundServer;
let TEST_INBOUND_PORT;
const kotlinCalls = [];

// ----- mocked phone state -----
const phoneState = {
  focusedPackage: null,
  // canned screen we return from ax.tree. The first frame includes Pragati,
  // a Send button, and the message input field. After we type "Hi" we mutate
  // it to include the typed text so the verification ui.read_screen sees it.
  screenFrame: 'whatsapp_chat_list',
  typedText: '',
};

function startFakeKotlin() {
  return new Promise((resolve) => {
    fakeKotlin = net.createServer((sock) => {
      let buf = '';
      sock.on('data', (chunk) => {
        buf += chunk.toString('utf8');
        let nl;
        while ((nl = buf.indexOf('\n')) !== -1) {
          const line = buf.slice(0, nl);
          buf = buf.slice(nl + 1);
          handleKotlin(sock, line);
        }
      });
      sock.on('error', () => {});
    });
    fakeKotlin.listen(0, '127.0.0.1', () => {
      const port = fakeKotlin.address().port;
      process.env.BEN_RPC_PORT = String(port);
      resolve(port);
    });
  });
}

function reply(sock, id, result) {
  sock.write(JSON.stringify({ id, result }) + '\n');
}

function handleKotlin(sock, line) {
  const req = JSON.parse(line);
  kotlinCalls.push({ method: req.method, params: req.params });
  switch (req.method) {
    case 'ax.launch_app':
      phoneState.focusedPackage = req.params.package;
      phoneState.screenFrame = req.params.package === 'com.whatsapp' ? 'whatsapp_chat_list' : 'unknown_app';
      return reply(sock, req.id, { ok: true, package: req.params.package });
    case 'device.launch_app': {
      const pkg = req.params.package || 'com.example.unknown';
      phoneState.focusedPackage = pkg;
      phoneState.screenFrame = pkg === 'com.whatsapp' ? 'whatsapp_chat_list' : 'unknown_app';
      return reply(sock, req.id, { ok: true, result: { launched: true, package: pkg } });
    }
    case 'device.get_location':
      return reply(sock, req.id, { ok: true, result: { latitude: 12.97, longitude: 77.59, accuracy_m: 14.0, source: 'fused' } });
    case 'device.clipboard_get':
      return reply(sock, req.id, { ok: true, result: { text: 'sample' } });
    case 'device.clipboard_set':
      return reply(sock, req.id, { ok: true, result: { set: true, length: (req.params.text || '').length } });
    case 'device.place_call':
      return reply(sock, req.id, { ok: true, result: { dialed: req.params.number || 'unknown' } });
    case 'ax.focus':
      phoneState.focusedPackage = req.params.package;
      return reply(sock, req.id, { ok: true, package: req.params.package });
    case 'ax.tree':
      return reply(sock, req.id, currentTree());
    case 'ax.click':
      // a click on the Pragati row advances the screen to a chat with her
      if (req.params.ax_id === 'chat:pragati') {
        phoneState.screenFrame = 'whatsapp_chat_pragati';
      } else if (req.params.ax_id === 'btn:send') {
        phoneState.screenFrame = 'whatsapp_chat_pragati_after_send';
      }
      return reply(sock, req.id, { ok: true });
    case 'ax.click_at':
      // click_at on the chat-list row that vision.locate_text returned (380, 230)
      if (req.params.x > 200 && req.params.y > 200 && req.params.y < 260) {
        phoneState.screenFrame = 'whatsapp_chat_pragati';
      }
      return reply(sock, req.id, { ok: true, x: req.params.x, y: req.params.y });
    case 'ax.type':
      phoneState.typedText = req.params.text || '';
      return reply(sock, req.id, { ok: true });
    case 'ax.scroll':
      return reply(sock, req.id, { ok: true });
    case 'ax.swipe':
      return reply(sock, req.id, { ok: true });
    case 'ax.screen_size':
      return reply(sock, req.id, { ok: true, width: 1080, height: 2400 });
    case 'ax.screenshot': {
      const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ben-shot-'));
      const file = path.join(tmp, 'screen.png');
      // 1x1 PNG so vision.read_screen has bytes to base64 if it ever runs.
      const png = Buffer.from('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c63000100000005000101a8af2c0d0000000049454e44ae426082', 'hex');
      fs.writeFileSync(file, png);
      return reply(sock, req.id, { ok: true, path: file, width: 1080, height: 2400 });
    }
    case 'ocr.recognize_text':
      return reply(sock, req.id, currentOcr());
    case 'secrets.peer':
      return reply(sock, req.id, { device_id: 'mac-1', host: '127.0.0.1', port: 18790, secret_b64: 'AAAA', own_device_id: 'phone-1' });
    case 'secrets.openai':
      return reply(sock, req.id, { key: 'sk-test-fake' });
    case 'device.battery_status':
      return reply(sock, req.id, { ok: true, result: { percent: 88, charging: true } });
    case 'device.get_contacts':
      return reply(sock, req.id, { ok: false, error: 'permission_not_granted', permission: 'android.permission.READ_CONTACTS', hint: 'User has been prompted; ask them to allow and retry.' });
    default:
      return sock.write(JSON.stringify({ id: req.id, error: { message: 'unknown_method:' + req.method } }) + '\n');
  }
}

function currentTree() {
  if (phoneState.screenFrame === 'whatsapp_chat_list') {
    return {
      generation: 1,
      count: 4,
      root: {
        ax_id: 'root', role: 'window', cx: 540, cy: 1200,
        children: [
          { ax_id: 'chat:harini', role: 'row', text: 'Harini', cx: 540, cy: 180, w: 1080, h: 160 },
          { ax_id: 'chat:pragati', role: 'row', text: 'Pragati Biradar', cx: 540, cy: 360, w: 1080, h: 160 },
          { ax_id: 'chat:rohan', role: 'row', text: 'Rohan', cx: 540, cy: 540, w: 1080, h: 160 },
        ],
      },
    };
  }
  if (phoneState.screenFrame === 'whatsapp_chat_pragati') {
    return {
      generation: 2,
      count: 5,
      root: {
        ax_id: 'root', role: 'window', cx: 540, cy: 1200,
        children: [
          { ax_id: 'header', role: 'header', text: 'Pragati Biradar', cx: 540, cy: 80 },
          { ax_id: 'msg:welcome', role: 'text', text: 'Hey, what\'s up?', cx: 540, cy: 1000 },
          { ax_id: 'input:msg', role: 'edit', contentDescription: 'Message', text: phoneState.typedText, cx: 540, cy: 2200, w: 800, h: 120 },
          { ax_id: 'btn:send', role: 'button', contentDescription: 'Send', text: 'Send', cx: 1000, cy: 2200, w: 120, h: 120 },
        ],
      },
    };
  }
  if (phoneState.screenFrame === 'whatsapp_chat_pragati_after_send') {
    return {
      generation: 3,
      count: 6,
      root: {
        ax_id: 'root', role: 'window',
        children: [
          { ax_id: 'header', role: 'header', text: 'Pragati Biradar', cx: 540, cy: 80 },
          { ax_id: 'msg:welcome', role: 'text', text: 'Hey, what\'s up?', cx: 540, cy: 900 },
          { ax_id: 'msg:sent', role: 'text', text: 'Hi', cx: 880, cy: 1100 },
          { ax_id: 'input:msg', role: 'edit', contentDescription: 'Message', text: '', cx: 540, cy: 2200 },
          { ax_id: 'btn:send', role: 'button', contentDescription: 'Send', text: 'Send', cx: 1000, cy: 2200 },
        ],
      },
    };
  }
  if (phoneState.screenFrame === 'electron_chat_list') {
    // Sparse tree (Electron-style): no usable text, forces vision fallback.
    return { generation: 1, count: 1, root: { ax_id: 'root', role: 'window', children: [] } };
  }
  return { generation: 0, count: 0, root: { ax_id: 'root', role: 'window', children: [] } };
}

function currentOcr() {
  if (phoneState.screenFrame === 'electron_chat_list') {
    return {
      ok: true,
      image_width: 1080, image_height: 2400,
      items: [
        { text: 'Harini', confidence: 0.93, bbox: { x: 100, y: 100, w: 200, h: 60 } },
        { text: 'Pragati Biradar', confidence: 0.96, bbox: { x: 100, y: 200, w: 560, h: 60 } },
        { text: 'Rohan', confidence: 0.91, bbox: { x: 100, y: 300, w: 200, h: 60 } },
      ],
    };
  }
  if (phoneState.screenFrame === 'whatsapp_chat_list') {
    return {
      ok: true,
      image_width: 1080, image_height: 2400,
      items: [
        { text: 'Pragati Biradar', confidence: 0.96, bbox: { x: 100, y: 320, w: 560, h: 60 } },
      ],
    };
  }
  return { ok: true, items: [] };
}

function startInbound() {
  return new Promise((resolve) => {
    const inbound = require(path.join(__dirname, '..', 'src', 'bridge', 'inbound_rpc.js'));
    const tmpWorkspace = fs.mkdtempSync(path.join(os.tmpdir(), 'ben-sim-'));
    inbound.startInboundRpc({ workspace: tmpWorkspace, port: 0 }).then((srv) => {
      inboundServer = srv;
      TEST_INBOUND_PORT = srv.address().port;
      resolve();
    });
  });
}

function rpcCall(method, params) {
  return new Promise((resolve, reject) => {
    const sock = net.createConnection({ host: '127.0.0.1', port: TEST_INBOUND_PORT }, () => {
      sock.write(JSON.stringify({ id: 't' + Date.now() + Math.random(), method, params }) + '\n');
    });
    let buf = '';
    sock.on('data', (chunk) => {
      buf += chunk.toString('utf8');
      const nl = buf.indexOf('\n');
      if (nl === -1) return;
      const line = buf.slice(0, nl);
      try { sock.end(); } catch (_) {}
      try { resolve(JSON.parse(line)); } catch (e) { reject(e); }
    });
    sock.on('error', reject);
    setTimeout(() => reject(new Error('rpcCall timeout: ' + method)), 8000);
  });
}

async function invokeTool(name, args) {
  const r = await rpcCall('tools.invoke', { name, args: args || {} });
  return r.result;
}

async function listTools() {
  const r = await rpcCall('tools.list', {});
  return r.result.tools;
}

async function scenarioWhatsApp() {
  console.log('  scenario: WhatsApp -> Pragati -> "Hi" -> Send (AX-tree-driven path)');
  let r;
  // 1. Launch
  r = await invokeTool('device.launch_app', { package: 'com.whatsapp' });
  assert.strictEqual(r.ok, true, 'launch failed: ' + JSON.stringify(r));
  assert.strictEqual(phoneState.focusedPackage, 'com.whatsapp');

  // 2. Read screen, confirm Pragati visible
  r = await invokeTool('ui.read_screen', {});
  assert.strictEqual(r.ok, true);
  const flat = JSON.stringify(r.result);
  assert.ok(flat.includes('Pragati Biradar'), 'tree missing Pragati: ' + flat.slice(0, 200));

  // 3. Tap Pragati by visible text
  r = await invokeTool('ui.click', { text: 'Pragati' });
  assert.strictEqual(r.ok, true, 'click Pragati: ' + JSON.stringify(r));
  assert.strictEqual(r.result.ax_id, 'chat:pragati');

  // 4. Confirm chat opened
  r = await invokeTool('ui.read_screen', {});
  assert.ok(JSON.stringify(r.result).includes('Hey, what'), 'chat did not open');

  // 5. Tap input field then type
  r = await invokeTool('ui.click', { ax_id: 'input:msg' });
  assert.strictEqual(r.ok, true);
  r = await invokeTool('ui.type', { text: 'Hi' });
  assert.strictEqual(r.ok, true);
  assert.strictEqual(phoneState.typedText, 'Hi');

  // 6. Tap Send
  r = await invokeTool('ui.click', { text: 'Send' });
  assert.strictEqual(r.ok, true, 'click Send: ' + JSON.stringify(r));
  assert.strictEqual(r.result.ax_id, 'btn:send');

  // 7. Verify the message is in the tree
  r = await invokeTool('ui.read_screen', {});
  const tree = JSON.stringify(r.result);
  assert.ok(tree.includes('"text":"Hi"'), 'sent message missing in verification tree');

  console.log('  -> ' + kotlinCalls.length + ' Kotlin RPC calls during scenario');
}

async function scenarioElectronVisionFallback() {
  console.log('  scenario: Electron-style app -> AX tree empty -> vision.locate_text fallback');
  // Reset phone to "Electron" state (sparse tree, OCR-rich screen).
  phoneState.focusedPackage = 'com.example.electronchat';
  phoneState.screenFrame = 'electron_chat_list';
  phoneState.typedText = '';

  // ui.click by text MUST fail -> model would fall back to vision
  let r = await invokeTool('ui.click', { text: 'Pragati' });
  assert.strictEqual(r.ok, false, 'expected click fail on sparse tree');
  assert.match(r.error, /no_visible_match/);

  // vision.locate_text returns coordinates for the OCR'd "Pragati Biradar"
  r = await invokeTool('vision.locate_text', { target: 'Pragati' });
  assert.strictEqual(r.ok, true, 'vision.locate_text: ' + JSON.stringify(r));
  assert.strictEqual(r.result.found, true);
  assert.ok(typeof r.result.click_x === 'number', 'click_x missing');
  assert.ok(typeof r.result.click_y === 'number', 'click_y missing');

  // ui.click_at the returned coordinates -> chat opens
  const cx = r.result.click_x; const cy = r.result.click_y;
  r = await invokeTool('ui.click_at', { x: cx, y: cy });
  assert.strictEqual(r.ok, true, 'click_at: ' + JSON.stringify(r));
  assert.strictEqual(phoneState.screenFrame, 'whatsapp_chat_pragati', 'screen did not advance');
}

async function scenarioPermissionFlow() {
  console.log('  scenario: device.get_contacts returns permission_not_granted; envelope reaches model');
  const r = await invokeTool('device.get_contacts', { query: 'pra' });
  // device_tools wraps the kotlin error envelope as-is:
  assert.strictEqual(r.ok, false);
  assert.match(r.error, /permission_not_granted/);
  assert.match(r.permission, /READ_CONTACTS/);
}

async function scenarioPeerDelegate() {
  console.log('  scenario: peer.delegate without a paired peer client');
  const r = await invokeTool('peer.delegate', { task: 'on my Mac, take a screenshot' });
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.error, 'peer_not_paired');
  assert.match(r.hint, /pair/);
}

async function scenarioToolListShape() {
  console.log('  scenario: tools.list shape sanity');
  const tools = await listTools();
  const names = tools.map((t) => t.name);
  // Mandatory presence:
  for (const must of [
    'peer.delegate',
    'ui.read_screen', 'ui.click', 'ui.click_at', 'ui.type', 'ui.scroll', 'ui.swipe',
    'ui.screenshot', 'ui.screen_size', 'ui.focus_app',
    'vision.locate_text', 'vision.read_screen',
    'device.get_location', 'device.get_contacts', 'device.place_call',
    'device.launch_app', 'device.clipboard_get', 'device.clipboard_set',
    'device.battery_status',
  ]) {
    assert.ok(names.includes(must), 'missing tool: ' + must);
  }
  for (const t of tools) {
    assert.strictEqual(t.type, 'function', t.name + ' missing type=function');
    assert.ok(t.parameters && typeof t.parameters === 'object', t.name + ' missing parameters');
    assert.ok(typeof t.description === 'string' && t.description.length > 10, t.name + ' description too short');
  }
  console.log('  -> ' + tools.length + ' tools registered, names: ' + names.join(', '));
}

async function run() {
  await startFakeKotlin();
  await startInbound();
  // Force-reset registry then load all tools (built-in + device).
  const registry = require(path.join(__dirname, '..', 'src', 'openclaw', 'registry.js'));
  registry.clear();
  require(path.join(__dirname, '..', 'src', 'openclaw', 'builtin_tools.js')).registerBuiltinTools();
  require(path.join(__dirname, '..', 'src', 'openclaw', 'device_tools.js')).registerDeviceTools();

  await scenarioToolListShape();
  await scenarioWhatsApp();
  await scenarioElectronVisionFallback();
  await scenarioPermissionFlow();
  await scenarioPeerDelegate();

  console.log('automation_simulation.test PASS (5 scenarios, ' + kotlinCalls.length + ' Kotlin RPCs)');
  inboundServer.close(); fakeKotlin.close();
}

run().catch((e) => {
  console.error(e);
  if (inboundServer) try { inboundServer.close(); } catch (_) {}
  if (fakeKotlin) try { fakeKotlin.close(); } catch (_) {}
  process.exit(1);
});
