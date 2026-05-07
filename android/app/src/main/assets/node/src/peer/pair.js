'use strict';
/**
 * QR-based pairing payload encoding and parsing.
 *
 * Direct port of omniclaw/peer/pair.py. The on-disk JSON shapes and the URI
 * format MUST stay identical so a Mac-generated QR scanned on the phone yields
 * the same field values as the Python parser.
 */
const querystring = require('querystring');

const URI_SCHEME = 'jarvis://pair';

function payloadToUri(p) {
  const qs = querystring.stringify({
    host: p.host,
    port: String(p.port),
    fp: p.fingerprint || '',
    secret: p.secret_b64,
    role: p.role,
    id: p.device_id,
    v: String(p.schema_version || 1),
  });
  return URI_SCHEME + '?' + qs;
}

function payloadFromUri(uri) {
  if (!uri.startsWith(URI_SCHEME)) {
    throw new Error('not a ' + URI_SCHEME + ' URI: ' + uri);
  }
  const q = uri.indexOf('?');
  const params = querystring.parse(q === -1 ? '' : uri.slice(q + 1));
  const required = (k) => {
    const v = params[k];
    if (v === undefined || v === '') throw new Error('pairing URI missing ' + k);
    return Array.isArray(v) ? v[0] : v;
  };
  const optional = (k, dflt = '') => {
    const v = params[k];
    if (v === undefined || v === '') return dflt;
    return Array.isArray(v) ? v[0] : v;
  };
  return {
    host: required('host'),
    port: parseInt(required('port'), 10),
    fingerprint: optional('fp'),
    secret_b64: required('secret'),
    role: required('role'),
    device_id: required('id'),
    schema_version: parseInt(optional('v', '1'), 10),
  };
}

function newPairingPayload({ host, port, role, deviceId, fingerprint = '' }) {
  const crypto = require('crypto');
  const secret = crypto.randomBytes(32);
  return {
    host,
    port,
    fingerprint,
    secret_b64: secret.toString('base64url'),
    role,
    device_id: deviceId,
    schema_version: 1,
  };
}

function sharedSecretBytes(record) {
  return Buffer.from(record.shared_secret_b64, 'base64url');
}

module.exports = { URI_SCHEME, payloadToUri, payloadFromUri, newPairingPayload, sharedSecretBytes };
