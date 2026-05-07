"""Same fixtures as
android/app/src/main/assets/node/test/wake_phrase_matcher.test.js and the
expected matrix the Kotlin WakePhraseMatcher.kt is tuned for.

If a fixture changes here, change it in both other implementations too.

v0.1.3 rule:
    len(token) < 4: 1 edit + first-char rule. Catches 'ben'/'bend'/'bin'/
        'ban'/'been' but rejects 'pen'/'hen'/'ten'.
    4 <= len(token) <= 6: exact match only.
    len(token) >= 7: 1 edit allowed.
"""
from __future__ import annotations

import pytest

from omniclaw.voice.wake_phrase_matcher import matches


POSITIVE_FIXTURES = [
    # Direct hits.
    ("Ben",                "Ben"),
    ("ben",                "Ben"),
    ("hey ben",            "Ben"),
    ("Ben?",               "Ben"),
    ("Ben.",               "Ben"),
    ("ok ben please",      "Ben"),
    ("Hey Ben",            "Hey Ben"),
    ("hey, ben!",          "Hey Ben"),
    # Short-token lenient with first-char guard - the v0.1.3 fix.
    ("bend",               "Ben"),
    ("bin",                "Ben"),
    ("ban",                "Ben"),
    ("been",               "Ben"),
    ("hey bend",           "Hey Ben"),
    ("hey bin please",     "Ben"),
    # 4-6 char strict regime.
    ("sasha",              "Sasha"),
    ("hey sasha",          "Sasha"),
    ("jarvis",             "Jarvis"),
    ("friday please",      "Friday"),
    # 7+ char tokens tolerate one Damerau-Levenshtein edit.
    ("comput",             "compute"),     # delete 'e'
    ("computor",           "computer"),    # 'o' for 'e'
]

NEGATIVE_FIXTURES = [
    # Word-boundary / first-char protection.
    ("amber",              "Ben"),    # first char != 'b'
    ("absurd",             "Ben"),    # first char != 'b'
    ("rebel",              "Ben"),    # first char != 'b'
    ("banana",             "Ben"),    # DL too high
    ("benjamin",           "Ben"),    # DL too high (we want a clean word)
    ("",                   "Ben"),
    (None,                 "Ben"),
    ("hey",                "Hey Ben"),
    ("ben hey",            "Hey Ben"),
    # Different first letter -> no match even at DL=1.
    ("pen",                "Ben"),
    ("hen",                "Ben"),
    ("ten",                "Ben"),
    ("len",                "Ben"),
    # 4-6 char tokens stay strict (regression guard for the 2026-05 Sasha
    # runaway).
    ("tasha",              "Sasha"),
    ("saska",              "Sasha"),
    ("sasher",             "Sasha"),
    ("jarviz",             "Jarvis"),
    ("frida",              "Friday"),
]


@pytest.mark.parametrize("candidate, target", POSITIVE_FIXTURES)
def test_positive(candidate: str | None, target: str) -> None:
    assert matches(candidate, target) is True, (
        f"expected positive match for candidate={candidate!r}, target={target!r}"
    )


@pytest.mark.parametrize("candidate, target", NEGATIVE_FIXTURES)
def test_negative(candidate: str | None, target: str) -> None:
    assert matches(candidate, target) is False, (
        f"expected NO match for candidate={candidate!r}, target={target!r}"
    )
