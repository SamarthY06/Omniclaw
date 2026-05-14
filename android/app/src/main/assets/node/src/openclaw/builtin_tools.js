'use strict';
/**
 * Tools that speak the OpenClaw / Android-AccessibilityService idiom but
 * don't need a separate Kotlin handler. Includes the cross-device
 * peer.delegate primitive, the full Android UI-automation surface, and the
 * vision tools that mirror the Mac-side text_locate / vision_read flow.
 *
 * Tool naming convention:
 *   peer.*    - cross-device handoff (e.g. peer.delegate to Mac)
 *   ui.*      - Android UI automation (read screen, click, type, scroll)
 *   vision.*  - on-device OCR (vision.locate_text) + multimodal LLM Q&A
 *               (vision.read_screen)
 *   device.*  - native Android APIs (registered separately in device_tools.js)
 *
 * The Realtime model's "TOOL RULE" prompt directs it to:
 *   1. Use peer.delegate for any task the user phrases as "on my Mac".
 *   2. For on-phone tasks: device.launch_app -> ui.read_screen ->
 *      ui.click / ui.click_at -> ui.type. Take ui.screenshot when the
 *      AX tree is ambiguous (Electron-style apps with custom views).
 *   3. When ui.read_screen doesn't surface what you need, take a screenshot
 *      then call vision.locate_text (OCR) for text targets, falling back to
 *      vision.read_screen (multimodal Q&A) for visual reasoning.
 */

const fs = require('fs');
const path = require('path');
const { register } = require('./registry.js');
const peerStart = require('../peer/start.js');
const kotlin = require('../bridge/kotlin_rpc.js');

function registerBuiltinTools() {
  // ============================================================
  // peer.delegate -- cross-device handoff to Mac
  // ============================================================
  register({
    name: 'peer.delegate',
    description: 'Forward a natural-language task to the user\'s paired Mac and return the result. Use this whenever the user asks for something that lives on the Mac (e.g. "what\'s the latest Teams message", "send a Slack message to X", "open Spotify and play Y on my laptop"). The Mac runs OpenClaw with full vision-driven UI automation, so the task description should be exactly what you would tell a human assistant. Returns { text: <string> }.',
    parameters: {
      type: 'object',
      properties: {
        task: {
          type: 'string',
          description: 'Natural-language description of what to do on the Mac. Example: "Open Microsoft Teams, find the BLR group, and read me the last five messages." Be specific about which app, which contact/channel, and what the success criterion is.',
        },
        timeout_ms: {
          type: 'integer',
          description: 'How long to wait (ms). Default 60000. Increase for tasks that require app launches + multiple UI hops.',
        },
      },
      required: ['task'],
      additionalProperties: false,
    },
  }, async (args) => {
    const client = peerStart.client && peerStart.client();
    if (!client) {
      return {
        ok: false,
        error: 'peer_not_paired',
        hint: 'The user has not paired this phone with a Mac yet. Tell them to pair via the app\'s onboarding step 3 and try again.',
      };
    }
    const timeoutMs = Math.max(5000, parseInt(args.timeout_ms, 10) || 60_000);
    // Method name resolution:
    //   Newer omniclaw exposes `task.run`. Older builds expose `peer.run_task`.
    //   We try the new name first, then fall back on `unknown_method`. If
    //   BOTH come back unknown_method, the Mac side does not implement
    //   cross-device task delegation at all - we surface a user-readable
    //   error rather than the raw RPC string. Mac-side migration is tracked
    //   in android/MIGRATION_TODO.md item #1.
    const candidateMethods = ['task.run', 'peer.run_task'];
    let lastErr = null;
    for (const method of candidateMethods) {
      try {
        const result = await client.call(method, { task: args.task || '' }, { timeoutMs });
        if (result && typeof result === 'object' && Object.prototype.hasOwnProperty.call(result, 'ok')) return result;
        return { ok: true, result };
      } catch (e) {
        const msg = (e && e.message) ? String(e.message) : String(e);
        lastErr = msg;
        // Try the next candidate only on unknown-method, never on
        // network/timeout errors (those would just hang the user twice).
        const isUnknownMethod = msg.includes('unknown_method') || msg.includes('method_not_found');
        if (!isUnknownMethod) break;
      }
    }
    if (lastErr && (lastErr.includes('unknown_method') || lastErr.includes('method_not_found'))) {
      return {
        ok: false,
        error: 'peer_no_task_handler',
        hint: 'The paired Mac does not expose a task-delegation handler. Tell the user their Mac needs the latest omniclaw daemon (with task.run) running. Until then, you cannot delegate to the Mac.',
      };
    }
    return {
      ok: false,
      error: 'peer_call_failed',
      hint: 'Cross-device call to the Mac failed: ' + (lastErr || 'unknown'),
    };
  });

  // ============================================================
  // ui.* -- Android UI automation surface
  // ============================================================

  register({
    name: 'ui.read_screen',
    description: 'Read the current Android accessibility tree as a structured JSON object. Use this FIRST whenever you need to interact with what is currently on screen - it tells you which elements exist, their text/contentDescription, and their ax_id (for ui.click) or center coords (for ui.click_at). Costs ~50ms. Returns { generation, count, root: <node tree> } where each node has { ax_id, role, text?, contentDescription?, cx, cy, w, h, children: [] }.',
    parameters: {
      type: 'object',
      properties: {
        max_depth: { type: 'integer', description: 'Tree depth cap. Default 12.' },
        max_elements: { type: 'integer', description: 'Hard cap on total nodes. Default 200.' },
      },
      additionalProperties: false,
    },
  }, async () => {
    try {
      const r = await kotlin.ax.tree();
      return { ok: true, result: r };
    } catch (e) {
      return { ok: false, error: 'ax_tree_failed:' + e.message };
    }
  });

  register({
    name: 'ui.click',
    description: 'Tap an on-screen UI element by visible text OR by ax_id (from a recent ui.read_screen). Use ax_id when you have it - exact, no fuzzy match. Use text when you only know what the user said ("tap on Pragati"). Returns { tapped: true, ax_id?, x?, y? }.',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'Visible text to tap. Case-insensitive substring match.' },
        ax_id: { type: 'string', description: 'Exact ax_id from ui.read_screen. Preferred when known.' },
      },
      additionalProperties: false,
    },
  }, async (args) => {
    if (args && args.ax_id) {
      try { await kotlin.ax.click(args.ax_id); return { ok: true, result: { tapped: true, ax_id: args.ax_id } }; }
      catch (e) { return { ok: false, error: 'click_by_id_failed:' + e.message }; }
    }
    const text = (args && args.text) || '';
    if (!text.trim()) return { ok: false, error: 'text_or_ax_id_required' };
    let tree;
    try { tree = await kotlin.ax.tree(); } catch (e) { return { ok: false, error: 'ax_tree_failed:' + e.message }; }
    // The Kotlin bridge returns { generation, count, root: <node> }; walk
    // from .root, not from the envelope.
    const startNode = (tree && tree.root) ? tree.root : tree;

    // v0.1.7: tiered fuzzy matching.
    //   tier 1: exact case-insensitive substring on text/contentDescription/label
    //   tier 2: token-prefix match - any visible field's first token starts
    //           with our query ("Pragati" matches "Pragati Biradar",
    //           "Pragati B" matches "Pragati Biradar"). This is the fix for
    //           the reported "couldn't find Pragati on WhatsApp Chats list"
    //           failure on Samsung S24 - real WhatsApp contact rows contain
    //           the full name in contentDescription, not just the first name.
    //   tier 3: diacritic-stripped + whitespace-collapsed substring match
    //           ("Bjorn" matches "Björn").
    // Prefer clickable matches over non-clickable when ties exist.
    const normalize = (s) => String(s || '')
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '') // strip diacritics
      .replace(/[\u00A0]/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase();
    const wanted = normalize(text);
    function fieldsOf(n) {
      return [n.text, n.contentDescription, n.label].filter(Boolean).map(String);
    }
    function tierForNode(n) {
      if (!n) return 99;
      const fields = fieldsOf(n);
      if (fields.length === 0) return 99;
      // tier 1: literal substring (lowercased)
      for (const f of fields) {
        if (f.toLowerCase().includes(text.toLowerCase())) return 1;
      }
      // tier 2: token-prefix
      for (const f of fields) {
        const tokens = f.split(/\s+/).filter(Boolean).map((t) => t.toLowerCase());
        if (tokens.length === 0) continue;
        if (tokens.some((t) => t.startsWith(text.toLowerCase()))) return 2;
        // multi-token prefix: "Pragati B" -> "Pragati B(iradar)"
        const joined = tokens.join(' ');
        if (joined.startsWith(text.toLowerCase())) return 2;
      }
      // tier 3: diacritic-stripped substring
      for (const f of fields) {
        if (normalize(f).includes(wanted)) return 3;
      }
      return 99;
    }
    const ranked = findNodes(startNode, (n) => tierForNode(n) < 99)
      .map((n) => ({ node: n, tier: tierForNode(n), clickable: !!n.clickable }))
      .sort((a, b) => {
        if (a.tier !== b.tier) return a.tier - b.tier;
        if (a.clickable !== b.clickable) return a.clickable ? -1 : 1;
        return 0;
      });
    if (ranked.length === 0) {
      return {
        ok: false,
        error: 'no_visible_match',
        hint: 'No node had text/contentDescription/label matching "' + text + '" (tried exact, token-prefix, and diacritic-stripped match). Try ui.read_screen to inspect what is visible, or ui.screenshot + vision.locate_text for visual-only matches.',
      };
    }
    const target = ranked[0].node;
    if (target.ax_id) {
      try { await kotlin.ax.click(target.ax_id); return { ok: true, result: { tapped: true, ax_id: target.ax_id } }; }
      catch (_) { /* fall through to coords */ }
    }
    if (typeof target.cx === 'number' && typeof target.cy === 'number') {
      try { await kotlin.ax.clickAt(target.cx, target.cy); return { ok: true, result: { tapped: true, x: target.cx, y: target.cy } }; }
      catch (e) { return { ok: false, error: 'click_at_failed:' + e.message }; }
    }
    return { ok: false, error: 'match_has_no_coords' };
  });

  register({
    name: 'ui.click_at',
    description: 'Tap at absolute screen pixel coordinates. PRIMARY click path for views where ui.click by text/ax_id is unreliable (custom Compose / WebView surfaces). Typical pairing: vision.locate_text -> click_x/click_y -> ui.click_at. Returns { tapped: true, x, y }.',
    parameters: {
      type: 'object',
      properties: {
        x: { type: 'number', description: 'Pixel X (0 = left edge).' },
        y: { type: 'number', description: 'Pixel Y (0 = top edge).' },
      },
      required: ['x', 'y'],
      additionalProperties: false,
    },
  }, async (args) => {
    try {
      await kotlin.ax.clickAt(args.x, args.y);
      return { ok: true, result: { tapped: true, x: args.x, y: args.y } };
    } catch (e) {
      return { ok: false, error: 'click_at_failed:' + e.message };
    }
  });

  register({
    name: 'ui.type',
    description: 'Type text into the currently focused Android input field (e.g. WhatsApp message box). The field must already be focused - typically you ui.click the field first.',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'Text to type.' },
      },
      required: ['text'],
      additionalProperties: false,
    },
  }, async (args) => {
    try {
      await kotlin.ax.type((args && args.text) || '');
      return { ok: true, result: { typed: true } };
    } catch (e) {
      return { ok: false, error: 'ui_type_failed:' + e.message };
    }
  });

  register({
    name: 'ui.scroll',
    description: 'Scroll the current screen vertically. Direction "down" = swipe upward (content moves up, you see what was below). Returns { scrolled: true }.',
    parameters: {
      type: 'object',
      properties: {
        direction: { type: 'string', enum: ['up', 'down'], description: 'Direction of content travel.' },
        amount: { type: 'integer', description: 'Logical amount, 1=one page. Default 1.' },
      },
      required: ['direction'],
      additionalProperties: false,
    },
  }, async (args) => {
    try {
      const size = await kotlin.ax.screenSize().catch(() => ({ width: 1080, height: 2400 }));
      const w = size.width || 1080;
      const h = size.height || 2400;
      const cx = Math.floor(w / 2);
      const top = Math.floor(h * 0.30);
      const bottom = Math.floor(h * 0.70);
      // direction "down" -> swipe up (drag from bottom -> top)
      const [y1, y2] = (args.direction === 'down') ? [bottom, top] : [top, bottom];
      const reps = Math.max(1, Math.min(parseInt(args.amount, 10) || 1, 5));
      for (let i = 0; i < reps; i++) {
        await kotlin.ax.scroll(cx, y1, cx, y2);
      }
      return { ok: true, result: { scrolled: true, direction: args.direction, reps } };
    } catch (e) {
      return { ok: false, error: 'ui_scroll_failed:' + e.message };
    }
  });

  register({
    name: 'ui.swipe',
    description: 'Swipe a gesture from (x1, y1) to (x2, y2). Useful for horizontal swipes (Tinder-style cards, image carousels) or precise drags. For vertical scrolling prefer ui.scroll.',
    parameters: {
      type: 'object',
      properties: {
        x1: { type: 'number' },
        y1: { type: 'number' },
        x2: { type: 'number' },
        y2: { type: 'number' },
      },
      required: ['x1', 'y1', 'x2', 'y2'],
      additionalProperties: false,
    },
  }, async (args) => {
    try {
      await kotlin.ax.swipe(args.x1, args.y1, args.x2, args.y2);
      return { ok: true, result: { swiped: true } };
    } catch (e) {
      return { ok: false, error: 'ui_swipe_failed:' + e.message };
    }
  });

  register({
    name: 'ui.screenshot',
    description: 'Capture a screenshot of the current Android screen and return its on-disk path. Required before vision.locate_text / vision.read_screen. Returns { path, width, height }.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  }, async () => {
    try {
      const r = await kotlin.ax.screenshot();
      return { ok: true, result: r };
    } catch (e) {
      return { ok: false, error: 'screenshot_failed:' + e.message };
    }
  });

  register({
    name: 'ui.screen_size',
    description: 'Return the screen size in pixels as { width, height }. Useful when you need to plan ui.click_at coords from a description ("tap the bottom-right corner").',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  }, async () => {
    try {
      const r = await kotlin.ax.screenSize();
      return { ok: true, result: r };
    } catch (e) {
      return { ok: false, error: 'screen_size_failed:' + e.message };
    }
  });

  register({
    name: 'ui.focus_app',
    description: 'Bring an already-running Android app to the foreground (or launch it). Same as device.launch_app but with a more natural name when the user just says "open WhatsApp".',
    parameters: {
      type: 'object',
      properties: {
        package: { type: 'string', description: 'Android package id, e.g. "com.whatsapp".' },
      },
      required: ['package'],
      additionalProperties: false,
    },
  }, async (args) => {
    try {
      const r = await kotlin.ax.focus(args.package);
      return { ok: true, result: r };
    } catch (e) {
      return { ok: false, error: 'focus_failed:' + e.message };
    }
  });

  // ============================================================
  // vision.* -- on-device OCR + multimodal Q&A
  // ============================================================

  register({
    name: 'vision.locate_text',
    description: 'Find a text element on the current screen via on-device OCR (ML Kit). Use this when ui.read_screen / ui.click can\'t find what you want by accessibility text - common for Compose / WebView / Electron-style apps where the AX tree is sparse. Pipeline: ui.screenshot -> vision.locate_text. Returns { found: true, text, confidence, bbox: {x,y,w,h}, click_x, click_y } ready for ui.click_at. On miss returns { ok:false, error:"no_text_match" }.',
    parameters: {
      type: 'object',
      properties: {
        target: {
          type: 'string',
          description: 'Text to find. Substring + lowercase fuzzy match. Example: "Pragati" matches "Pragati Biradar".',
        },
        min_score: {
          type: 'number',
          description: 'Minimum (similarity * confidence) to consider a match. Default 0.6.',
        },
      },
      required: ['target'],
      additionalProperties: false,
    },
  }, async (args) => {
    let shot;
    try { shot = await kotlin.ax.screenshot(); } catch (e) { return { ok: false, error: 'screenshot_failed:' + e.message }; }
    if (!shot || !shot.path) return { ok: false, error: 'screenshot_no_path' };
    let ocr;
    try { ocr = await kotlin.ocr.recognizeText(shot.path); } catch (e) { return { ok: false, error: 'ocr_failed:' + e.message }; }
    const items = (ocr && ocr.items) || [];
    if (items.length === 0) return { ok: false, error: 'no_text_on_screen' };
    const target = String(args.target || '').toLowerCase().trim();
    const minScore = (typeof args.min_score === 'number') ? args.min_score : 0.6;
    let best = null; let bestScore = -1;
    for (const it of items) {
      const text = String(it.text || '').toLowerCase();
      if (!text) continue;
      let sim = 0;
      if (text === target) sim = 1.0;
      else if (text.includes(target)) sim = 0.85;
      else if (target.includes(text)) sim = 0.75;
      else sim = jaccardSimilarity(text, target);
      const score = sim * (typeof it.confidence === 'number' ? it.confidence : 0.9);
      if (score > bestScore) { bestScore = score; best = it; }
    }
    if (!best || bestScore < minScore) {
      return { ok: false, error: 'no_text_match', hint: 'Increase fuzziness with min_score: 0.4, take a fresh screenshot, or fall back to vision.read_screen for visual reasoning.', best_score: Number(bestScore.toFixed(3)) };
    }
    const bbox = best.bbox || {};
    const clickX = (typeof bbox.x === 'number' && typeof bbox.w === 'number') ? bbox.x + bbox.w / 2 : null;
    const clickY = (typeof bbox.y === 'number' && typeof bbox.h === 'number') ? bbox.y + bbox.h / 2 : null;
    return {
      ok: true,
      result: {
        found: true,
        text: best.text,
        confidence: best.confidence,
        score: Number(bestScore.toFixed(3)),
        bbox,
        click_x: clickX,
        click_y: clickY,
      },
    };
  });

  register({
    name: 'vision.read_screen',
    description: 'Take a screenshot and ask a multimodal LLM (tries gpt-5.5 first, falls back to gpt-4o, then gpt-4o-mini) to extract / describe / answer a question about what is on screen. Use this when text-only OCR is not enough (e.g. "what color is the chart?", "list every notification with sender + preview"). Sensitivity: image leaves the device, sent to OpenAI. Returns { answer, model, fallback_chain }.',
    parameters: {
      type: 'object',
      properties: {
        question: {
          type: 'string',
          description: 'What to extract / describe. Be explicit about format (e.g. "Return a JSON array of {sender, time, text} for every visible message").',
        },
        max_tokens: { type: 'integer', description: 'Cap on response length. Default 600.' },
        detail: { type: 'string', enum: ['high', 'low', 'auto'], description: 'Vision detail level. high for tiny text. Default auto.' },
      },
      required: ['question'],
      additionalProperties: false,
    },
  }, async (args) => {
    let shot;
    try { shot = await kotlin.ax.screenshot(); } catch (e) { return { ok: false, error: 'screenshot_failed:' + e.message }; }
    if (!shot || !shot.path) return { ok: false, error: 'screenshot_no_path' };
    let png;
    try { png = fs.readFileSync(shot.path); } catch (e) { return { ok: false, error: 'screenshot_read_failed:' + e.message }; }
    const b64 = png.toString('base64');
    let key;
    try { const s = await kotlin.secrets.openai(); key = s && s.key; } catch (e) { return { ok: false, error: 'secrets_unavailable:' + e.message }; }
    if (!key) return { ok: false, error: 'no_openai_key', hint: 'User must paste their OpenAI key in onboarding step 2 / settings.' };
    const fetchFn = (typeof fetch === 'function') ? fetch : null;
    if (!fetchFn) return { ok: false, error: 'fetch_unavailable_in_runtime' };
    // Vision-model fallback chain. Order: head = newest/highest-quality,
    // tail = oldest/cheapest. We walk down the list on transient or
    // model-availability errors and return the first answer that comes
    // back successfully. Order chosen to match android_vision.js + the
    // rest of the Mac-side fleet so behaviour is consistent across devices.
    const models = ['gpt-5.5', 'gpt-4o', 'gpt-4o-mini'];
    const maxTokens = Math.min(2000, parseInt(args.max_tokens, 10) || 600);
    const detail = args.detail || 'auto';
    const question = args.question || 'Describe this screen.';
    const tried = [];

    /**
     * Per-model output-budget param. The gpt-5* and o* families rejected
     * the legacy `max_tokens` field with HTTP 400 starting around 2025-09;
     * they require `max_completion_tokens` instead. The gpt-4o family
     * still accepts `max_tokens`. We pick the right one based on the
     * model name prefix; if the API surprises us we recover by retrying
     * with the other one (see "Unsupported parameter" handling below).
     */
    function tokenBudgetParam(model) {
      const m = (model || '').toLowerCase();
      if (m.startsWith('gpt-5') || m.startsWith('o1') || m.startsWith('o3') || m.startsWith('o4')) {
        return 'max_completion_tokens';
      }
      return 'max_tokens';
    }

    async function callModel(model, budgetParam) {
      const body = {
        model,
        [budgetParam]: maxTokens,
        messages: [{
          role: 'user',
          content: [
            { type: 'text', text: question },
            { type: 'image_url', image_url: { url: 'data:image/png;base64,' + b64, detail } },
          ],
        }],
      };
      try {
        const resp = await fetchFn('https://api.openai.com/v1/chat/completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key },
          body: JSON.stringify(body),
        });
        return { resp };
      } catch (e) {
        return { networkError: e.message || String(e) };
      }
    }

    for (const model of models) {
      let budgetParam = tokenBudgetParam(model);
      let { resp, networkError } = await callModel(model, budgetParam);
      if (networkError) {
        tried.push({ model, network_error: networkError });
        continue;
      }
      // Defense-in-depth for our per-model token-budget guess: if OpenAI
      // says "Unsupported parameter: 'max_tokens'" or vice-versa, retry the
      // SAME model with the other param name. This survives future model
      // releases that change which family they belong to.
      if (resp.status === 400) {
        const errText = await resp.text().catch(() => '');
        const wantsCompletion = errText.includes("Unsupported parameter: 'max_tokens'");
        const wantsLegacy = errText.includes("Unsupported parameter: 'max_completion_tokens'");
        if (wantsCompletion || wantsLegacy) {
          const altParam = wantsCompletion ? 'max_completion_tokens' : 'max_tokens';
          tried.push({ model, switched_param_to: altParam });
          const alt = await callModel(model, altParam);
          if (alt.networkError) {
            tried.push({ model, network_error: alt.networkError });
            continue;
          }
          resp = alt.resp;
        } else {
          // Some other 400 - try next model rather than re-issuing the
          // exact same broken request.
          tried.push({ model, status: 400, body: errText.slice(0, 200) });
          continue;
        }
      }
      if (resp.ok) {
        const json = await resp.json().catch(() => null);
        const answer = json && json.choices && json.choices[0] && json.choices[0].message && json.choices[0].message.content;
        if (!answer) {
          tried.push({ model, error: 'no_content_in_response' });
          continue;
        }
        return { ok: true, result: { answer, model, fallback_chain: tried } };
      }
      const status = resp.status;
      const errText = await resp.text().catch(() => '');
      tried.push({ model, status, body: errText.slice(0, 200) });
      // 401 / 403: bad API key. Trying another model won't help.
      if (status === 401 || status === 403) {
        return {
          ok: false,
          error: 'vision_auth_failed',
          status,
          hint: 'OpenAI rejected the API key. Tell the user to refresh their key in settings.',
          attempts: tried,
        };
      }
      // 404 model_not_found / 429 rate limit / 5xx server error: walk down
      // the chain (other models may have different availability).
      if (status === 404 || status === 429 || (status >= 500 && status < 600)) {
        continue;
      }
      // Other unknown 4xx (invalid request, oversize image, etc.): no point
      // retrying with another model on the same input.
      return {
        ok: false,
        error: 'vision_http_' + status,
        hint: errText.slice(0, 300),
        attempts: tried,
      };
    }
    return {
      ok: false,
      error: 'vision_all_models_failed',
      hint: 'All vision models failed. The user may be offline, rate-limited, or out of OpenAI quota.',
      attempts: tried,
    };
  });
}

// ----------------- helpers -----------------

function findNodes(node, predicate, acc = []) {
  if (!node || typeof node !== 'object') return acc;
  if (predicate(node)) acc.push(node);
  const kids = node.children || node.nodes || [];
  for (const k of kids) findNodes(k, predicate, acc);
  return acc;
}

function jaccardSimilarity(a, b) {
  if (!a || !b) return 0;
  const A = new Set(a.split(/\s+/).filter(Boolean));
  const B = new Set(b.split(/\s+/).filter(Boolean));
  if (A.size === 0 || B.size === 0) return 0;
  let inter = 0;
  for (const x of A) if (B.has(x)) inter++;
  return inter / (A.size + B.size - inter);
}

module.exports = { registerBuiltinTools };
