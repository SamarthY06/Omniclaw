'use strict';
/**
 * Web tools: generic HTTP fetch + a weather convenience wrapper that uses
 * wttr.in (free, no API key required, returns a JSON body via ?format=j1).
 *
 * These run inside the embedded nodejs-mobile runtime, so we use the global
 * fetch (Node 18+ ships undici). Outbound HTTPS works on Android subject to
 * the user's INTERNET permission - declared in the manifest at install time.
 */
const { register } = require('./registry.js');

const DEFAULT_TIMEOUT_MS = 12_000;
const MAX_BODY_BYTES = 64 * 1024; // 64 KB cap on returned body so the model context isn't blown out

function registerWebTools() {
  register({
    name: 'web.fetch',
    description: 'Make an HTTP request and return the response. Use this for any external web call: news, scores, sports, public APIs, fact-check lookups. The response body is truncated at 64 KB. Avoid binary URLs (images, video) - this tool is for text/JSON. Returns { status, ok, headers, body, truncated, content_type }.',
    parameters: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'Absolute URL (https:// preferred).' },
        method: { type: 'string', enum: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD'], description: 'HTTP method. Default GET.' },
        headers: { type: 'object', description: 'Map of header name -> value. Authorization etc. allowed; nothing is logged.', additionalProperties: { type: 'string' } },
        body: { type: 'string', description: 'Request body for POST/PUT/PATCH (string, e.g. JSON-stringified).' },
        timeout_ms: { type: 'integer', description: 'Per-request timeout in ms. Default 12000.' },
      },
      required: ['url'],
      additionalProperties: false,
    },
  }, async (args) => {
    const url = String(args.url || '');
    if (!/^https?:\/\//i.test(url)) {
      return { ok: false, error: 'invalid_url', hint: 'URL must start with http:// or https://.' };
    }
    const method = (args.method || 'GET').toUpperCase();
    const timeoutMs = Math.max(1000, parseInt(args.timeout_ms, 10) || DEFAULT_TIMEOUT_MS);
    if (typeof fetch !== 'function') {
      return { ok: false, error: 'fetch_unavailable_in_runtime' };
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(new Error('timeout')), timeoutMs);
    let resp;
    try {
      resp = await fetch(url, {
        method,
        headers: args.headers || {},
        body: ['POST', 'PUT', 'PATCH'].includes(method) ? (args.body || '') : undefined,
        signal: ctrl.signal,
      });
    } catch (e) {
      clearTimeout(t);
      return { ok: false, error: 'fetch_failed', hint: e && e.message ? e.message : String(e) };
    }
    clearTimeout(t);
    let raw;
    try {
      raw = await resp.text();
    } catch (e) {
      return { ok: false, error: 'body_read_failed', hint: e.message };
    }
    const truncated = raw.length > MAX_BODY_BYTES;
    const body = truncated ? raw.slice(0, MAX_BODY_BYTES) : raw;
    const headers = {};
    try { resp.headers.forEach((v, k) => { headers[k] = v; }); } catch (_) {}
    return {
      ok: true,
      result: {
        status: resp.status,
        ok: resp.ok,
        content_type: headers['content-type'] || null,
        headers,
        body,
        truncated,
      },
    };
  });

  register({
    name: 'weather.current',
    description: 'Get the current weather in plain English for a location. Uses wttr.in (free, no API key). If location is omitted, the model SHOULD call device.get_location first and pass "lat,lon" - that gives weather for exactly where the user is. Otherwise pass a city name ("Bangalore", "Bengaluru", "London"). Returns { area, country, condition, temp_c, feels_like_c, humidity_pct, wind_kmh, summary, raw }.',
    parameters: {
      type: 'object',
      properties: {
        location: { type: 'string', description: 'City name or "lat,lon" pair. If omitted the IP-based default of wttr.in is used (less accurate).' },
      },
      additionalProperties: false,
    },
  }, async (args) => {
    const loc = String(args.location || '').trim();
    const url = `https://wttr.in/${encodeURIComponent(loc)}?format=j1`;
    if (typeof fetch !== 'function') {
      return { ok: false, error: 'fetch_unavailable_in_runtime' };
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(new Error('timeout')), DEFAULT_TIMEOUT_MS);
    let resp;
    try {
      resp = await fetch(url, { headers: { 'User-Agent': 'Ben-AndroidAssistant/0.1.3' }, signal: ctrl.signal });
    } catch (e) {
      clearTimeout(t);
      return { ok: false, error: 'wttr_fetch_failed', hint: e && e.message ? e.message : String(e) };
    }
    clearTimeout(t);
    if (!resp.ok) {
      const txt = await resp.text().catch(() => '');
      return { ok: false, error: 'wttr_http_' + resp.status, hint: txt.slice(0, 200) };
    }
    let json;
    try { json = await resp.json(); }
    catch (e) { return { ok: false, error: 'wttr_parse_failed', hint: e.message }; }
    const cur = (json.current_condition && json.current_condition[0]) || {};
    const area = (json.nearest_area && json.nearest_area[0]) || {};
    const condition = (cur.weatherDesc && cur.weatherDesc[0] && cur.weatherDesc[0].value) || 'Unknown';
    const tempC = parseFloat(cur.temp_C) || null;
    const feels = parseFloat(cur.FeelsLikeC) || null;
    const humidity = parseInt(cur.humidity, 10) || null;
    const wind = parseFloat(cur.windspeedKmph) || null;
    const areaName = (area.areaName && area.areaName[0] && area.areaName[0].value) || loc || 'your area';
    const country = (area.country && area.country[0] && area.country[0].value) || '';
    const summary = `${condition}, ${tempC != null ? tempC + '°C' : 'temp unknown'}` +
      (feels != null ? ` (feels like ${feels}°C)` : '') +
      ` in ${areaName}${country ? ', ' + country : ''}`;
    return {
      ok: true,
      result: {
        area: areaName,
        country,
        condition,
        temp_c: tempC,
        feels_like_c: feels,
        humidity_pct: humidity,
        wind_kmh: wind,
        summary,
      },
    };
  });
}

module.exports = { registerWebTools };
