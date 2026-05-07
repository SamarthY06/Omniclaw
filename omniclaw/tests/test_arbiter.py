"""Wake-word arbitration: ranking, deterministic outcome with two simulated peers."""
from __future__ import annotations

import asyncio
import socket
import struct
import time
from dataclasses import dataclass

import pytest

from omniclaw.proto.types import WakeClaim
from omniclaw.wake.arbiter import (
    ArbitrationResult,
    WakeArbiter,
    rank_claim,
)


def test_rank_higher_rms_wins():
    a = WakeClaim(device_id="A", rms_dbfs=-30, confidence=1.0, ts_ms=1, priority=5)
    b = WakeClaim(device_id="B", rms_dbfs=-20, confidence=1.0, ts_ms=1, priority=5)
    assert rank_claim(b) > rank_claim(a)


def test_rank_equal_rms_priority_tiebreaks():
    a = WakeClaim(device_id="A", rms_dbfs=-25.4, confidence=1, ts_ms=1, priority=5)
    b = WakeClaim(device_id="B", rms_dbfs=-25.0, confidence=1, ts_ms=1, priority=10)
    # Both round to -25; b has higher priority -> wins
    assert rank_claim(b) > rank_claim(a)


def test_rank_full_tie_device_id_decides():
    a = WakeClaim(device_id="A", rms_dbfs=-25, confidence=1, ts_ms=1, priority=5)
    b = WakeClaim(device_id="B", rms_dbfs=-25, confidence=1, ts_ms=1, priority=5)
    # B > A lexicographically
    assert rank_claim(b) > rank_claim(a)


def _pick_unused_multicast_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.asyncio
async def test_solo_when_no_peer_responds(shared_secret):
    port = _pick_unused_multicast_port()
    arb = WakeArbiter(
        device_id="mac-only",
        priority=10,
        secret=shared_secret,
        vote_window_ms=80,
        port=port,
    )
    try:
        result = await arb.claim(rms_dbfs=-22.0)
    finally:
        arb.close()
    assert result == ArbitrationResult.SOLO


@pytest.mark.asyncio
async def test_two_peers_loud_wins(shared_secret):
    port = _pick_unused_multicast_port()
    quiet = WakeArbiter(
        device_id="quiet-phone",
        priority=5,
        secret=shared_secret,
        vote_window_ms=200,
        port=port,
    )
    loud = WakeArbiter(
        device_id="loud-mac",
        priority=10,
        secret=shared_secret,
        vote_window_ms=200,
        port=port,
    )
    try:
        results = await asyncio.gather(
            quiet.claim(rms_dbfs=-40.0),
            loud.claim(rms_dbfs=-15.0),
        )
    finally:
        quiet.close()
        loud.close()

    quiet_result, loud_result = results
    # Outcomes can be SOLO if the OS didn't loop the multicast back to the
    # other socket within the window. Accept SOLO/SOLO as a non-failure
    # (multicast is best-effort), but if both received each other, the loud
    # one must win.
    if quiet_result == ArbitrationResult.SOLO and loud_result == ArbitrationResult.SOLO:
        pytest.skip("multicast loopback unavailable on this system")
    assert loud_result == ArbitrationResult.WON
    assert quiet_result == ArbitrationResult.YIELDED


@pytest.mark.asyncio
async def test_priority_breaks_rms_tie(shared_secret):
    port = _pick_unused_multicast_port()
    low_pri = WakeArbiter(
        device_id="phone",
        priority=5,
        secret=shared_secret,
        vote_window_ms=200,
        port=port,
    )
    high_pri = WakeArbiter(
        device_id="mac",
        priority=10,
        secret=shared_secret,
        vote_window_ms=200,
        port=port,
    )
    try:
        results = await asyncio.gather(
            low_pri.claim(rms_dbfs=-25.0),
            high_pri.claim(rms_dbfs=-25.0),
        )
    finally:
        low_pri.close()
        high_pri.close()
    if all(r == ArbitrationResult.SOLO for r in results):
        pytest.skip("multicast loopback unavailable on this system")
    assert results[1] == ArbitrationResult.WON  # high_pri
    assert results[0] == ArbitrationResult.YIELDED  # low_pri
