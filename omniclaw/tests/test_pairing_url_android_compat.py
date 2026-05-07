"""Verify that the pairing URL produced by `pair show --qr` on Mac contains
every query parameter the Android PairingActivity.kt parser depends on.

This is a guard against accidental drift: if anyone changes either side's
URL format we want a failing test, not a silently-broken pair-once-and-pray
flow.

The Android-side parser lives in
android/app/src/main/java/com/ben/pairing/PairingActivity.kt and looks for:

    host (required)
    port (required, parsed as int, default 18790 on miss)
    secret (required, base64url string)
    id (used as deviceId)
    role (mac|android, optional but logged)
    fp (fingerprint, optional)
    v (schema version, optional)

This test imports payload_to_uri/payload_from_uri and asserts the round-trip
shape is what the Kotlin code expects.
"""
from __future__ import annotations

import re
import urllib.parse

import pytest

from omniclaw.peer.pair import PairingPayload, payload_to_uri, payload_from_uri


REQUIRED_FOR_ANDROID = {"host", "port", "secret", "id"}
OPTIONAL_FOR_ANDROID = {"role", "fp", "v"}


def _parse_query(uri: str) -> dict[str, str]:
    parsed = urllib.parse.urlparse(uri)
    return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}


def test_pair_uri_has_all_keys_android_parser_needs() -> None:
    pl = PairingPayload(
        host="100.110.5.1", port=18790, fingerprint="abcd1234",
        secret_b64="dGVzdC1zZWNyZXQ=", role="mac", device_id="mac-x",
    )
    uri = payload_to_uri(pl)
    assert uri.startswith("jarvis://pair?")
    q = _parse_query(uri)
    assert REQUIRED_FOR_ANDROID <= set(q.keys()), \
        f"missing required keys: {REQUIRED_FOR_ANDROID - set(q.keys())}"
    assert int(q["port"]) == 18790
    assert q["host"] == "100.110.5.1"
    assert q["secret"] == "dGVzdC1zZWNyZXQ="
    assert q["id"] == "mac-x"


def test_pair_uri_round_trip_preserves_required_fields() -> None:
    pl = PairingPayload(
        host="mac.tail.ts", port=18790, fingerprint="",
        secret_b64="dGVzdA==", role="mac", device_id="mac-1",
    )
    uri = payload_to_uri(pl)
    parsed = payload_from_uri(uri)
    assert parsed.host == pl.host
    assert parsed.port == pl.port
    assert parsed.secret_b64 == pl.secret_b64
    assert parsed.device_id == pl.device_id
    assert parsed.role == pl.role


def test_kotlin_simple_query_parser_compatibility() -> None:
    """Ensure Mac uses urlencode params that the simple Kotlin substringAfter('?')
    + split('&') parser can decode safely.

    Specifically: no '#' fragment chars, all values URL-encoded so they don't
    contain a literal '&' or '=' that would confuse split().
    """
    pl = PairingPayload(
        host="weird host with spaces", port=18790, fingerprint="a&b=c",
        secret_b64="x+y/z=", role="mac", device_id="mac=hash#frag",
    )
    uri = payload_to_uri(pl)
    assert "#" not in uri, "Kotlin parser uses substringAfter('?'); fragment would break it"
    # Each kv should split cleanly.
    qs = uri.split('?', 1)[1]
    for kv in qs.split('&'):
        assert kv.count('=') >= 1, f"unsplittable kv: {kv!r}"
    # And re-parse via Kotlin-style manual parser:
    out: dict[str, str] = {}
    for kv in qs.split('&'):
        k, v = kv.split('=', 1)
        # Kotlin uses java.net.URLDecoder.decode(v, "UTF-8") which is form-decode:
        # '+' -> space. Mirror that with unquote_plus.
        out[k] = urllib.parse.unquote_plus(v)
    assert out["host"] == pl.host
    assert out["secret"] == pl.secret_b64
    assert out["id"] == pl.device_id
