'use strict';
/**
 * On startup, make sure the on-device workspace has the seed files we need.
 * Mirrors omniclaw/workspace_template/* on the Mac.
 */
const fs = require('fs');
const path = require('path');

function ensureWorkspaceLayout(workspaceRoot) {
  const subdirs = ['sessions', 'logs', 'caches', 'tmp', 'screenshots'];
  for (const sub of subdirs) {
    fs.mkdirSync(path.join(workspaceRoot, sub), { recursive: true });
  }
  const indexFile = path.join(workspaceRoot, 'sessions', 'index.jsonl');
  if (!fs.existsSync(indexFile)) {
    fs.writeFileSync(indexFile, '');
  }
}

module.exports = { ensureWorkspaceLayout };
