"""Python mirror of WakePhraseMatcher.kt and assets/node/src/wake/phrase_matcher.js.

We deliberately keep the same constants and the same Damerau-Levenshtein
algorithm so a fixture that matches on Android also matches on Mac.

Algorithm (same on all three sides):

    1. Lowercase and strip non-alphanumeric punctuation.
    2. Tokenize on whitespace.
    3. Slide a window of len(target) tokens over candidate tokens; for each
       window compute per-token Damerau-Levenshtein distance, allow up to
       _max_token_edits_for(targetToken) edits per token AND up to
       MAX_TOTAL_EDITS overall.
    4. Match must start at a token boundary (so 'amber' does not fire 'ben').

Per-target-token edit limit:
    target token length <= 6: 0 edits (exact). Keeps "Ben"/"Sasha"/"Jarvis"/
        "Friday" all in exact-only mode - short wake words match too much
        ambient noise at 1 edit.
    target token length >= 7: MAX_TOKEN_EDITS edits.

The function returns True on first matching window.
"""
from __future__ import annotations

import re

MAX_TOKEN_EDITS = 1
MAX_TOTAL_EDITS = 2
STRICT_LENGTH_THRESHOLD = 6

_NORM_RE = re.compile(r"[^a-z0-9 ]")
_WS_RE = re.compile(r"\s+")


def _max_token_edits_for(token: str) -> int:
    return 0 if len(token) <= STRICT_LENGTH_THRESHOLD else MAX_TOKEN_EDITS


def matches(candidate: str | None, target: str) -> bool:
    if not candidate:
        return False
    target_tokens = [t for t in _normalize(target).split(" ") if t]
    if not target_tokens:
        return False
    cand_tokens = [t for t in _normalize(candidate).split(" ") if t]
    if len(cand_tokens) < len(target_tokens):
        return False

    for start in range(0, len(cand_tokens) - len(target_tokens) + 1):
        total = 0
        ok = True
        for i, tt in enumerate(target_tokens):
            edits = _damerau_levenshtein(tt, cand_tokens[start + i])
            if edits > _max_token_edits_for(tt):
                ok = False
                break
            total += edits
            if total > MAX_TOTAL_EDITS:
                ok = False
                break
        if ok:
            return True
    return False


def _normalize(s: str) -> str:
    s = s.lower()
    s = _NORM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def _damerau_levenshtein(a: str, b: str) -> int:
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + cost)
    return d[n][m]
