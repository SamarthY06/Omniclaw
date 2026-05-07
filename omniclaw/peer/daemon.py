"""Long-lived peer daemon for the Mac side.

Owns:
  * The peer WS server (port 18790, secure-by-default via shared HMAC, optional
    tailnet binding when out of LAN).
  * A persistent peer WS client to the paired Android, lazily reconnected.
  * The wake-word UDP arbiter (best-effort multicast).
  * A local control-plane Unix socket at ~/.jarvis/peer.sock with mode 0600.
    `peer_cli.py` (exec'd by the OpenClaw agent) talks to the daemon over this
    socket. The agent never holds the peer secret directly.

Process is meant to run under launchd (KeepAlive=true). On crash it just dies;
launchd restarts it. State (peer.json, identity.json) lives on disk.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import socket
import stat
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, Optional

from omniclaw import keychain
from omniclaw.peer.client import PeerClient, PeerClientError, RemoteError
from omniclaw.peer.pair import (
    IDENTITY_FILE,
    PEER_DIR,
    PEER_FILE,
    IdentityRecord,
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
from omniclaw.peer.server import HandlerContext, PeerServer
from omniclaw.proto.types import (
    HelloParams,
    HelloResult,
    PingParams,
    PingResult,
    SCHEMA_MAX,
    SCHEMA_MIN,
    SCHEMA_VERSION,
)

LOG = logging.getLogger("omniclaw.peer.daemon")

# ----- defaults -------------------------------------------------------------

DEFAULT_PEER_PORT = 18790
DEFAULT_SOCK_PATH = Path(os.path.expanduser("~/.jarvis/peer.sock"))
DEFAULT_LOG_PATH = Path(os.path.expanduser("~/.jarvis/peer.log"))
DEFAULT_PRIORITY_MAC = 10

LOCAL_CAPS = [
    "tool:mac_ax",
    "tool:mac_screenshot",
    "tool:mac_screen_size",
    "tool:mac_focused_app",
    "tool:mac_list_apps",
    "tool:mac_launch",
    "tool:mac_focus",
    "tool:mac_quit",
    "tool:mac_tree",
    "tool:mac_click",
    "tool:mac_type",
    "tool:mac_shortcut",
    "tool:mac_scroll",
]


# ---- Daemon ---------------------------------------------------------------


class PeerDaemon:
    """Single asyncio event-loop daemon."""

    def __init__(
        self,
        identity: IdentityRecord,
        secret: bytes,
        peer_port: int = DEFAULT_PEER_PORT,
        sock_path: Path = DEFAULT_SOCK_PATH,
        peer_record: Optional[PeerRecord] = None,
        host: str = "0.0.0.0",
    ) -> None:
        self.identity = identity
        self.secret = secret
        self.peer_record = peer_record
        self.peer_port = peer_port
        self.sock_path = sock_path
        self.host = host

        self._server = PeerServer(
            device_id=identity.device_id,
            secret=secret,
            handlers=self._make_handlers(),
            host=host,
            port=peer_port,
        )
        self._client: Optional[PeerClient] = None
        self._client_lock = asyncio.Lock()
        self._control_server: Optional[asyncio.base_events.Server] = None
        self._stop_event = asyncio.Event()
        self._stop_invoked = False

    # ---- handlers (incoming RPCs from the phone) -------------------------

    def _make_handlers(self) -> dict[str, Any]:
        return {
            "peer.hello": self._on_hello,
            "peer.ping": self._on_ping,
            "tools.invoke": self._on_tools_invoke,
        }

    async def _on_hello(self, params: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        # Peer announced itself; we may want to pin it to our peer_record.
        try:
            HelloParams.model_validate(params)
        except Exception as exc:
            return HelloResult(
                device_id=self.identity.device_id,
                role="mac",
                caps=LOCAL_CAPS,
            ).model_dump() | {"error": f"invalid_hello: {exc}"}

        return HelloResult(
            schema_version=SCHEMA_VERSION,
            device_id=self.identity.device_id,
            role="mac",
            caps=LOCAL_CAPS,
            schema_min=SCHEMA_MIN,
            schema_max=SCHEMA_MAX,
        ).model_dump()

    async def _on_ping(self, params: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        try:
            sent = PingParams.model_validate(params).ts_ms
        except Exception:
            sent = 0
        now = int(time.time() * 1000)
        return PingResult(sent_ts_ms=sent, recv_ts_ms=now, peer_ts_ms=now).model_dump()

    async def _on_tools_invoke(self, params: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
        """Mac-side tools.invoke handler. Currently we only know how to dispatch
        mac_* tools by shelling out to omniclaw/tools/macos_ax.py. The OpenClaw
        agent process is the better dispatcher long-term; this is a minimal
        bridge for the phone to call mac_screen_size / mac_screenshot / etc.
        """
        tool_name = params.get("tool_name", "")
        args = params.get("args", {}) or {}
        if not tool_name.startswith("mac_"):
            return {"ok": False, "error": f"daemon_only_supports_mac_tools_for_now: {tool_name}"}
        return await _run_mac_ax(tool_name, args)

    # ---- outgoing client (we initiate calls to the peer) ----------------

    async def _ensure_client(self) -> PeerClient:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if not self.peer_record:
                raise RuntimeError("no_peer_paired")
            endpoint = self.peer_record.last_seen_endpoint or self._guess_endpoint(self.peer_record)
            client = PeerClient(
                device_id=self.identity.device_id,
                secret=shared_secret_bytes(self.peer_record),
                endpoint=endpoint,
            )
            await client.connect()
            self._client = client
            return client

    @staticmethod
    def _guess_endpoint(p: PeerRecord) -> str:
        # PeerRecord doesn't store a host directly; rely on last_seen_endpoint
        # being filled by pairing flow. Fall back to localhost for dev.
        return p.last_seen_endpoint or "ws://127.0.0.1:18790"

    async def call_peer(self, method: str, params: dict[str, Any], timeout_s: float = 10) -> dict[str, Any]:
        client = await self._ensure_client()
        try:
            return await client.call(method, params, timeout_s=timeout_s)
        except (PeerClientError, RemoteError, ConnectionError) as exc:
            # one-shot reconnect on broken pipe
            with suppress(Exception):
                await client.close()
            self._client = None
            client = await self._ensure_client()
            return await client.call(method, params, timeout_s=timeout_s)

    async def stream_peer_task(self, intent: str, args: dict[str, Any], deadline_ms: int = 60_000):
        client = await self._ensure_client()
        run_id = str(uuid.uuid4())
        async with client.stream(
            "task.run",
            {
                "run_id": run_id,
                "intent": intent,
                "args": args,
                "allow_remote_tools": True,
                "deadline_ms": deadline_ms,
            },
        ) as (queue, fut):
            yield {"type": "started", "run_id": run_id}
            while not fut.done():
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                yield ev
            result = await fut
            yield {"type": "result", "run_id": run_id, "result": result}

    # ---- control plane (Unix socket) ------------------------------------

    async def _start_control(self) -> None:
        # Create the directory; chmod the socket to 0600 after binding.
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.sock_path.exists():
            try:
                self.sock_path.unlink()
            except OSError:
                pass
        self._control_server = await asyncio.start_unix_server(
            self._handle_control, path=str(self.sock_path)
        )
        try:
            os.chmod(self.sock_path, 0o600)
        except OSError:
            LOG.warning("could not chmod %s to 0600", self.sock_path)

    async def _handle_control(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw:
                return
            try:
                req = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                await self._reply(writer, {"ok": False, "error": f"bad_json: {exc}"})
                return

            op = req.get("op", "")
            handler = _CONTROL_OPS.get(op)
            if not handler:
                await self._reply(writer, {"ok": False, "error": f"unknown_op: {op}"})
                return
            try:
                resp = await handler(self, req)
            except Exception as exc:
                resp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            await self._reply(writer, resp)
        finally:
            with suppress(Exception):
                writer.close()
                await writer.wait_closed()

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write(json.dumps(payload).encode("utf-8") + b"\n")
        with suppress(Exception):
            await writer.drain()

    # ---- run / stop ------------------------------------------------------

    async def run(self) -> None:
        await self._server.start()
        await self._start_control()
        LOG.info(
            "daemon up: ws=%s:%s sock=%s peer_paired=%s",
            self.host,
            self._server.actual_port,
            self.sock_path,
            self.peer_record is not None,
        )
        try:
            await self._stop_event.wait()
        finally:
            with suppress(Exception):
                await self._server.stop()
            if self._client is not None:
                with suppress(Exception):
                    await self._client.close()
                self._client = None
            if self._control_server is not None:
                self._control_server.close()
                with suppress(Exception):
                    await self._control_server.wait_closed()
            with suppress(FileNotFoundError):
                self.sock_path.unlink()

    def stop(self) -> None:
        if not self._stop_invoked:
            self._stop_invoked = True
            self._stop_event.set()


# ---- helper: shell out to macos_ax.py -------------------------------------


async def _run_mac_ax(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a mac_* tool by shelling out to omniclaw/tools/macos_ax.py.

    Mapping rule: `mac_<verb>` becomes the `<verb>` subcommand; underscores in
    the verb become hyphens. Each tool's positional / flag args follow the
    macos_ax.py argparse conventions.
    """
    verb = tool_name[len("mac_"):]
    cli_subcommand = verb.replace("_", "-")

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "omniclaw" / "tools" / "macos_ax.py"
    if not script.exists():
        return {"ok": False, "error": f"macos_ax.py not found at {script}"}

    cmd = [sys.executable, str(script), cli_subcommand]

    # Positional vs flag arg mapping. Keep this small & well-known; the agent
    # is expected to use macos_ax.py directly via OpenClaw exec for the
    # complex calls. The peer bridge is for cross-device scripted use.
    positional_args = {
        "launch": ["app"],
        "focus": ["app"],
        "quit": ["app"],
        "type": ["text"],
        "shortcut": ["keys"],
        "scroll": ["direction", "amount"],
        "click-at": ["x", "y"],
        "hover": ["x", "y"],
        "drag": ["start_x", "start_y", "end_x", "end_y"],
    }

    pos_keys = positional_args.get(cli_subcommand, [])
    consumed = set()
    for key in pos_keys:
        if key in args and args[key] is not None:
            cmd.append(str(args[key]))
            consumed.add(key)
        elif key in args:
            consumed.add(key)
    for key, value in args.items():
        if key in consumed or value is None:
            continue
        if isinstance(value, bool):
            if value:
                cmd.append(f"--{key.replace('_', '-')}")
        else:
            cmd.extend([f"--{key.replace('_', '-')}", str(value)])

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    out_str = out.decode("utf-8", errors="replace").strip()
    err_str = err.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        return {"ok": False, "error": err_str or out_str or f"exit_{proc.returncode}"}
    try:
        parsed = json.loads(out_str)
    except json.JSONDecodeError:
        return {"ok": True, "output": {"raw": out_str}}
    return {"ok": parsed.get("ok", True), "output": parsed}


# ----- Control-plane operations --------------------------------------------


async def _op_status(d: PeerDaemon, _req: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "device_id": d.identity.device_id,
        "role": d.identity.role,
        "ws_port": d._server.actual_port,
        "sock_path": str(d.sock_path),
        "peer_paired": d.peer_record is not None,
        "peer_device_id": d.peer_record.peer_device_id if d.peer_record else None,
        "peer_caps": d.peer_record.peer_caps if d.peer_record else [],
        "peer_endpoint": d.peer_record.last_seen_endpoint if d.peer_record else None,
        "schema_version": SCHEMA_VERSION,
    }


async def _op_ping_self(d: PeerDaemon, _req: dict[str, Any]) -> dict[str, Any]:
    """Connect a fresh client to our own server and round-trip peer.ping.

    Useful for `peer_cli.py verify` even before pairing.
    """
    endpoint = f"ws://127.0.0.1:{d._server.actual_port}"
    client = PeerClient(
        device_id=d.identity.device_id,
        secret=d.secret,
        endpoint=endpoint,
    )
    await client.connect()
    try:
        sent = int(time.time() * 1000)
        out = await client.call("peer.ping", {"ts_ms": sent}, timeout_s=5)
        recv = int(time.time() * 1000)
        return {"ok": True, "rtt_ms": recv - sent, "result": out}
    finally:
        await client.close()


async def _op_ping_peer(d: PeerDaemon, _req: dict[str, Any]) -> dict[str, Any]:
    if not d.peer_record:
        return {"ok": False, "error": "no_peer_paired"}
    sent = int(time.time() * 1000)
    out = await d.call_peer("peer.ping", {"ts_ms": sent}, timeout_s=5)
    recv = int(time.time() * 1000)
    return {"ok": True, "rtt_ms": recv - sent, "result": out}


async def _op_caps(d: PeerDaemon, _req: dict[str, Any]) -> dict[str, Any]:
    if not d.peer_record:
        return {"ok": False, "error": "no_peer_paired"}
    out = await d.call_peer(
        "peer.hello",
        HelloParams(
            schema_version=SCHEMA_VERSION,
            device_id=d.identity.device_id,
            role="mac",
            caps=LOCAL_CAPS,
        ).model_dump(),
        timeout_s=5,
    )
    return {"ok": True, "result": out}


async def _op_tools_invoke(d: PeerDaemon, req: dict[str, Any]) -> dict[str, Any]:
    if not d.peer_record:
        return {"ok": False, "error": "no_peer_paired"}
    tool_name = req.get("tool_name")
    args = req.get("args", {}) or {}
    deadline_ms = int(req.get("deadline_ms") or 30_000)
    if not tool_name:
        return {"ok": False, "error": "missing_tool_name"}
    out = await d.call_peer(
        "tools.invoke",
        {
            "tool_name": tool_name,
            "args": args,
            "deadline_ms": deadline_ms,
        },
        timeout_s=deadline_ms / 1000.0 + 5,
    )
    return {"ok": True, "result": out}


async def _op_task_run(d: PeerDaemon, req: dict[str, Any]) -> dict[str, Any]:
    if not d.peer_record:
        return {"ok": False, "error": "no_peer_paired"}
    intent = req.get("intent")
    args = req.get("args", {}) or {}
    deadline_ms = int(req.get("deadline_ms") or 60_000)
    if not intent:
        return {"ok": False, "error": "missing_intent"}
    events: list[dict[str, Any]] = []
    final = None
    async for ev in d.stream_peer_task(intent, args, deadline_ms=deadline_ms):
        if ev.get("type") == "result":
            final = ev["result"]
        else:
            events.append(ev)
    return {"ok": True, "events": events, "result": final}


async def _op_pair_show(d: PeerDaemon, req: dict[str, Any]) -> dict[str, Any]:
    host = req.get("host") or _detect_tailscale_host() or _local_ip()
    port = int(req.get("port") or d._server.actual_port)
    payload = create_pairing_payload(
        host=host,
        port=port,
        role=d.identity.role,
        device_id=d.identity.device_id,
        fingerprint=req.get("fingerprint", ""),
    )
    return {
        "ok": True,
        "uri": payload_to_uri(payload),
        "host": host,
        "port": port,
        "secret_b64": payload.secret_b64,
    }


async def _op_pair_accept(d: PeerDaemon, req: dict[str, Any]) -> dict[str, Any]:
    uri = req.get("uri")
    if not uri:
        return {"ok": False, "error": "missing_uri"}
    payload = payload_from_uri(uri)
    record = PeerRecord(
        peer_device_id=payload.device_id,
        peer_role=payload.role,
        peer_caps=[],
        shared_secret_b64=payload.secret_b64,
        fingerprint=payload.fingerprint,
        last_seen_endpoint=f"ws://{payload.host}:{payload.port}",
    )
    save_peer_record(record)
    if d._client is not None:
        with suppress(Exception):
            await d._client.close()
        d._client = None
    d.peer_record = record
    return {"ok": True, "peer_device_id": payload.device_id, "endpoint": record.last_seen_endpoint}


async def _op_reload(d: PeerDaemon, _req: dict[str, Any]) -> dict[str, Any]:
    """Re-read peer.json from disk (called after `pair accept` from elsewhere)."""
    new_peer = load_peer_record()
    if d._client is not None:
        with suppress(Exception):
            await d._client.close()
        d._client = None
    d.peer_record = new_peer
    return {"ok": True, "peer_paired": new_peer is not None}


async def _op_verify(d: PeerDaemon, _req: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "daemon_alive": True,
        "ws_port": d._server.actual_port,
        "sock_perms_ok": _check_sock_perms(d.sock_path),
        "ping_self": False,
        "peer_paired": d.peer_record is not None,
        "peer_reachable": False,
    }
    try:
        out = await _op_ping_self(d, {})
        checks["ping_self"] = bool(out.get("ok"))
    except Exception as exc:  # pragma: no cover - defensive
        checks["ping_self_error"] = str(exc)
    if d.peer_record:
        try:
            out = await asyncio.wait_for(_op_ping_peer(d, {}), timeout=5)
            checks["peer_reachable"] = bool(out.get("ok"))
        except Exception as exc:
            checks["peer_reachable_error"] = str(exc)
    return {"ok": all([checks["daemon_alive"], checks["sock_perms_ok"], checks["ping_self"]]), "checks": checks}


_CONTROL_OPS = {
    "status": _op_status,
    "ping_self": _op_ping_self,
    "ping_peer": _op_ping_peer,
    "caps": _op_caps,
    "tools_invoke": _op_tools_invoke,
    "task_run": _op_task_run,
    "pair_show": _op_pair_show,
    "pair_accept": _op_pair_accept,
    "reload": _op_reload,
    "verify": _op_verify,
}


# ----- helpers --------------------------------------------------------------


def _check_sock_perms(path: Path) -> bool:
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    # Owner read+write only (0o600 on the socket inode).
    return (st.st_mode & 0o777) == 0o600


def _detect_tailscale_host() -> Optional[str]:
    """Best-effort tailscale MagicDNS hostname (`tailscale status --self`).

    Returns None if tailscale not installed or the command fails.
    """
    try:
        proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        self_node = data.get("Self") or {}
        dns = self_node.get("DNSName")
        if dns:
            return dns.rstrip(".")
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return None
    return None


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _ensure_identity() -> IdentityRecord:
    existing = load_identity()
    if existing:
        return existing
    identity = IdentityRecord(
        device_id=f"mac-{uuid.uuid4().hex[:8]}",
        role="mac",
        priority=DEFAULT_PRIORITY_MAC,
    )
    save_identity(identity)
    return identity


def _ensure_secret() -> bytes:
    """Resolve PEER_SHARED_SECRET. If absent, generate one and store it.

    The shared secret is symmetric — the same value lives on both Mac and
    Android, established at pairing time. We generate on the Mac if missing
    so the daemon can start (the secret will be re-set when pairing).
    """
    raw = keychain.get_secret(keychain.PEER_SHARED_SECRET)
    if raw:
        try:
            return base64.urlsafe_b64decode(raw)
        except Exception:
            return raw.encode("utf-8")
    # bootstrap a placeholder so the WS server can run; pair_accept will
    # overwrite when pairing completes.
    LOG.warning("PEER_SHARED_SECRET not set; generating a placeholder for self-mode")
    secret = os.urandom(32)
    try:
        keychain.set_secret(
            keychain.PEER_SHARED_SECRET,
            base64.urlsafe_b64encode(secret).decode("ascii"),
        )
    except Exception:
        pass
    return secret


def _setup_logging(log_path: Path, level: int = logging.INFO) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.FileHandler(log_path))
    except OSError:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


# ----- entrypoint -----------------------------------------------------------


async def _async_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Jarvis peer daemon (Mac side).")
    parser.add_argument("--port", type=int, default=DEFAULT_PEER_PORT)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--sock", type=Path, default=DEFAULT_SOCK_PATH)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    _setup_logging(args.log, getattr(logging, args.log_level.upper(), logging.INFO))

    identity = _ensure_identity()
    peer = load_peer_record()
    secret = _ensure_secret()

    # If we have a paired peer, prefer its shared secret for our own identity
    # (we serve under the SAME secret the peer signs with).
    if peer:
        secret = shared_secret_bytes(peer)

    daemon = PeerDaemon(
        identity=identity,
        secret=secret,
        peer_port=args.port,
        sock_path=args.sock,
        peer_record=peer,
        host=args.host,
    )

    loop = asyncio.get_running_loop()
    try:
        import signal
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, daemon.stop)
    except NotImplementedError:  # pragma: no cover - non-unix
        pass

    await daemon.run()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return asyncio.run(_async_main(argv))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
