'use strict';
/**
 * Wire-format constants and helpers. Direct port of omniclaw/proto/types.py.
 *
 * The Python side validates with pydantic; here we use plain object shape
 * checks (the wire format is what matters, not the in-memory class).
 */

const SCHEMA_VERSION = 1;
const SCHEMA_MIN = 1;
const SCHEMA_MAX = 1;

/**
 * Build the dict that's HMAC-signed. MUST match Envelope.signed_dict() on the
 * Python side - excludes auth.hmac_sha256, includes auth.device_id under
 * "device_id" key.
 */
function signedDict(env) {
  return {
    v: env.v,
    id: env.id,
    kind: env.kind,
    method: env.method,
    ts_ms: env.ts_ms,
    params: env.params,
    device_id: env.auth.device_id,
  };
}

function newEnvelope({ kind, method, params, deviceId, requestId }) {
  return {
    v: SCHEMA_VERSION,
    id: requestId || randomUuid(),
    kind,
    method,
    ts_ms: Date.now(),
    params: params || {},
    auth: { device_id: deviceId, hmac_sha256: '0'.repeat(64) },
  };
}

function randomUuid() {
  // RFC 4122 v4-ish, sufficient for envelope ids; we don't gate auth on this.
  const crypto = require('crypto');
  const b = crypto.randomBytes(16);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = b.toString('hex');
  return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' +
         hex.slice(16, 20) + '-' + hex.slice(20, 32);
}

module.exports = { SCHEMA_VERSION, SCHEMA_MIN, SCHEMA_MAX, signedDict, newEnvelope, randomUuid };
