'use strict';
/**
 * Unit tests for src/wake/phrase_matcher.js. Same fixtures should be runnable
 * against the Kotlin WakePhraseMatcher.kt; if you change the algorithm in one,
 * mirror the change in the other and re-run BOTH suites.
 */
const assert = require('node:assert');
const { matches } = require('../src/wake/phrase_matcher.js');

// -- positives: should fire ---------------------------------------------------
const positives = [
  ['Ben',                 'Ben'],
  ['ben',                 'Ben'],
  ['hey ben',             'Ben'],
  ['Ben?',                'Ben'],
  ['Ben.',                'Ben'],
  ['ok ben please',       'Ben'],
  ['Hey Ben',             'Hey Ben'],
  ['hey, ben!',           'Hey Ben'],
  // Other short wake phrases that fit in the <=6-char strict regime.
  ['sasha',               'Sasha'],
  ['hey sasha',           'Sasha'],
  ['jarvis',              'Jarvis'],
  ['friday please',       'Friday'],
  // 7+ char tokens still tolerate one Damerau-Levenshtein edit.
  ['comput',              'compute'],   // delete 'e'
  ['computor',            'computer'],  // 'o' for 'e'
];

// -- negatives: should NOT fire ----------------------------------------------
const negatives = [
  ['amber',               'Ben'],   // word boundary protection
  ['absurd',              'Ben'],
  ['banana',              'Ben'],
  ['rebel',               'Ben'],
  ['benjamin',            'Ben'],   // we want a clean word, not a substring
  ['',                    'Ben'],
  [null,                  'Ben'],
  ['hey',                 'Hey Ben'],
  ['ben hey',             'Hey Ben'], // wrong order
  // Short-token strict-match (regression guard for the 2026-05 Hindi-reply
  // storm bug): 1-edit neighbors must NOT fire a 3-char wake word, otherwise
  // the recognizer's noise output starts a feedback loop.
  ['been',                'Ben'],
  ['ban',                 'Ben'],
  ['bin',                 'Ben'],
  ['pen',                 'Ben'],
  ['bend',                'Ben'],
  ['bn',                  'Ben'],
  ['hey ban',             'Ben'],
  ['hey bend',            'Hey Ben'],
  // 5- and 6-char tokens are now strict-only too (regression guard for the
  // 2026-05 "Sasha" runaway: 1-edit allowance let "tasha"/"saska" trigger).
  ['tasha',               'Sasha'],
  ['saska',               'Sasha'],
  ['sasher',              'Sasha'],
  ['jarviz',              'Jarvis'],
  ['frida',               'Friday'],
];

let ok = 0; let fail = 0;
for (const [c, t] of positives) {
  if (matches(c, t)) ok++; else { fail++; console.error('positive failed:', JSON.stringify(c), 'vs', t); }
}
for (const [c, t] of negatives) {
  if (!matches(c, t)) ok++; else { fail++; console.error('negative failed:', JSON.stringify(c), 'vs', t); }
}

if (fail) {
  console.error(`wake_phrase_matcher: ${fail} failures (${ok}/${positives.length + negatives.length} pass)`);
  process.exit(1);
}
console.log(`wake_phrase_matcher: PASS (${ok}/${ok})`);
