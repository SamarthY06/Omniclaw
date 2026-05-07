'use strict';
/**
 * Unit tests for src/wake/phrase_matcher.js. Same fixtures should be runnable
 * against the Kotlin WakePhraseMatcher.kt; if you change the algorithm in one,
 * mirror the change in the other and re-run BOTH suites.
 *
 * v0.1.3 rule:
 *   - len(token) < 4 (short, e.g. "Ben"): 1 edit + first-char match required.
 *     Catches "ben"/"bend"/"bin"/"ban"/"been" but rejects "pen"/"hen"/"ten".
 *   - 4 <= len(token) <= 6 ("Sasha", "Jarvis"): exact match only.
 *   - len(token) >= 7 ("Friday"... wait Friday=6, "Computer"): 1 edit allowed.
 */
const assert = require('node:assert');
const { matches } = require('../src/wake/phrase_matcher.js');

// -- positives: should fire ---------------------------------------------------
const positives = [
  // Direct hits
  ['Ben',                 'Ben'],
  ['ben',                 'Ben'],
  ['hey ben',             'Ben'],
  ['Ben?',                'Ben'],
  ['Ben.',                'Ben'],
  ['ok ben please',       'Ben'],
  ['Hey Ben',             'Hey Ben'],
  ['hey, ben!',           'Hey Ben'],
  // Short-token lenient with first-char guard - the v0.1.3 fix.
  // SpeechRecognizer often returns these instead of bare "ben"; before
  // v0.1.3 they all silently failed and the wake never fired.
  ['bend',                'Ben'],
  ['bin',                 'Ben'],
  ['ban',                 'Ben'],
  ['been',                'Ben'],
  ['hey bend',            'Hey Ben'],
  ['hey bin please',      'Ben'],
  // 4-6 char strict regime
  ['sasha',               'Sasha'],
  ['hey sasha',           'Sasha'],
  ['jarvis',              'Jarvis'],
  ['friday please',       'Friday'],
  // 7+ char tokens tolerate one Damerau-Levenshtein edit.
  ['comput',              'compute'],   // delete 'e'
  ['computor',            'computer'],  // 'o' for 'e'
];

// -- negatives: should NOT fire ----------------------------------------------
const negatives = [
  // Word-boundary / first-char protection.
  ['amber',               'Ben'],   // first char 'a' != 'b'
  ['absurd',              'Ben'],   // first char 'a' != 'b'
  ['rebel',               'Ben'],   // first char 'r' != 'b'
  ['banana',              'Ben'],   // DL("ben","banana")=4
  ['benjamin',            'Ben'],   // DL too high (we want a clean word, not a substring)
  ['',                    'Ben'],
  [null,                  'Ben'],
  ['hey',                 'Hey Ben'],
  ['ben hey',             'Hey Ben'], // wrong order
  // Different first letter -> no match even at DL=1. The whole point of
  // the v0.1.3 first-char rule.
  ['pen',                 'Ben'],
  ['hen',                 'Ben'],
  ['ten',                 'Ben'],
  ['len',                 'Ben'],
  // 4-6 char tokens stay strict (regression guard for the 2026-05 "Sasha"
  // runaway: 1-edit allowance let "tasha"/"saska" trigger).
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
