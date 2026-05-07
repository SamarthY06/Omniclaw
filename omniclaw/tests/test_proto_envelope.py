"""Envelope serialization, canonical JSON, HMAC sign+verify, replay window."""
from __future__ import annotations

import json
import time
import uuid

import pytest

from omniclaw.proto.crypto import (
    canonical_json,
    compute_hmac,
    sign_envelope,
    verify_envelope,
    verify_hmac,
)
from omniclaw.proto.types import AuthBlock, Envelope


def make_env() -> Envelope:
    return Envelope(
        v=1,
        id=str(uuid.uuid4()),
        kind="req",
        method="peer.ping",
        ts_ms=int(time.time() * 1000),
        params={"ts_ms": 12345},
        auth=AuthBlock(device_id="mac-test", hmac_sha256="0" * 64),
    )


def test_canonical_json_is_deterministic():
    a = {"b": 1, "a": [3, 2, 1], "c": {"y": 2, "x": 1}}
    b = {"a": [3, 2, 1], "c": {"x": 1, "y": 2}, "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert b" " not in canonical_json(a)


def test_compute_and_verify_hmac_matches(shared_secret):
    payload = {"hello": "world", "n": 7}
    mac = compute_hmac(shared_secret, payload)
    assert len(mac) == 64
    assert verify_hmac(shared_secret, payload, mac)
    assert not verify_hmac(shared_secret, {"hello": "world", "n": 8}, mac)


def test_sign_envelope_round_trip(shared_secret):
    env = make_env()
    sign_envelope(env, shared_secret)
    ok, reason = verify_envelope(env, shared_secret)
    assert ok, reason


def test_envelope_replay_window_rejects_old(shared_secret):
    env = make_env()
    env.ts_ms = int(time.time() * 1000) - 120_000
    sign_envelope(env, shared_secret)
    ok, reason = verify_envelope(env, shared_secret, max_skew_ms=60_000)
    assert not ok
    assert reason == "ts_outside_replay_window"


def test_envelope_replay_window_rejects_future(shared_secret):
    env = make_env()
    env.ts_ms = int(time.time() * 1000) + 120_000
    sign_envelope(env, shared_secret)
    ok, reason = verify_envelope(env, shared_secret, max_skew_ms=60_000)
    assert not ok
    assert reason == "ts_outside_replay_window"


def test_envelope_tamper_detected(shared_secret):
    env = make_env()
    sign_envelope(env, shared_secret)
    env.method = "peer.ping_tampered"
    ok, reason = verify_envelope(env, shared_secret)
    assert not ok
    assert reason == "hmac_mismatch"


def test_envelope_missing_keys_rejected():
    with pytest.raises(Exception):
        Envelope.model_validate({"v": 1, "id": "x"})


def test_envelope_id_must_be_uuid():
    with pytest.raises(Exception):
        Envelope.model_validate({
            "v": 1,
            "id": "not-a-uuid",
            "kind": "req",
            "method": "peer.ping",
            "ts_ms": 1,
            "params": {},
            "auth": {"device_id": "x", "hmac_sha256": "0" * 64},
        })


def test_envelope_serializes_to_json(shared_secret):
    env = make_env()
    sign_envelope(env, shared_secret)
    out = json.dumps(env.model_dump())
    parsed = Envelope.model_validate_json(out)
    ok, _ = verify_envelope(parsed, shared_secret)
    assert ok
