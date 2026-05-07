"""pytest fixtures."""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

import pytest


# Ensure the omniclaw package (this directory's parent) is importable.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def shared_secret() -> bytes:
    return b"\x00" * 32  # deterministic test secret


@pytest.fixture
def free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port




@pytest.fixture(autouse=True)
def _isolated_jarvis_home(tmp_path, monkeypatch):
    """Point ~/.jarvis at a fresh temp dir for every test."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Re-import any module that read ~/.jarvis/peer/peer.json at import time
    # would be a problem. We only need PEER_DIR / PEER_FILE / IDENTITY_FILE
    # to point at the new HOME, so monkeypatch them explicitly.
    from omniclaw.peer import pair as _pair_mod
    new_dir = fake_home / ".jarvis" / "peer"
    monkeypatch.setattr(_pair_mod, "PEER_DIR", new_dir)
    monkeypatch.setattr(_pair_mod, "PEER_FILE", new_dir / "peer.json")
    monkeypatch.setattr(_pair_mod, "IDENTITY_FILE", new_dir / "identity.json")
    yield fake_home
