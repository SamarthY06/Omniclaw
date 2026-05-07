'use strict';
/**
 * Canonical-JSON + HMAC-SHA256 for envelope authentication.
 *
 * The canonical JSON used for signing MUST be byte-for-byte identical to what
 * omniclaw/proto/crypto.py emits, otherwise the HMAC won't verify across the
 * Python/JS boundary. Spec:
 *   - sorted keys
 *   - no whitespace at all
 *   - JSON separators (",", ":")
 *   - UTF-8
 *
 * Python's `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
 * matches exactly when we replicate the same output.
 */
const crypto = require('crypto');
const { signedDict } = require('./types.js');

/**
 * Deterministic JSON serializer with sorted keys and minimal separators.
 * Recurses into objects and arrays. Numbers, booleans, null pass through.
 *
 * Matches Python's json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).
 */
function canonicalJson(value) {
  return _canon(value);
}

function _canon(v) {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') {
    // Match Python json: integers without decimal, floats with shortest representation.
    if (Number.isFinite(v)) {
      if (Number.isInteger(v)) return v.toString();
      return JSON.stringify(v);
    }
    throw new Error('canonicalJson: non-finite number');
  }
  if (typeof v === 'string') return JSON.stringify(v); // JSON.stringify handles escapes.
  if (Array.isArray(v)) return '[' + v.map(_canon).join(',') + ']';
  if (typeof v === 'object') {
    const keys = Object.keys(v).sort();
    const parts = keys.map((k) => JSON.stringify(k) + ':' + _canon(v[k]));
    return '{' + parts.join(',') + '}';
  }
  throw new Error('canonicalJson: unsupported type ' + typeof v);
}

function computeHmac(secretBytes, payload) {
  return crypto.createHmac('sha256', secretBytes).update(canonicalJson(payload), 'utf8').digest('hex');
}

function verifyHmac(secretBytes, payload, hexMac) {
  const expected = computeHmac(secretBytes, payload);
  if (expected.length !== hexMac.length) return false;
  return crypto.timingSafeEqual(Buffer.from(expected, 'utf8'), Buffer.from(hexMac, 'utf8'));
}

function signEnvelope(env, secretBytes) {
  env.auth.hmac_sha256 = computeHmac(secretBytes, signedDict(env));
  return env;
}

function verifyEnvelope(env, secretBytes, { maxSkewMs = 60_000, nowMs = null } = {}) {
  if (!verifyHmac(secretBytes, signedDict(env), env.auth.hmac_sha256)) {
    return { ok: false, reason: 'hmac_mismatch' };
  }
  const now = nowMs == null ? Date.now() : nowMs;
  if (Math.abs(now - env.ts_ms) > maxSkewMs) {
    return { ok: false, reason: 'ts_outside_replay_window' };
  }
  return { ok: true, reason: null };
}

module.exports = { canonicalJson, computeHmac, verifyHmac, signEnvelope, verifyEnvelope };
