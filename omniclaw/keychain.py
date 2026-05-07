"""Thin wrapper around the macOS `security` CLI for Keychain secret access.

Stores secrets under service "jarvis" with the secret name as the account.
Falls back to environment variables when the keychain isn't available
(handy for tests and Linux CI).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

SERVICE = "jarvis"


class KeychainError(RuntimeError):
    pass


def _security_available() -> bool:
    return shutil.which("security") is not None


def get_secret(name: str, env_fallback: bool = True) -> Optional[str]:
    """Return the secret value, or None if not found.

    Order: macOS Keychain → environment variable (if env_fallback).
    """
    if _security_available():
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    if env_fallback:
        return os.environ.get(name)
    return None


def require_secret(name: str) -> str:
    """Like get_secret but raises if missing."""
    val = get_secret(name)
    if not val:
        raise KeychainError(
            f"Secret {name!r} not found. "
            f"Add it with: security add-generic-password -s {SERVICE} -a {name} -w <value> "
            f"(or export {name}=<value>)."
        )
    return val


def set_secret(name: str, value: str) -> None:
    """Store a secret in the keychain. Overwrites any existing entry."""
    if not _security_available():
        raise KeychainError("`security` CLI not available; cannot write to Keychain.")
    subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", name],
        capture_output=True,
    )
    proc = subprocess.run(
        ["security", "add-generic-password", "-s", SERVICE, "-a", name, "-w", value, "-U"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise KeychainError(f"keychain write failed: {proc.stderr.strip()}")


def delete_secret(name: str) -> bool:
    """Delete a secret. Returns True if it existed."""
    if not _security_available():
        return False
    proc = subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", name],
        capture_output=True,
    )
    return proc.returncode == 0


# Public secret name constants used elsewhere in the codebase.
OPENAI_API_KEY = "OPENAI_API_KEY"
PORCUPINE_ACCESS_KEY = "PORCUPINE_ACCESS_KEY"
PEER_SHARED_SECRET = "PEER_SHARED_SECRET"
