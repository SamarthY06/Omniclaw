package com.ben.wake

/**
 * Fuzzy matcher for the wake phrase.
 *
 * Algorithm:
 *   1. Lowercase + strip punctuation from both target and candidate.
 *   2. Tokenize on whitespace.
 *   3. Slide a window of `target.tokenCount` over candidate tokens; for each
 *      window compute the per-pair Damerau-Levenshtein distance, allow up to
 *      maxTokenEditsFor(targetToken) edits per pair AND at most
 *      MAX_TOTAL_EDITS overall.
 *   4. Each matched pair must also start with the same first character -
 *      so "ben" -> "bend"/"bin"/"benz" matches but "ben" -> "pen"/"hen"
 *      does not. This is what cuts the false-positive rate for short
 *      wake words without forcing exact match.
 *   5. Match must START on a token boundary.
 *
 * Per-target-token edit limit:
 *   - target token length <= 3 (e.g. "Ben"): 1 edit + first-char rule.
 *     Required for short wake words to actually fire on a noisy mic where
 *     the recognizer hears "bend" / "bin" / "ben." but rarely "ben" alone.
 *     The first-char rule keeps the false-positive rate sane.
 *   - 4 <= target token length <= 6: 0 edits (exact). "Sasha" / "Jarvis" /
 *     "Friday" stay strict; they're long enough that recognition error
 *     is rare and false positives matter more.
 *   - target token length >= 7: MAX_TOKEN_EDITS edits.
 *
 * Identical Kotlin / JS / Python implementations - keep them in sync.
 */
object WakePhraseMatcher {
    private const val MAX_TOKEN_EDITS = 1
    private const val MAX_TOTAL_EDITS = 2
    private const val EXACT_LOWER = 4    // length <  this -> short rule (1 edit + first char)
    private const val EXACT_UPPER = 7    // length >= this -> generous rule (1 edit, no first-char)

    fun matches(candidate: String?, target: String): Boolean {
        if (candidate.isNullOrBlank()) return false
        val targetTokens = normalize(target).split(' ').filter { it.isNotEmpty() }
        if (targetTokens.isEmpty()) return false
        val candTokens = normalize(candidate).split(' ').filter { it.isNotEmpty() }
        if (candTokens.size < targetTokens.size) return false
        for (start in 0..(candTokens.size - targetTokens.size)) {
            var totalEdits = 0
            var ok = true
            for (i in targetTokens.indices) {
                val tt = targetTokens[i]
                val ct = candTokens[start + i]
                val edits = damerauLevenshtein(tt, ct)
                if (edits > maxTokenEditsFor(tt)) { ok = false; break }
                if (requiresFirstCharMatch(tt) && tt.isNotEmpty() && ct.isNotEmpty() && tt[0] != ct[0]) {
                    ok = false; break
                }
                totalEdits += edits
                if (totalEdits > MAX_TOTAL_EDITS) { ok = false; break }
            }
            if (ok) return true
        }
        return false
    }

    private fun maxTokenEditsFor(token: String): Int = when {
        token.length < EXACT_LOWER -> MAX_TOKEN_EDITS         // short: lenient with first-char guard
        token.length < EXACT_UPPER -> 0                       // mid: exact only
        else -> MAX_TOKEN_EDITS                               // long: lenient
    }

    private fun requiresFirstCharMatch(token: String): Boolean = token.length < EXACT_LOWER

    private fun normalize(s: String): String =
        s.lowercase().replace(Regex("[^a-z0-9 ]"), " ").replace(Regex("\\s+"), " ").trim()

    /** Damerau-Levenshtein with adjacent-transposition. Small strings, O(n*m) is fine. */
    private fun damerauLevenshtein(a: String, b: String): Int {
        val n = a.length; val m = b.length
        if (n == 0) return m
        if (m == 0) return n
        val d = Array(n + 1) { IntArray(m + 1) }
        for (i in 0..n) d[i][0] = i
        for (j in 0..m) d[0][j] = j
        for (i in 1..n) {
            for (j in 1..m) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                d[i][j] = minOf(
                    d[i - 1][j] + 1,
                    d[i][j - 1] + 1,
                    d[i - 1][j - 1] + cost,
                )
                if (i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1]) {
                    d[i][j] = minOf(d[i][j], d[i - 2][j - 2] + cost)
                }
            }
        }
        return d[n][m]
    }
}
