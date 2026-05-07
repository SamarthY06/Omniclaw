"""QR-based pairing flow + peer record persistence at ~/.jarvis/peer/."""
from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.parse
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


PEER_DIR = Path(os.path.expanduser("~/.jarvis/peer"))
PEER_FILE = PEER_DIR / "peer.json"
IDENTITY_FILE = PEER_DIR / "identity.json"


@dataclass
class PairingPayload:
    """The blob encoded into the QR. Sent Mac -> Android during pairing."""
    host: str
    port: int
    fingerprint: str
    secret_b64: str
    role: str  # "mac" or "android"
    device_id: str
    schema_version: int = 1


@dataclass
class PeerRecord:
    """What each device persists about its paired peer."""
    peer_device_id: str
    peer_role: str
    peer_caps: list[str] = field(default_factory=list)
    shared_secret_b64: str = ""
    fingerprint: str = ""
    last_seen_endpoint: Optional[str] = None  # "tailscale-host:port"
    schema_version: int = 1


@dataclass
class IdentityRecord:
    """This device's own identity."""
    device_id: str
    role: str
    priority: int = 10  # used in wake arbitration tiebreaks


# ---------------------------------------------------------------------------
# Payload <-> URI

URI_SCHEME = "jarvis://pair"


def payload_to_uri(p: PairingPayload) -> str:
    qs = urllib.parse.urlencode({
        "host": p.host,
        "port": str(p.port),
        "fp": p.fingerprint,
        "secret": p.secret_b64,
        "role": p.role,
        "id": p.device_id,
        "v": str(p.schema_version),
    })
    return f"{URI_SCHEME}?{qs}"


def payload_from_uri(uri: str) -> PairingPayload:
    parsed = urllib.parse.urlparse(uri)
    if f"{parsed.scheme}://{parsed.netloc}{parsed.path}" not in (URI_SCHEME, URI_SCHEME + "/"):
        # Allow either "jarvis://pair?..." or "jarvis://pair/?..."
        if not uri.startswith(URI_SCHEME):
            raise ValueError(f"not a {URI_SCHEME} URI: {uri!r}")
    # keep_blank_values=True so optional fields like fingerprint round-trip when empty.
    q = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def _required(name: str) -> str:
        if name not in q or not q[name] or not q[name][0]:
            raise ValueError(f"pairing URI missing {name!r}")
        return q[name][0]

    def _optional(name: str, default: str = "") -> str:
        if name not in q or not q[name]:
            return default
        return q[name][0]

    return PairingPayload(
        host=_required("host"),
        port=int(_required("port")),
        fingerprint=_optional("fp"),
        secret_b64=_required("secret"),
        role=_required("role"),
        device_id=_required("id"),
        schema_version=int(q.get("v", ["1"])[0] or "1"),
    )


# ---------------------------------------------------------------------------
# Pairing actions

def create_pairing_payload(host: str, port: int, role: str, device_id: str, fingerprint: str = "") -> PairingPayload:
    """Generate a fresh shared-secret payload. Mac calls this and shows the QR."""
    secret = secrets.token_bytes(32)
    return PairingPayload(
        host=host,
        port=port,
        fingerprint=fingerprint,
        secret_b64=base64.urlsafe_b64encode(secret).decode("ascii"),
        role=role,
        device_id=device_id,
    )


def save_peer_record(record: PeerRecord, path: Path = PEER_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), indent=2))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_peer_record(path: Path = PEER_FILE) -> Optional[PeerRecord]:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return PeerRecord(**data)


def save_identity(identity: IdentityRecord, path: Path = IDENTITY_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(identity), indent=2))


def load_identity(path: Path = IDENTITY_FILE) -> Optional[IdentityRecord]:
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return IdentityRecord(**data)


def shared_secret_bytes(record: PeerRecord) -> bytes:
    return base64.urlsafe_b64decode(record.shared_secret_b64.encode("ascii"))
