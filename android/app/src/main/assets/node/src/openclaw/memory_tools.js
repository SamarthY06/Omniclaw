'use strict';
/**
 * Durable on-device memory for Ben (Android), mirrors the Mac OpenClaw
 * `memory_search` / `memory_get` / `memory_set` tool family.
 *
 * Storage model: a single JSON file at <workspace>/memory.json with shape
 *   {
 *     "<key>": {
 *       value: any,            // the user-supplied value (string|number|object|array|bool)
 *       updated_at: <ms>,      // last write time
 *       created_at: <ms>,      // first write time
 *       tags: [<string>, ...]  // optional, for grouping
 *     },
 *     ...
 *   }
 *
 * Why a single JSON file:
 *   * Total store stays small (kilobytes); we expect O(100) facts per user.
 *   * Atomic write via tmp+rename means we never corrupt the store on crash.
 *   * No native dep, works in nodejs-mobile out of the box.
 *
 * Why an in-process cache: the Realtime model can call memory.get back-to-back
 * during a turn; reading the file each time is wasteful.
 *
 * USER.md: separate from memory.json. We expose `memory.user_facts` so the
 * model can read the curated, human-edited facts file at session start, and
 * `memory.append_user_facts` so the model can persist a new fact the user
 * stated mid-conversation ("I live in Whitefield"). USER.md is NEVER auto-
 * trimmed; users are free to hand-edit it.
 *
 * All handlers return a plain object; the registry wraps them as
 * { ok: true, result: ... } unless the handler returns its own { ok: false }.
 */

const fs = require('fs');
const path = require('path');
const { register } = require('./registry.js');

const MEMORY_FILE = 'memory.json';
const USER_FILE = 'USER.md';
const MAX_KEY_LEN = 200;
const MAX_VALUE_BYTES = 16 * 1024; // 16 KB per entry, generous for free-form notes
const SEARCH_DEFAULT_LIMIT = 10;
const SEARCH_MAX_LIMIT = 50;

// Resolved on first use from BEN_WORKSPACE; the launcher passes workspace into
// startOpenClaw which calls registerMemoryTools(workspace), but we also fall
// back to the env var so the module is usable in tests and from the inbound
// RPC path before the launcher runs.
let _workspace = null;
let _cache = null;       // parsed JSON (object) or null when stale
let _cacheReadAt = 0;    // mtimeMs we last read

function _resolveWorkspace() {
  if (_workspace) return _workspace;
  const env = process.env.BEN_WORKSPACE;
  if (env && env.length > 0) return env;
  return null;
}

function _memoryPath() {
  const ws = _resolveWorkspace();
  if (!ws) throw new Error('memory_workspace_unset');
  return path.join(ws, MEMORY_FILE);
}

function _userPath() {
  const ws = _resolveWorkspace();
  if (!ws) throw new Error('memory_workspace_unset');
  return path.join(ws, USER_FILE);
}

function _loadStore() {
  const p = _memoryPath();
  let mtime = 0;
  try { mtime = fs.statSync(p).mtimeMs; } catch (_) { mtime = 0; }
  if (_cache && mtime === _cacheReadAt) return _cache;
  let parsed = {};
  try {
    const raw = fs.readFileSync(p, 'utf8');
    parsed = JSON.parse(raw || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) parsed = {};
  } catch (e) {
    if (e && e.code === 'ENOENT') {
      parsed = {};
    } else {
      console.warn('[memory] corrupt memory.json, starting empty:', e && e.message);
      parsed = {};
    }
  }
  _cache = parsed;
  _cacheReadAt = mtime;
  return _cache;
}

function _saveStore(store) {
  const p = _memoryPath();
  const tmp = p + '.tmp';
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(tmp, JSON.stringify(store, null, 2), 'utf8');
  fs.renameSync(tmp, p);
  try { _cacheReadAt = fs.statSync(p).mtimeMs; } catch (_) {}
  _cache = store;
}

function _normalizeKey(k) {
  if (typeof k !== 'string') return null;
  const t = k.trim();
  if (!t) return null;
  if (t.length > MAX_KEY_LEN) return null;
  return t;
}

function _stringify(v) {
  try {
    if (typeof v === 'string') return v;
    return JSON.stringify(v);
  } catch (_) {
    return String(v);
  }
}

function _byteLen(s) {
  return Buffer.byteLength(s, 'utf8');
}

// ----- Tool handlers -----

async function memorySet(args) {
  const key = _normalizeKey(args && args.key);
  if (!key) return { ok: false, error: 'invalid_key' };
  if (!Object.prototype.hasOwnProperty.call(args || {}, 'value')) {
    return { ok: false, error: 'value_required' };
  }
  const value = args.value;
  const serialized = _stringify(value);
  if (_byteLen(serialized) > MAX_VALUE_BYTES) {
    return { ok: false, error: 'value_too_large', max_bytes: MAX_VALUE_BYTES };
  }
  const tags = Array.isArray(args.tags)
    ? args.tags.map((t) => String(t)).filter((t) => t.length > 0).slice(0, 16)
    : [];
  const store = _loadStore();
  const now = Date.now();
  const existing = store[key];
  store[key] = {
    value,
    updated_at: now,
    created_at: existing && existing.created_at ? existing.created_at : now,
    tags: tags.length > 0 ? tags : (existing && existing.tags) || [],
  };
  _saveStore(store);
  return { saved: true, key, updated_at: now };
}

async function memoryGet(args) {
  const key = _normalizeKey(args && args.key);
  if (!key) return { ok: false, error: 'invalid_key' };
  const store = _loadStore();
  const entry = store[key];
  if (!entry) return { ok: true, found: false, key };
  return {
    ok: true,
    found: true,
    key,
    value: entry.value,
    updated_at: entry.updated_at,
    created_at: entry.created_at,
    tags: entry.tags || [],
  };
}

async function memorySearch(args) {
  const q = (args && typeof args.query === 'string') ? args.query.trim().toLowerCase() : '';
  const requestedLimit = parseInt(args && args.limit, 10);
  const limit = Math.min(
    SEARCH_MAX_LIMIT,
    Math.max(1, Number.isFinite(requestedLimit) ? requestedLimit : SEARCH_DEFAULT_LIMIT),
  );
  const tagFilter = (args && typeof args.tag === 'string') ? args.tag.trim().toLowerCase() : '';
  const store = _loadStore();
  const all = Object.entries(store);
  // Score: 2 if query found in key, 1 if found in stringified value, 0 otherwise.
  // Empty query returns the most-recently-updated entries (so memory.search()
  // with no args becomes a "what do you remember about me" probe).
  const scored = [];
  for (const [k, v] of all) {
    if (!v || typeof v !== 'object') continue;
    if (tagFilter && !(Array.isArray(v.tags) && v.tags.some((t) => String(t).toLowerCase() === tagFilter))) {
      continue;
    }
    let score = 0;
    if (q) {
      const kl = k.toLowerCase();
      const vs = _stringify(v.value).toLowerCase();
      if (kl.includes(q)) score = 2;
      else if (vs.includes(q)) score = 1;
      if (score === 0) continue;
    } else {
      score = 1;
    }
    scored.push({ key: k, score, updated_at: v.updated_at || 0, value: v.value, tags: v.tags || [] });
  }
  scored.sort((a, b) => (b.score - a.score) || (b.updated_at - a.updated_at));
  return { matches: scored.slice(0, limit), total: scored.length };
}

async function memoryList(args) {
  const prefix = (args && typeof args.prefix === 'string') ? args.prefix : '';
  const requestedLimit = parseInt(args && args.limit, 10);
  const limit = Math.min(
    SEARCH_MAX_LIMIT,
    Math.max(1, Number.isFinite(requestedLimit) ? requestedLimit : SEARCH_DEFAULT_LIMIT),
  );
  const store = _loadStore();
  const keys = Object.keys(store)
    .filter((k) => (prefix ? k.startsWith(prefix) : true))
    .sort()
    .slice(0, limit);
  return { keys, total: keys.length };
}

async function memoryDelete(args) {
  const key = _normalizeKey(args && args.key);
  if (!key) return { ok: false, error: 'invalid_key' };
  const store = _loadStore();
  if (!Object.prototype.hasOwnProperty.call(store, key)) {
    return { deleted: false, key };
  }
  delete store[key];
  _saveStore(store);
  return { deleted: true, key };
}

async function memoryUserFacts() {
  let p;
  try { p = _userPath(); } catch (_) { return { ok: true, facts: '', present: false }; }
  try {
    const raw = fs.readFileSync(p, 'utf8');
    return { ok: true, facts: raw, present: true, path: p };
  } catch (e) {
    if (e && e.code === 'ENOENT') return { ok: true, facts: '', present: false, path: p };
    return { ok: false, error: e && e.message ? e.message : String(e) };
  }
}

async function memoryAppendUserFacts(args) {
  const text = (args && typeof args.text === 'string') ? args.text.trim() : '';
  if (!text) return { ok: false, error: 'text_required' };
  const heading = (args && typeof args.heading === 'string') ? args.heading.trim() : '';
  let p;
  try { p = _userPath(); } catch (e) { return { ok: false, error: 'memory_workspace_unset' }; }
  fs.mkdirSync(path.dirname(p), { recursive: true });
  let body = '';
  try { body = fs.readFileSync(p, 'utf8'); } catch (_) { body = ''; }
  const stamp = new Date().toISOString();
  const block = (heading ? `\n## ${heading}\n` : '') + `\n- [${stamp}] ${text}\n`;
  if (!body.endsWith('\n') && body.length > 0) body += '\n';
  body += block;
  fs.writeFileSync(p, body, 'utf8');
  return { appended: true, path: p, bytes: _byteLen(body) };
}

function registerMemoryTools(workspace) {
  if (workspace && !_workspace) _workspace = workspace;

  register({
    name: 'memory.set',
    description: 'Save a fact, preference, or piece of state in durable on-device memory. The value persists across sessions and reboots. Use this whenever the user says something you should remember next time: home address, default delivery address, favourite cuisine, names/numbers of friends, work hours, an in-progress task, the result of a recent tool call you may want to recall later. Choose a stable, snake-case key like "home_address", "favourite_biryani_place", "last_swiggy_order". Returns { saved: true, key, updated_at }.',
    parameters: {
      type: 'object',
      properties: {
        key: { type: 'string', description: 'Stable identifier for the fact (snake_case, <=200 chars). Reuse the same key to update an existing fact.' },
        value: { description: 'The fact to remember. Can be a string, number, object, array, or boolean.' },
        tags: { type: 'array', items: { type: 'string' }, description: 'Optional tags for grouping (e.g. ["address", "personal"]). Up to 16.' },
      },
      required: ['key', 'value'],
      additionalProperties: false,
    },
  }, memorySet);

  register({
    name: 'memory.get',
    description: 'Read a previously-saved fact by exact key. Use this when you already know the key (e.g. "home_address", "favourite_biryani_place"). Returns { found: true, value } or { found: false }. Prefer memory.search if you only have a fuzzy idea of what to look for.',
    parameters: {
      type: 'object',
      properties: {
        key: { type: 'string', description: 'Exact key the fact was saved under.' },
      },
      required: ['key'],
      additionalProperties: false,
    },
  }, memoryGet);

  register({
    name: 'memory.search',
    description: 'Search durable memory by substring across both keys and values. Call this AT THE START of every conversation with the empty-query form `memory.search({})` to load the most-recent N facts into your context, then again with a focused query when the user mentions something you might already know about ("biryani like last Friday" -> memory.search({query: "biryani"})). Returns { matches: [{key, value, score, updated_at, tags}, ...], total }.',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Substring to find (case-insensitive). Empty string means "give me the most-recently-updated facts".' },
        tag: { type: 'string', description: 'Optional tag filter; only entries with this tag are returned.' },
        limit: { type: 'integer', description: 'Max matches to return. Default 10, max 50.' },
      },
      additionalProperties: false,
    },
  }, memorySearch);

  register({
    name: 'memory.list',
    description: 'List all memory keys (optionally filtered by prefix). Useful when the user asks "what do you remember about me" - call memory.list, then memory.get on each interesting key. Returns { keys: [...], total }.',
    parameters: {
      type: 'object',
      properties: {
        prefix: { type: 'string', description: 'Only keys starting with this prefix are returned.' },
        limit: { type: 'integer', description: 'Max keys to return. Default 10, max 50.' },
      },
      additionalProperties: false,
    },
  }, memoryList);

  register({
    name: 'memory.delete',
    description: 'Forget a fact by exact key. Use this when the user explicitly asks you to forget something ("forget my old address"). Returns { deleted: true|false, key }.',
    parameters: {
      type: 'object',
      properties: {
        key: { type: 'string', description: 'Exact key to delete.' },
      },
      required: ['key'],
      additionalProperties: false,
    },
  }, memoryDelete);

  register({
    name: 'memory.user_facts',
    description: 'Read the canonical user-facts file (USER.md) the user has hand-curated. This contains identity, important contacts, addresses, default payment method, devices, etc. The model is auto-fed this at session start, but you may re-read it mid-conversation if the user references something you forgot. Returns { facts: <markdown string>, present: true|false }.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  }, memoryUserFacts);

  register({
    name: 'memory.append_user_facts',
    description: 'Append a new line of user facts to USER.md. Use this when the user states a personal fact you should remember LONG-term and surface to every future session ("my home is at 21 Whitefield, Bengaluru", "my partner is Pragati", "use HDFC card for grocery"). Pass `text` (one short sentence) and optionally `heading` (e.g. "Addresses", "Contacts"). For volatile / one-off state prefer memory.set. Returns { appended: true, path, bytes }.',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'The fact in one short sentence.' },
        heading: { type: 'string', description: 'Optional H2 heading to start a new section (e.g. "Addresses").' },
      },
      required: ['text'],
      additionalProperties: false,
    },
  }, memoryAppendUserFacts);
}

module.exports = {
  registerMemoryTools,
  // Exported for tests:
  _memorySet: memorySet,
  _memoryGet: memoryGet,
  _memorySearch: memorySearch,
  _memoryList: memoryList,
  _memoryDelete: memoryDelete,
  _memoryUserFacts: memoryUserFacts,
  _memoryAppendUserFacts: memoryAppendUserFacts,
};
