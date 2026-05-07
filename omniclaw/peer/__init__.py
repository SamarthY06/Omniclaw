"""Peer transport: WS server, WS client, mDNS discovery, QR pairing, daemon."""

from omniclaw.peer.client import PeerClient, PeerClientError, RemoteError
from omniclaw.peer.daemon import PeerDaemon
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
)
from omniclaw.peer.server import PeerServer

__all__ = [
    "PeerServer",
    "PeerClient",
    "PeerClientError",
    "RemoteError",
    "PeerDaemon",
    "IdentityRecord",
    "PairingPayload",
    "PeerRecord",
    "create_pairing_payload",
    "load_identity",
    "load_peer_record",
    "payload_from_uri",
    "payload_to_uri",
    "save_identity",
    "save_peer_record",
]
