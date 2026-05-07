#!/usr/bin/env node
'use strict';
/**
 * android_vision - mirrors omniclaw/tools/macos_vision.py.
 *
 * Subcommands:
 *   --json-tools                              -> emit OpenAI tool schemas
 *   text-locate --image PATH --target STR     -> on-device ML Kit OCR + fuzzy match
 *   locate      --image PATH --target STR     -> OpenAI Responses API GA `computer` tool, gpt-5.5
 *   read        --image PATH --question STR   -> chat-completions multimodal, gpt-5.5
 *
 * Exits with a single JSON object printed to stdout, exit 0 on logical success
 * (ok=true) and 0 on logical failure too (ok=false). Errors are surfaced inside
 * the JSON, never via non-zero exit. This keeps the OpenClaw exec adapter happy.
 */
const fs = require('fs');
const path = require('path');
const https = require('https');
const kotlin = require('../bridge/kotlin_rpc.js');

const DEFAULT_MODEL = 'gpt-5.5';
const RESPONSES_URL = 'https://api.openai.com/v1/responses';
const CHAT_URL = 'https://api.openai.com/v1/chat/completions';

main().catch((e) => {
  emit({ ok: false, error: e && e.message ? e.message : String(e) });
});

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const sub = args._[0];
  if (args['json-tools']) return emitJsonTools();
  switch (sub) {
    case 'text-locate': return await cmdTextLocate(args);
    case 'locate': return await cmdLocate(args);
    case 'read': return await cmdRead(args);
    default: return emit({ ok: false, error: 'unknown_subcommand: ' + (sub || '(none)') });
  }
}

// -------- text-locate (free, on-device) ----------------------------------
async function cmdTextLocate(args) {
  const image = args.image;
  const target = args.target;
  if (!image || !target) return emit({ ok: false, error: 'missing --image or --target' });

  const ocr = await kotlin.ocr.recognizeText(image);
  if (!ocr.ok) return emit({ ok: false, error: ocr.error || 'ocr_failed' });

  const items = ocr.items || [];
  const targetLower = target.toLowerCase();
  let best = null;
  const screenW = parseInt(args['screen-width'] || '0', 10);
  const screenH = parseInt(args['screen-height'] || '0', 10);
  const minScore = parseFloat(args['min-score'] || '0.7');
  const maxCandidates = parseInt(args['max-candidates'] || '8', 10);
  const candidates = items
    .map((i) => {
      const sim = scoreMatch(targetLower, (i.text || '').toLowerCase());
      const conf = i.confidence != null ? Number(i.confidence) : 1.0;
      const score = Number((sim * conf).toFixed(4));
      return {
        text: i.text,
        similarity: Number(sim.toFixed(4)),
        ocr_confidence: Number(conf.toFixed(4)),
        score,
        bbox: [i.bbox.x, i.bbox.y, i.bbox.w, i.bbox.h],
      };
    })
    .filter((c) => c.similarity > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.max(1, maxCandidates));
  if (!candidates.length || candidates[0].score < minScore) {
    return emit({
      ok: true, found: false,
      image_width: ocr.image_width, image_height: ocr.image_height,
      candidates,
      min_score: minScore,
    });
  }
  best = candidates[0];
  best.bbox_obj = { x: best.bbox[0], y: best.bbox[1], w: best.bbox[2], h: best.bbox[3] };
  const cx = best.bbox_obj.x + Math.round(best.bbox_obj.w / 2);
  const cy = best.bbox_obj.y + Math.round(best.bbox_obj.h / 2);
  const out = {
    ok: true, found: true,
    matched_text: best.text, match_score: best.score,
    ocr_confidence: best.ocr_confidence,
    image_x: cx, image_y: cy,
    image_width: ocr.image_width, image_height: ocr.image_height,
    bbox: best.bbox,
    candidates,
  };
  if (screenW && screenH && ocr.image_width && ocr.image_height) {
    out.click_x = Math.round((cx / ocr.image_width) * screenW);
    out.click_y = Math.round((cy / ocr.image_height) * screenH);
    out.screen_width = screenW;
    out.screen_height = screenH;
  }
  emit(out);
}

function scoreMatch(target, text) {
  if (!text) return 0;
  if (text === target) return 1.0;
  if (text.includes(target)) return Math.min(1.0, target.length / text.length + 0.3);
  if (target.includes(text)) return Math.min(0.95, text.length / target.length + 0.25);
  // Token overlap fallback.
  const a = new Set(target.split(/\s+/).filter(Boolean));
  const b = new Set(text.split(/\s+/).filter(Boolean));
  let inter = 0;
  for (const t of a) if (b.has(t)) inter++;
  const denom = Math.max(a.size, b.size);
  return denom === 0 ? 0 : inter / denom * 0.85;
}

// -------- locate (OpenAI GA `computer` tool, gpt-5.5, two-turn handshake)
// Wire-compatible with omniclaw/tools/macos_vision.py call_vision_locate.
async function cmdLocate(args) {
  const image = args.image;
  const target = args.target;
  if (!image || !target) return emit({ ok: false, error: 'missing --image or --target' });
  const apiKey = await resolveApiKey();
  if (!apiKey) return emit({ ok: false, error: 'missing_openai_api_key' });

  let imgBytes;
  try {
    imgBytes = fs.readFileSync(image);
  } catch (e) { return emit({ ok: false, error: 'cannot read image: ' + e.message }); }
  const dims = readPngSize(imgBytes);
  if (!dims.w || !dims.h) return emit({ ok: false, error: 'cannot read image dims' });
  const b64 = imgBytes.toString('base64');
  const model = args.model || DEFAULT_MODEL;
  const maxTokens = parseInt(args['max-tokens'] || '1024', 10);

  // Turn 1: text-only. Mirrors macos_vision._build_locate_first_turn_body.
  const turn1Body = {
    model,
    tools: [{ type: 'computer' }],
    input: [{
      role: 'user',
      content: [{
        type: 'input_text',
        text: 'You are operating the user\'s Android phone. Use the computer tool to click on: ' +
              target + '. Return exactly one click action with pixel coordinates.',
      }],
    }],
    max_output_tokens: maxTokens,
  };
  let r1;
  try { r1 = await postJson(RESPONSES_URL, apiKey, turn1Body); }
  catch (e) { return emit(wrapHttpError(e)); }

  let call = firstComputerCall(r1);
  if (!call) {
    return emit({ ok: false, error: 'computer tool returned no computer_call on turn 1',
                  raw: (r1 && r1.output) || [], model: r1 && r1.model });
  }
  let click = firstClickAction(call);
  let last = r1;
  if (!click) {
    // Turn 2: feed the screenshot back. Mirrors macos_vision._build_locate_screenshot_turn_body.
    const turn2Body = {
      model,
      tools: [{ type: 'computer' }],
      previous_response_id: r1.id,
      input: [{
        type: 'computer_call_output',
        call_id: call.call_id,
        output: { type: 'computer_screenshot', image_url: 'data:image/png;base64,' + b64 },
      }],
      max_output_tokens: maxTokens,
    };
    let r2;
    try { r2 = await postJson(RESPONSES_URL, apiKey, turn2Body); }
    catch (e) { return emit(wrapHttpError(e)); }
    call = firstComputerCall(r2);
    if (!call) {
      return emit({ ok: false, error: 'computer tool returned no computer_call on turn 2',
                    raw: (r2 && r2.output) || [], model: r2 && r2.model });
    }
    click = firstClickAction(call);
    last = r2;
  }
  if (!click) {
    return emit({ ok: false, error: 'computer tool returned no click action',
                  raw: (last && last.output) || [], model: last && last.model });
  }

  const out = {
    ok: true, found: true,
    image_x: click.x, image_y: click.y,
    image_width: dims.w, image_height: dims.h,
    raw_action: click.raw,
    model: last && last.model,
    response_id: last && last.id,
  };
  const screenW = parseInt(args['screen-width'] || '0', 10);
  const screenH = parseInt(args['screen-height'] || '0', 10);
  if (screenW && screenH) {
    out.screen_width = screenW;
    out.screen_height = screenH;
    out.click_x = Math.round((click.x / dims.w) * screenW);
    out.click_y = Math.round((click.y / dims.h) * screenH);
  }
  emit(out);
}

function firstComputerCall(resp) {
  const outArr = resp && resp.output;
  if (!Array.isArray(outArr)) return null;
  for (const item of outArr) if (item && item.type === 'computer_call') return item;
  return null;
}
function firstClickAction(call) {
  // GA computer tool: the model emits actions[] inside a computer_call.
  const actions = (call && call.actions) || [];
  for (const a of actions) {
    if (a && a.type === 'click') {
      const x = Math.round(a.x), y = Math.round(a.y);
      if (Number.isFinite(x) && Number.isFinite(y)) return { x, y, raw: a };
    }
  }
  // Some responses use `action: {type: click, x, y}` instead of `actions: [...]`.
  const a = call && call.action;
  if (a && a.type === 'click') {
    const x = Math.round(a.x), y = Math.round(a.y);
    if (Number.isFinite(x) && Number.isFinite(y)) return { x, y, raw: a };
  }
  return null;
}
function wrapHttpError(e) {
  return { ok: false, error: 'http_error: ' + (e && e.message ? e.message : String(e)) };
}

// -------- read (chat-completions multimodal, gpt-5.5)
// Wire-compatible with omniclaw/tools/macos_vision.py call_vision.
async function cmdRead(args) {
  const image = args.image;
  const question = args.question;
  if (!image || !question) return emit({ ok: false, error: 'missing --image or --question' });
  const apiKey = await resolveApiKey();
  if (!apiKey) return emit({ ok: false, error: 'missing_openai_api_key' });
  let imgBytes;
  try { imgBytes = fs.readFileSync(image); }
  catch (e) { return emit({ ok: false, error: 'cannot read image: ' + e.message }); }
  if (!imgBytes.length) return emit({ ok: false, error: 'image is empty: ' + image });

  const b64 = imgBytes.toString('base64');
  const model = args.model || DEFAULT_MODEL;
  const detail = args.detail || 'auto';
  const maxTokens = parseInt(args['max-tokens'] || '1024', 10);
  const body = {
    model,
    max_tokens: maxTokens,
    messages: [{
      role: 'user',
      content: [
        { type: 'text', text: question },
        { type: 'image_url', image_url: { url: 'data:image/png;base64,' + b64, detail } },
      ],
    }],
  };
  let data;
  try { data = await postJson(CHAT_URL, apiKey, body); }
  catch (e) { return emit(wrapHttpError(e)); }
  const choices = (data && data.choices) || [];
  if (!choices.length) return emit({ ok: false, error: 'no choices in response: ' + JSON.stringify(data) });
  const msg = (choices[0] && choices[0].message) || {};
  emit({
    ok: true,
    result: typeof msg.content === 'string' ? msg.content : '',
    model: data.model || model,
    usage: data.usage || {},
    id: data.id || '',
  });
}

// -------- json-tools ------------------------------------------------------
function emitJsonTools() {
  emit({
    ok: true,
    tools: [
      {
        name: 'android_vision_read',
        description:
          'Send a PNG screenshot to a multimodal model (gpt-5.5) and return extracted text. ' +
          'Use this AFTER android_screenshot when the AX tree is blank or partial -- common for ' +
          'WebView / hybrid apps. Phrase the question to ask for structured output (e.g. JSON ' +
          'list of {sender, time, text}). Sensitivity S2: image leaves the device.',
        parameters: { type: 'object', required: ['image', 'question'], properties: {
          image: { type: 'string', description: 'Absolute path to the PNG.' },
          question: { type: 'string', description: 'What to extract. Be explicit about format.' },
          max_tokens: { type: 'integer', default: 1024 },
          detail: { type: 'string', enum: ['high', 'low', 'auto'], default: 'auto' },
          model: { type: 'string', default: 'gpt-5.5' },
        } },
        sensitivity: 'S2',
      },
      {
        name: 'android_text_locate',
        description:
          'Find a TEXT element in a PNG screenshot using on-device ML Kit OCR. Free, ~150ms, ' +
          'pixel-perfect, no image leaves the device. ALWAYS try this BEFORE android_vision_locate ' +
          'when the click target has a visible text label. Returns click coordinates ready for ' +
          'android_click_at.',
        parameters: { type: 'object', required: ['image', 'target'], properties: {
          image: { type: 'string' },
          target: { type: 'string' },
          screen_width: { type: 'integer' },
          screen_height: { type: 'integer' },
          min_score: { type: 'number', default: 0.7 },
        } },
        sensitivity: 'S0',
      },
      {
        name: 'android_vision_locate',
        description:
          "Find a UI element by natural-language description using OpenAI's GA `computer` tool " +
          '(gpt-5.5). Use this WHEN android_text_locate fails (icon-only buttons, non-text targets). ' +
          'Two-turn handshake. Sensitivity S2: image leaves the device.',
        parameters: { type: 'object', required: ['image', 'target'], properties: {
          image: { type: 'string' },
          target: { type: 'string' },
          screen_width: { type: 'integer' },
          screen_height: { type: 'integer' },
          model: { type: 'string', default: 'gpt-5.5' },
        } },
        sensitivity: 'S2',
      },
    ],
  });
}

// -------- helpers ---------------------------------------------------------
async function resolveApiKey() {
  if (process.env.OPENAI_API_KEY) return process.env.OPENAI_API_KEY;
  try {
    const r = await kotlin.secrets.openai();
    return r && r.key ? r.key : null;
  } catch (_) { return null; }
}

function readPngSize(buf) {
  if (buf.length < 24) return { w: 0, h: 0 };
  const w = buf.readUInt32BE(16);
  const h = buf.readUInt32BE(20);
  return { w, h };
}

function postJson(url, apiKey, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const data = JSON.stringify(body);
    const req = https.request({
      method: 'POST',
      hostname: u.hostname,
      path: u.pathname + u.search,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey,
        'Content-Length': Buffer.byteLength(data),
      },
    }, (res) => {
      let chunks = '';
      res.on('data', (c) => { chunks += c; });
      res.on('end', () => {
        try { resolve(JSON.parse(chunks || '{}')); } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith('--')) { out[key] = next; i++; }
      else out[key] = true;
    } else {
      out._.push(a);
    }
  }
  return out;
}

function emit(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }
