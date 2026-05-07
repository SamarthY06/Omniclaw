'use strict';
/**
 * Wires the session timer's "silence cut" event into the Kotlin BenVoiceService
 * by sending an ACTION_STOP intent equivalent (we just return ok and BenVoiceService
 * polls for session.ended state). Keeps the Node side authoritative on timing.
 */
const { sessionTimer } = require('./lifecycle.js');
const { sessionStore } = require('./store.js');

async function startVoicePipeline({ workspace }) {
  const timer = sessionTimer();
  timer.onTimeout = ({ idleFor }) => {
    console.log('[session] silence cutoff fired after ' + idleFor + ' ms');
    // The Kotlin side polls session.ended via the JSONL file and via in-process
    // notifications from BenVoiceService.stopAndRearm(). Closing the WSS lives
    // in Kotlin; here we just persist the ended event so history reflects it.
    // Active session id is whatever BenVoiceService told us last.
    const store = sessionStore(workspace);
    for (const id of store.activeSessions.keys()) {
      store.end(id, 'silence_180s');
    }
  };
}

module.exports = { startVoicePipeline };
