'use strict';
/**
 * Optional mDNS discovery.
 *
 * On the Mac (Python) side we use `zeroconf`. On Android we'd ideally use
 * `bonjour-service` (npm), but it pulls native deps that don't always cross-compile.
 * The plan calls this out as fallback-friendly: discovery is OPTIONAL, the
 * primary pairing flow is QR + manual host:port, so this module silently
 * no-ops when the dep isn't available.
 */
let bonjour;
try { bonjour = require('bonjour-service'); } catch (_) { bonjour = null; }

const SERVICE_TYPE = 'jarvis';

class Discovery {
  constructor(instanceName = 'jarvis') {
    this.instanceName = instanceName;
    this._b = null;
    this._service = null;
  }

  publish(port, props = {}) {
    if (!bonjour) return false;
    this._b = new bonjour.Bonjour();
    this._service = this._b.publish({
      name: this.instanceName, type: SERVICE_TYPE, port, txt: props,
    });
    return true;
  }

  unpublish() {
    if (this._service) this._service.stop();
    if (this._b) this._b.destroy();
    this._service = null; this._b = null;
  }

  browseOnce({ timeoutMs = 2000 } = {}) {
    return new Promise((resolve) => {
      if (!bonjour) return resolve([]);
      const seen = [];
      const b = new bonjour.Bonjour();
      const browser = b.find({ type: SERVICE_TYPE });
      browser.on('up', (svc) => {
        if (svc.addresses && svc.addresses[0]) {
          seen.push({ host: svc.addresses[0], port: svc.port });
        }
      });
      setTimeout(() => { b.destroy(); resolve(seen); }, timeoutMs);
    });
  }
}

module.exports = { Discovery, SERVICE_TYPE };
