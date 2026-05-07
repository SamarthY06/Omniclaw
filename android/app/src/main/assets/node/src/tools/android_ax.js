#!/usr/bin/env node
'use strict';
/**
 * android_ax CLI - mirrors omniclaw/tools/macos_ax.py.
 * Backed entirely by JSON-RPC into BenAccessibilityService.
 */
const kotlin = require('../bridge/kotlin_rpc.js');

main().catch((e) => emit({ ok: false, error: String(e && e.message ? e.message : e) }));

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args['json-tools']) return emitJsonTools();
  const sub = args._[0];
  switch (sub) {
    case 'tree': return emit(await kotlin.ax.tree());
    case 'click': return emit(await kotlin.ax.click(args['ax-id'] || args['index']));
    case 'click-at': return emit(await kotlin.ax.clickAt(int(args.x), int(args.y), args.app));
    case 'type': return emit(await kotlin.ax.type(args.text, args['ax-id']));
    case 'swipe': return emit(await kotlin.ax.swipe(int(args.x1), int(args.y1), int(args.x2), int(args.y2)));
    case 'scroll': return emit(await kotlin.ax.scroll(int(args.x1), int(args.y1), int(args.x2), int(args.y2)));
    case 'focus': return emit(await kotlin.ax.focus(args.package));
    case 'launch': return emit(await kotlin.ax.launchApp(args.package));
    case 'screen-size': return emit(await kotlin.ax.screenSize());
    case 'screenshot': return emit(await kotlin.ax.screenshot(args.path, args.app));
    default: return emit({ ok: false, error: 'unknown_subcommand:' + sub });
  }
}

function emitJsonTools() {
  emit({
    tools: [
      { name: 'android_ax_tree', description: 'Get the AX tree of the foreground app.', parameters: { type: 'object', properties: {} } },
      { name: 'android_ax_click_at', description: 'Tap at a pixel coordinate.', parameters: { type: 'object', required: ['x', 'y'], properties: { x: { type: 'integer' }, y: { type: 'integer' }, app: { type: 'string' } } } },
      { name: 'android_ax_type', description: 'Type text into the focused editable.', parameters: { type: 'object', required: ['text'], properties: { text: { type: 'string' }, 'ax-id': { type: 'string' } } } },
      { name: 'android_ax_screenshot', description: 'Take a screencap PNG via MediaProjection.', parameters: { type: 'object', properties: { path: { type: 'string' }, app: { type: 'string' } } } },
      { name: 'android_ax_launch', description: 'Launch an app by package name.', parameters: { type: 'object', required: ['package'], properties: { package: { type: 'string' } } } },
    ],
  });
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const k = a.slice(2);
      const n = argv[i + 1];
      if (n !== undefined && !n.startsWith('--')) { out[k] = n; i++; } else out[k] = true;
    } else out._.push(a);
  }
  return out;
}
function int(v) { return parseInt(v, 10); }
function emit(o) { process.stdout.write(JSON.stringify(o) + '\n'); }
