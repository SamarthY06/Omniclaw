"""Pairing payload encode/decode + peer.json/identity.json persistence."""
from __future__ import annotations

import pytest

from omniclaw.peer.pair import (
    IdentityRecord,
    PairingPayload,
    PeerRecord,
    create_pairing_payload,
    load_identity,
    load_peer_record,
    payload_from_uri,
    payload_to_uri,
    save_identity,
    save_peer_record,
    shared_secret_bytes,
)


def test_pairing_uri_round_trip_with_fingerprint():
    src = PairingPayload(
        host="my-mac.tail-net.ts.net",
        port=18790,
        fingerprint="abc123",
        secret_b64="dGVzdA==",
        role="mac",
        device_id="mac-001",
    )
    uri = payload_to_uri(src)
    assert uri.startswith("jarvis://pair?")
    parsed = payload_from_uri(uri)
    assert parsed == src


def test_pairing_uri_round_trip_no_fingerprint():
    src = PairingPayload(
        host="192.168.1.42",
        port=18790,
        fingerprint="",
        secret_b64="x",
        role="android",
        device_id="phone-001",
    )
    uri = payload_to_uri(src)
    parsed = payload_from_uri(uri)
    assert parsed.host == "192.168.1.42"
    assert parsed.fingerprint == ""
    assert parsed.role == "android"


def test_create_pairing_payload_has_random_secret():
    a = create_pairing_payload(host="h", port=1, role="mac", device_id="d1")
    b = create_pairing_payload(host="h", port=1, role="mac", device_id="d1")
    assert a.secret_b64 != b.secret_b64
    assert len(a.secret_b64) >= 40  # base64 of 32 bytes


def test_payload_from_uri_rejects_garbage():
    with pytest.raises(ValueError):
        payload_from_uri("not a uri")


def test_payload_from_uri_missing_required_field():
    with pytest.raises(ValueError):
        payload_from_uri("jarvis://pair?host=&port=1&secret=s&role=mac&id=d")


def test_save_load_peer_record_round_trip(tmp_path):
    file = tmp_path / "peer.json"
    rec = PeerRecord(
        peer_device_id="phone-1",
        peer_role="android",
        peer_caps=["tool:camera"],
        shared_secret_b64="dGVzdA==",
        fingerprint="ab",
        last_seen_endpoint="ws://10.0.0.1:18790",
    )
    save_peer_record(rec, file)
    loaded = load_peer_record(file)
    assert loaded == rec
    # peer.json should be 0600
    assert (file.stat().st_mode & 0o777) == 0o600


def test_save_load_identity_round_trip(tmp_path):
    file = tmp_path / "identity.json"
    rec = IdentityRecord(device_id="mac-abc", role="mac", priority=10)
    save_identity(rec, file)
    loaded = load_identity(file)
    assert loaded == rec


def test_load_peer_record_missing_returns_none(tmp_path):
    assert load_peer_record(tmp_path / "nope.json") is None


def test_shared_secret_bytes_decodes():
    rec = PeerRecord(
        peer_device_id="x",
        peer_role="android",
        shared_secret_b64="dGVzdA==",  # "test"
    )
    assert shared_secret_bytes(rec) == b"test"
