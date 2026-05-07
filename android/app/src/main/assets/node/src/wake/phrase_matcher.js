'use strict';
/**
 * Mirror of WakePhraseMatcher.kt and omniclaw/voice/wake_phrase_matcher.py.
 *
 * Per-target-token edit limit (v0.1.3):
 *   - length < 4 (e.g. "Ben"): 1 edit + first-char rule.
 *     Catches "ben"/"bend"/"bin"/"benz" but rejects "pen"/"hen". Without
 *     this, short wake words almost never fire on a noisy mic because
 *     the recognizer rarely returns the bare word "ben" alone.
 *   - 4 <= length <= 6 (e.g. "Sasha"): 0 edits, strict.
 *   - length >= 7 (e.g. "Jarvis", "Friday"): MAX_TOKEN_EDITS edits.
 *
 * If you tune any of the three implementations, mirror the change in the
 * other two and re-run all three test suites.
 */
const MAX_TOKEN_EDITS = 1;
const MAX_TOTAL_EDITS = 2;
const EXACT_LOWER = 4;
const EXACT_UPPER = 7;

function maxTokenEditsFor(token) {
  if (token.length < EXACT_LOWER) return MAX_TOKEN_EDITS;
  if (token.length < EXACT_UPPER) return 0;
  return MAX_TOKEN_EDITS;
}

function requiresFirstCharMatch(token) {
  return token.length < EXACT_LOWER;
}

function matches(candidate, target) {
  if (!candidate) return false;
  const t = norm(target).split(' ').filter(Boolean);
  if (t.length === 0) return false;
  const c = norm(candidate).split(' ').filter(Boolean);
  if (c.length < t.length) return false;
  for (let s = 0; s <= c.length - t.length; s++) {
    let total = 0; let ok = true;
    for (let i = 0; i < t.length; i++) {
      const tt = t[i];
      const ct = c[s + i];
      const e = damLev(tt, ct);
      if (e > maxTokenEditsFor(tt)) { ok = false; break; }
      if (requiresFirstCharMatch(tt) && tt.length > 0 && ct.length > 0 && tt[0] !== ct[0]) {
        ok = false; break;
      }
      total += e;
      if (total > MAX_TOTAL_EDITS) { ok = false; break; }
    }
    if (ok) return true;
  }
  return false;
}

function norm(s) { return s.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim(); }

function damLev(a, b) {
  const n = a.length, m = b.length;
  if (!n) return m; if (!m) return n;
  const d = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 0; i <= n; i++) d[i][0] = i;
  for (let j = 0; j <= m; j++) d[0][j] = j;
  for (let i = 1; i <= n; i++) for (let j = 1; j <= m; j++) {
    const cost = a[i - 1] === b[j - 1] ? 0 : 1;
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
    if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
      d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + cost);
    }
  }
  return d[n][m];
}

module.exports = { matches };
