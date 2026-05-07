'use strict';
/**
 * End-to-end test for the new `tools.list` and `tools.invoke` RPC surface
 * exposed by src/bridge/inbound_rpc.js.
 *
 * Strategy:
 *   1. Stand up a fake Kotlin-side JSON-RPC server on a free port (mirrors
 *      NodeBridgeService.kt's handler map). Some device.* tools route
 *      through this server, so we mock the methods we care about.
 *   2. Spin up the real inbound_rpc.js server on a free port.
 *   3. Reset the registry, register a synthetic mock-gateway tool to prove
 *      we can mirror upstream OpenClaw tools.
 *   4. Drive `tools.list` and `tools.invoke` over a TCP client and assert
 *      shape + behaviour.
 *
 * Run with:  node test/tools_rpc.test.js
 */
const assert = require('node:assert');
const net = require('net');
const path = require('path');
const fs = require('fs');
const os = require('os');

let fakeKotlin;
let inboundServer;
let TEST_INBOUND_PORT;
let lastKotlinCall = null;

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

function handleKotlin(sock, line) {
  const req = JSON.parse(line);
  lastKotlinCall = req;
  let result = null;
  switch (req.method) {
    case 'device.battery_status':
      result = { ok: true, result: { percent: 73, charging: true, charging_source: 'usb' } };
      break;
    case 'device.get_location':
      result = { ok: true, result: { latitude: 12.97, longitude: 77.59, accuracy_m: 14.0, source: 'fused' } };
      break;
    case 'device.clipboard_get':
      result = { ok: true, result: { text: 'pasted-text' } };
      break;
    case 'device.get_contacts':
      result = { ok: true, result: { contacts: [{ name: 'Pragati', phones: ['+91...'], emails: [] }], count: 1 } };
      break;
    case 'ax.tree':
      result = { root: { children: [] } };
      break;
    default:
      sock.write(JSON.stringify({ id: req.id, error: { message: 'unknown_kotlin_method:' + req.method } }) + '\n');
      return;
  }
  sock.write(JSON.stringify({ id: req.id, result }) + '\n');
}

function startInbound() {
  return new Promise((resolve) => {
    // The inbound_rpc module hardcodes PORT=18792, so we monkey-patch the
    // listening port by re-implementing minimal scaffolding using its
    // handler. Easier: import the module's startInboundRpc and override
    // its bind by listening on a port and dispatching via handleLine. But
    // handleLine is private, so we just spawn a TCP server that proxies
    // newline-JSON to a local instance. Since startInboundRpc binds to a
    // fixed port in the real runtime, here we just spin it up and use that
    // port (18792) - the test runner doesn't share that port with anyone.
    const inbound = require(path.join(__dirname, '..', 'src', 'bridge', 'inbound_rpc.js'));
    const tmpWorkspace = fs.mkdtempSync(path.join(os.tmpdir(), 'ben-tools-rpc-'));
    // port: 0 -> let the kernel pick a free port so tests don't collide
    // with a real running instance on 18792.
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
      sock.write(JSON.stringify({ id: 't' + Date.now(), method, params }) + '\n');
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
    setTimeout(() => reject(new Error('rpcCall timeout: ' + method)), 5000);
  });
}

async function run() {
  await startFakeKotlin();
  await startInbound();

  // Pull in the registry + register a mock OpenClaw-style "upstream" tool
  // so we can prove the round-trip works for tools that aren't device.*.
  const registry = require(path.join(__dirname, '..', 'src', 'openclaw', 'registry.js'));
  const { registerDeviceTools } = require(path.join(__dirname, '..', 'src', 'openclaw', 'device_tools.js'));

  registry.clear();
  // Mock gateway tool (mirrors what an upstream openclaw npm tool would look
  // like once the launcher mirrors it into our registry).
  registry.register({
    name: 'mock.echo',
    description: 'Echo helper for tests.',
    parameters: { type: 'object', properties: { msg: { type: 'string' } }, additionalProperties: false },
  }, async (args) => ({ ok: true, result: { echoed: args.msg || '' } }));
  registerDeviceTools();

  // ---- tools.list ----
  const listResp = await rpcCall('tools.list', {});
  assert.ok(listResp.result, 'tools.list returned no result');
  const tools = listResp.result.tools;
  assert.ok(Array.isArray(tools), 'tools is not an array');
  const names = tools.map((t) => t.name);
  // Must include the mock + at least a few device.* names.
  assert.ok(names.includes('mock.echo'), 'mock.echo missing');
  assert.ok(names.includes('device.get_location'), 'device.get_location missing');
  assert.ok(names.includes('device.battery_status'), 'device.battery_status missing');
  for (const t of tools) {
    assert.strictEqual(t.type, 'function', t.name + ' missing type=function');
    assert.ok(t.parameters && typeof t.parameters === 'object', t.name + ' missing parameters');
  }
  console.log('  tools.list: ' + tools.length + ' tools (including mock.echo and device.*)');

  // ---- tools.invoke: mock direct ----
  const echoResp = await rpcCall('tools.invoke', { name: 'mock.echo', args: { msg: 'hi-there' } });
  assert.ok(echoResp.result, 'echo no result envelope');
  assert.strictEqual(echoResp.result.ok, true);
  assert.deepStrictEqual(echoResp.result.result, { echoed: 'hi-there' });

  // ---- tools.invoke: device.battery_status (round-trips through fake Kotlin) ----
  const batt = await rpcCall('tools.invoke', { name: 'device.battery_status', args: {} });
  assert.strictEqual(batt.result.ok, true, 'battery not ok: ' + JSON.stringify(batt.result));
  assert.strictEqual(batt.result.result.percent, 73);
  assert.strictEqual(lastKotlinCall.method, 'device.battery_status');

  // ---- tools.invoke: device.get_contacts with a query ----
  const contacts = await rpcCall('tools.invoke', { name: 'device.get_contacts', args: { query: 'pra' } });
  assert.strictEqual(contacts.result.ok, true);
  assert.strictEqual(contacts.result.result.count, 1);
  assert.strictEqual(lastKotlinCall.method, 'device.get_contacts');
  assert.strictEqual(lastKotlinCall.params.query, 'pra');

  // ---- tools.invoke: unknown tool ----
  const unknown = await rpcCall('tools.invoke', { name: 'does.not.exist', args: {} });
  assert.strictEqual(unknown.result.ok, false);
  assert.match(unknown.result.error, /unknown_tool/);

  // ---- tools.invoke: missing name ----
  const noName = await rpcCall('tools.invoke', { args: {} });
  assert.strictEqual(noName.result.ok, false);
  assert.match(noName.result.error, /name_required/);

  console.log('tools_rpc.test PASS');
  inboundServer.close();
  fakeKotlin.close();
}

run().catch((e) => {
  console.error(e);
  if (inboundServer) try { inboundServer.close(); } catch (_) {}
  if (fakeKotlin) try { fakeKotlin.close(); } catch (_) {}
  process.exit(1);
});
