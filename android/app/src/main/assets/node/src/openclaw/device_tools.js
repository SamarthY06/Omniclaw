'use strict';
/**
 * Native Android device-API tools. Each tool is a thin wrapper around a
 * matching JSON-RPC method on NodeBridgeService.kt's handler map (port 18791,
 * see kotlin_rpc.js). The Kotlin side owns FusedLocation / ContactsContract
 * / TelephonyManager / ClipboardManager / BatteryManager.
 *
 * Permission UX: when a Kotlin handler returns
 *   { ok: false, error: 'permission_not_granted', permission: '...' }
 * it ALSO fires an intent to PermissionGateActivity which prompts the user.
 * The model receives the error and naturally tells the user to allow the
 * permission; on retry the tool succeeds. We do NOT block here waiting for
 * the user - that would freeze the conversation.
 */

const { register } = require('./registry.js');
const kotlin = require('../bridge/kotlin_rpc.js');

// All device.* methods round-trip via kotlin_rpc.call(method, params, opts).
// We expose .call directly because the convenience surface in kotlin_rpc
// only covers ax / ocr / secrets / ping. Importing the underlying transport
// here keeps the bridge layer thin.
const _bridgeCall = kotlin.__call || kotlin.call;
const net = require('net');

const HOST = '127.0.0.1';
const PORT = parseInt(process.env.BEN_RPC_PORT || '18791', 10);

let _idSeq = 0;

function bridgeRpc(method, params = {}, { timeoutMs = 10_000 } = {}) {
  return new Promise((resolve, reject) => {
    const sock = net.createConnection({ host: HOST, port: PORT }, () => {
      const id = 'd' + (_idSeq++);
      sock.write(JSON.stringify({ id, method, params }) + '\n');
    });
    let buf = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try { sock.destroy(); } catch (_) {}
      reject(new Error('device_rpc_timeout:' + method));
    }, timeoutMs);
    sock.on('data', (chunk) => {
      buf += chunk.toString('utf8');
      const nl = buf.indexOf('\n');
      if (nl === -1) return;
      const line = buf.slice(0, nl);
      try {
        const parsed = JSON.parse(line);
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try { sock.end(); } catch (_) {}
        if (parsed.error) reject(new Error(parsed.error.message || 'rpc_error'));
        else resolve(parsed.result || {});
      } catch (e) {
        if (!settled) { settled = true; clearTimeout(timer); reject(e); }
      }
    });
    sock.on('error', (e) => {
      if (!settled) { settled = true; clearTimeout(timer); reject(e); }
    });
  });
}

function registerDeviceTools() {
  register({
    name: 'device.get_location',
    description: 'Return the user\'s current device location as { latitude, longitude, accuracy_m, source }. Use this any time the user asks about their current location, weather (it needs coords), nearby places, or commute. Do not ask the user to type their location - call this tool.',
    parameters: {
      type: 'object',
      properties: {
        high_accuracy: {
          type: 'boolean',
          description: 'If true, request a fresh GPS fix (slower, more battery). If false (default), use the last known location, which is usually within seconds and seconds-of-arc accurate.',
        },
      },
      additionalProperties: false,
    },
  }, async (args) => bridgeRpc('device.get_location', args, { timeoutMs: 12_000 }));

  register({
    name: 'device.get_contacts',
    description: 'Search the user\'s contacts list. Returns a JSON array of { name, phones: [string], emails: [string] }. Use this to look up phone numbers or email addresses before placing calls or sending messages.',
    parameters: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Free-text fragment of the contact name. Empty string returns the first 50 contacts. Matching is case-insensitive substring.',
        },
        limit: {
          type: 'integer',
          description: 'Max number of contacts to return. Default 25, max 100.',
        },
      },
      additionalProperties: false,
    },
  }, async (args) => bridgeRpc('device.get_contacts', args || {}, { timeoutMs: 8_000 }));

  register({
    name: 'device.place_call',
    description: 'Place a phone call. You can pass either a literal phone number or the name of a contact (in which case the device will resolve the contact lookup automatically). On success returns { dialed: <number>, contact_name?: <string> }.',
    parameters: {
      type: 'object',
      properties: {
        number: {
          type: 'string',
          description: 'A phone number in E.164 or local format (e.g. "+19175551234" or "9175551234"). Mutually exclusive with contact_name.',
        },
        contact_name: {
          type: 'string',
          description: 'A contact name fragment to look up. The Kotlin side will resolve it; if more than one match is found the tool returns an ambiguity error and the model should disambiguate with the user.',
        },
      },
      additionalProperties: false,
    },
  }, async (args) => bridgeRpc('device.place_call', args || {}, { timeoutMs: 10_000 }));

  register({
    name: 'device.launch_app',
    description: 'Launch an installed Android app by package name (preferred, e.g. "com.whatsapp") or human-readable label (e.g. "WhatsApp"). Returns { launched: true, package: <string> }. If the user wants follow-up UI actions (tap, type), chain this with the ax.* tools.',
    parameters: {
      type: 'object',
      properties: {
        package: {
          type: 'string',
          description: 'Android package id, e.g. "com.whatsapp" or "com.spotify.music".',
        },
        label: {
          type: 'string',
          description: 'App label as shown in the launcher. Used as a fallback if package is not provided. Substring match against PackageManager labels.',
        },
      },
      additionalProperties: false,
    },
  }, async (args) => bridgeRpc('device.launch_app', args || {}, { timeoutMs: 8_000 }));

  register({
    name: 'device.clipboard_get',
    description: 'Read the current Android clipboard contents. Note: Android 13+ shows a small "X read clipboard" toast every time this fires. Returns { text: <string> }.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  }, async () => bridgeRpc('device.clipboard_get', {}, { timeoutMs: 5_000 }));

  register({
    name: 'device.clipboard_set',
    description: 'Write text to the Android clipboard.',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'Text to copy to clipboard.' },
      },
      required: ['text'],
      additionalProperties: false,
    },
  }, async (args) => bridgeRpc('device.clipboard_set', args || {}, { timeoutMs: 5_000 }));

  register({
    name: 'device.battery_status',
    description: 'Returns { percent: <0-100>, charging: <bool>, charging_source?: "ac"|"usb"|"wireless", temperature_c?: <number> }.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
  }, async () => bridgeRpc('device.battery_status', {}, { timeoutMs: 5_000 }));
}

module.exports = { registerDeviceTools };
