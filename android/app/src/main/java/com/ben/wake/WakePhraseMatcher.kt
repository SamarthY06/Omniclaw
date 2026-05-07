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
 *   4. Match must START on a token boundary - protects against "amber" -> "ben".
 *
 * Per-target-token edit limit:
 *   - target token length <= 6: 0 edits (exact). Required because short
 *     wake words pick up too many false positives at 1 edit: "ben" matches
 *     "ban"/"bend"/"pen"; "sasha" matches "tasha"/"saska"/"sasher". The
 *     6-character cutoff keeps "Ben" / "Sasha" / "Jarvis" / "Friday" all
 *     in exact-match mode, which is what we want for a noisy ambient mic.
 *   - target token length >= 7: MAX_TOKEN_EDITS edits.
 *
 * Identical Kotlin / JS / Python implementations - keep them in sync.
 */
object WakePhraseMatcher {
    private const val MAX_TOKEN_EDITS = 1
    private const val MAX_TOTAL_EDITS = 2
    private const val STRICT_LENGTH_THRESHOLD = 6

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
                val edits = damerauLevenshtein(tt, candTokens[start + i])
                if (edits > maxTokenEditsFor(tt)) { ok = false; break }
                totalEdits += edits
                if (totalEdits > MAX_TOTAL_EDITS) { ok = false; break }
            }
            if (ok) return true
        }
        return false
    }

    private fun maxTokenEditsFor(token: String): Int =
        if (token.length <= STRICT_LENGTH_THRESHOLD) 0 else MAX_TOKEN_EDITS

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
