"""Wake-word arbitration via UDP multicast.

Each device on the LAN broadcasts a `WakeClaim` JSON+HMAC payload to
239.42.42.42:42424. After 200 ms, all devices independently agree on the
winner via a deterministic ranking.

Off-LAN (Tailscale-only) the multicast packet is never delivered, so the
local device just times out and wins by default.
"""
from __future__ import annotations

import asyncio
import enum
import hashlib
import hmac
import json
import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional

from omniclaw.proto.types import WakeClaim


MULTICAST_GROUP = "239.42.42.42"
MULTICAST_PORT = 42424
DEFAULT_VOTE_WINDOW_MS = 200


class ArbitrationResult(enum.Enum):
    WON = "won"
    YIELDED = "yielded"
    SOLO = "solo"


def _hmac_short(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()[:32]


def _serialize(claim: WakeClaim, secret: bytes) -> bytes:
    body = json.dumps(claim.model_dump(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = _hmac_short(secret, body)
    return body + b"|" + sig.encode("ascii")


def _deserialize(raw: bytes, secret: bytes) -> Optional[WakeClaim]:
    if b"|" not in raw:
        return None
    body, sig = raw.rsplit(b"|", 1)
    if _hmac_short(secret, body) != sig.decode("ascii"):
        return None
    try:
        data = json.loads(body)
        return WakeClaim.model_validate(data)
    except Exception:
        return None


def rank_claim(c: WakeClaim) -> tuple[int, int, str]:
    """Higher tuple wins. RMS rounded to integer dB to absorb tiny variation."""
    return (round(c.rms_dbfs), c.priority, c.device_id)


@dataclass
class _SocketPair:
    """A bound recv socket + a send socket. Tests can subclass this."""
    recv: socket.socket
    send: socket.socket


def _make_default_sockets(group: str = MULTICAST_GROUP, port: int = MULTICAST_PORT) -> _SocketPair:
    recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    recv.bind(("", port))
    mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
    recv.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    recv.setblocking(False)

    send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    send.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    return _SocketPair(recv=recv, send=send)


class WakeArbiter:
    """One arbiter per device. Call `claim(...)` after Porcupine fires."""

    def __init__(
        self,
        device_id: str,
        priority: int,
        secret: bytes,
        sockets: Optional[_SocketPair] = None,
        vote_window_ms: int = DEFAULT_VOTE_WINDOW_MS,
        group: str = MULTICAST_GROUP,
        port: int = MULTICAST_PORT,
    ) -> None:
        self.device_id = device_id
        self.priority = priority
        self.secret = secret
        self.vote_window_ms = vote_window_ms
        self.group = group
        self.port = port
        self._sockets = sockets

    def _ensure_sockets(self) -> _SocketPair:
        if self._sockets is None:
            self._sockets = _make_default_sockets(self.group, self.port)
        return self._sockets

    def close(self) -> None:
        if self._sockets is not None:
            try:
                self._sockets.recv.close()
            except OSError:
                pass
            try:
                self._sockets.send.close()
            except OSError:
                pass
            self._sockets = None

    def __enter__(self) -> "WakeArbiter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    async def claim(self, rms_dbfs: float, confidence: float = 1.0) -> ArbitrationResult:
        """Broadcast our claim, listen for peers, decide.

        Returns WON, YIELDED, or SOLO. SOLO means no peer responded in time.
        """
        socks = self._ensure_sockets()

        my_claim = WakeClaim(
            device_id=self.device_id,
            rms_dbfs=rms_dbfs,
            confidence=confidence,
            ts_ms=int(time.time() * 1000),
            priority=self.priority,
            schema_version=1,
        )
        packet = _serialize(my_claim, self.secret)
        socks.send.sendto(packet, (self.group, self.port))

        deadline = time.monotonic() + (self.vote_window_ms / 1000.0)
        peer_claims: list[WakeClaim] = []
        loop = asyncio.get_running_loop()

        while True:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            try:
                data = await asyncio.wait_for(
                    loop.run_in_executor(None, _recv_nonblocking, socks.recv),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                break
            if data is None:
                # spurious wakeup (no data)
                await asyncio.sleep(0.005)
                continue
            claim = _deserialize(data, self.secret)
            if claim is None or claim.device_id == self.device_id:
                continue
            peer_claims.append(claim)

        if not peer_claims:
            return ArbitrationResult.SOLO

        my_rank = rank_claim(my_claim)
        beaten = any(rank_claim(c) > my_rank for c in peer_claims)
        return ArbitrationResult.YIELDED if beaten else ArbitrationResult.WON


def _recv_nonblocking(sock: socket.socket) -> Optional[bytes]:
    """Try a single recv. Returns None if no data ready."""
    try:
        return sock.recv(4096)
    except BlockingIOError:
        # block briefly via select
        import select
        r, _, _ = select.select([sock], [], [], 0.05)
        if r:
            try:
                return sock.recv(4096)
            except BlockingIOError:
                return None
        return None
