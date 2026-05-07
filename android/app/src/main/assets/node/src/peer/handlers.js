'use strict';
/**
 * RPC handlers exposed by THIS device to its peer (Mac).
 *
 * The Mac can call:
 *   peer.hello       - capability exchange
 *   peer.ping        - latency check
 *   tools.invoke     - run a phone-side tool (android_ax / android_vision)
 *   task.run         - delegate a multi-step task to the phone agent
 *   memory.read/upsert  (stub for now)
 *   handoff.screen   (stub for now)
 *
 * tools.invoke is the workhorse: it lets the Mac say "click on this on the
 * phone" or "OCR this Android screenshot" without round-tripping back to OpenAI.
 */
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const kotlin = require('../bridge/kotlin_rpc.js');

const PHONE_TOOLS = path.join(__dirname, '..', 'tools');

module.exports = function build({ workspace, role }) {
  return {
    'peer.hello': async (params) => ({
      schema_version: 1,
      device_id: params.device_id || 'unknown',
      role: role || 'android',
      caps: ['tool:android_ax', 'tool:android_vision', 'tool:peer_cli', 'tool:history'],
      schema_min: 1, schema_max: 1,
    }),

    'peer.ping': async (params) => ({
      sent_ts_ms: params.ts_ms,
      recv_ts_ms: Date.now(),
      peer_ts_ms: Date.now(),
    }),

    'tools.invoke': async (params) => {
      const toolName = params.tool_name;
      const args = params.args || {};
      // Map tool names to local entry points.
      switch (toolName) {
        case 'android_ax':
          return await invokeAxTool(args);
        case 'android_vision':
          return await invokeVisionTool(args);
        case 'history':
          return await invokeHistoryTool(workspace, args);
        default:
          return { ok: false, error: 'unknown_tool:' + toolName };
      }
    },

    'task.run': async (params, ctx) => {
      // Minimal stub: emit lifecycle, run a no-op, return completed.
      // Full agent loop hookup happens once OpenClaw is embedded (todo openclaw_embed_and_workspace).
      ctx.emitEvent({ run_id: params.run_id, type: 'lifecycle', status: 'started' });
      ctx.emitEvent({ run_id: params.run_id, type: 'lifecycle', status: 'completed', detail: 'stub' });
      return {
        run_id: params.run_id,
        status: 'completed',
        output: { note: 'task.run stub - openclaw embed pending' },
      };
    },

    'memory.read': async () => ({ items: [] }),
    'memory.upsert': async (params) => ({ accepted: (params.items || []).length, rejected: [] }),

    'handoff.screen': async () => ({ acknowledged: true, user_action_started: false }),
  };
};

async function invokeAxTool(args) {
  const sub = args.subcommand;
  switch (sub) {
    case 'tree': return await kotlin.ax.tree();
    case 'click_at': return await kotlin.ax.clickAt(args.x, args.y, args.app);
    case 'type': return await kotlin.ax.type(args.text, args.ax_id);
    case 'screenshot': return await kotlin.ax.screenshot(args.path, args.app);
    case 'launch': return await kotlin.ax.launchApp(args.package);
    case 'screen_size': return await kotlin.ax.screenSize();
    default: return { ok: false, error: 'unknown_ax_subcommand:' + sub };
  }
}

async function invokeVisionTool(args) {
  // android_vision is implemented in src/tools/android_vision.js so we call
  // it via spawn to keep the same CLI ergonomics as the Mac side.
  return await runNodeTool('android_vision.js', args);
}

async function invokeHistoryTool(workspace, args) {
  return await runNodeTool('history_cli.js', Object.assign({}, args, { workspace }));
}

function runNodeTool(file, args) {
  return new Promise((resolve) => {
    const cliArgs = [];
    if (args.subcommand) cliArgs.push(args.subcommand);
    for (const k of Object.keys(args)) {
      if (k === 'subcommand') continue;
      const v = args[k];
      if (v === true) cliArgs.push('--' + k);
      else if (v === false) {/* drop */}
      else if (v !== undefined && v !== null) {
        cliArgs.push('--' + k);
        cliArgs.push(typeof v === 'object' ? JSON.stringify(v) : String(v));
      }
    }
    const child = spawn(process.execPath, [path.join(PHONE_TOOLS, file), ...cliArgs], { env: process.env });
    let out = '', err = '';
    child.stdout.on('data', (d) => { out += d.toString(); });
    child.stderr.on('data', (d) => { err += d.toString(); });
    child.on('close', (code) => {
      try {
        resolve(JSON.parse(out.trim()));
      } catch (_) {
        resolve({ ok: false, error: err || 'tool_exited_with_code_' + code });
      }
    });
  });
}
