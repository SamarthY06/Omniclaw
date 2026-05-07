"""Canonical JSON serialization + HMAC-SHA256 signing for envelopes."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from omniclaw.proto.types import Envelope


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8.

    The same input always produces the same bytes, so HMACs match across devices.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_hmac(secret: bytes, signed_payload: dict[str, Any]) -> str:
    """Compute lowercase hex HMAC-SHA256 of the canonical JSON of `signed_payload`."""
    mac = hmac.new(secret, canonical_json(signed_payload), hashlib.sha256)
    return mac.hexdigest()


def verify_hmac(secret: bytes, signed_payload: dict[str, Any], hex_mac: str) -> bool:
    """Constant-time compare. Returns True if the MAC matches."""
    expected = compute_hmac(secret, signed_payload)
    return hmac.compare_digest(expected, hex_mac)


def sign_envelope(env: Envelope, secret: bytes) -> Envelope:
    """Compute and attach the HMAC, returning the same envelope (mutated in place).

    Mutating in place so callers can serialize immediately without a second copy.
    """
    env.auth.hmac_sha256 = compute_hmac(secret, env.signed_dict())
    return env


def verify_envelope(env: Envelope, secret: bytes, max_skew_ms: int = 60_000, now_ms: int | None = None) -> tuple[bool, str | None]:
    """Verify HMAC and reject envelopes outside the replay window.

    Returns (ok, reason_if_not_ok).
    """
    if not verify_hmac(secret, env.signed_dict(), env.auth.hmac_sha256):
        return False, "hmac_mismatch"
    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)
    if abs(now_ms - env.ts_ms) > max_skew_ms:
        return False, "ts_outside_replay_window"
    return True, None
