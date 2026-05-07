"""Same fixtures as
android/app/src/main/assets/node/test/wake_phrase_matcher.test.js and the
expected matrix the Kotlin WakePhraseMatcher.kt is tuned for.

If a fixture changes here, change it in both other implementations too.
"""
from __future__ import annotations

import pytest

from omniclaw.voice.wake_phrase_matcher import matches


POSITIVE_FIXTURES = [
    ("Ben",                "Ben"),
    ("ben",                "Ben"),
    ("hey ben",            "Ben"),
    ("Ben?",               "Ben"),
    ("Ben.",               "Ben"),
    ("ok ben please",      "Ben"),
    ("Hey Ben",            "Hey Ben"),
    ("hey, ben!",          "Hey Ben"),
    # Other short wake phrases that fit in the <=6-char strict regime.
    ("sasha",              "Sasha"),
    ("hey sasha",          "Sasha"),
    ("jarvis",             "Jarvis"),
    ("friday please",      "Friday"),
    # 7+ char tokens still tolerate one Damerau-Levenshtein edit.
    ("comput",             "compute"),     # delete 'e'
    ("computor",           "computer"),    # 'o' for 'e'
]

NEGATIVE_FIXTURES = [
    # Word-boundary protection.
    ("amber",              "Ben"),
    ("absurd",             "Ben"),
    ("banana",             "Ben"),
    ("rebel",              "Ben"),
    ("benjamin",           "Ben"),
    ("",                   "Ben"),
    (None,                 "Ben"),
    ("hey",                "Hey Ben"),
    ("ben hey",            "Hey Ben"),
    # Short-token strict-match: 1-edit neighbors must NOT fire a 3-char wake
    # word, otherwise the recognizer's noise output triggers a feedback loop
    # (regression guard for the 2026-05 Hindi-reply storm bug).
    ("been",               "Ben"),
    ("ban",                "Ben"),
    ("bin",                "Ben"),
    ("pen",                "Ben"),
    ("bend",               "Ben"),
    ("bn",                 "Ben"),
    ("hey ban",            "Ben"),
    ("hey bend",           "Hey Ben"),
    # 5- and 6-char tokens are now strict-only too (regression guard for the
    # 2026-05 "Sasha" runaway: 1-edit allowance let "tasha"/"saska" trigger).
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
