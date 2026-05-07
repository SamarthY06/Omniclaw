#!/usr/bin/env python3
"""peer_cli.py -- exec'd by OpenClaw to talk to the local peer daemon.

Pattern matches omniclaw/tools/macos_ax.py: every subcommand prints a single
JSON object to stdout (pretty-printed if `--pretty`), exits 0 on success and
non-0 with `{"ok": false, "error": "..."}` on failure.

The agent never holds the peer shared secret. All peer access goes through the
daemon's Unix socket at ~/.jarvis/peer.sock (mode 0600, owner-only).

Subcommands:
  ping              -- daemon -> peer round-trip, returns rtt_ms
  ping --self       -- daemon -> own server loopback (works without pairing)
  caps              -- peer.hello result (peer's advertised capabilities)
  status            -- daemon health snapshot
  verify            -- end-to-end checks (sock perms, ping_self, peer_reachable)
  tools.invoke <name> --args <json>     -- direct tool call on peer
  task.run <intent> --args <json>       -- streamed agent task on peer
  pair show [--host H] [--port P] [--qr]  -- print pairing URI (no daemon needed)
  pair accept <uri>                     -- save peer record (no daemon needed,
                                           but reloads daemon if it's running)
  --json-tools      -- dump function-tool schemas (OpenAI format) for the agent
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from omniclaw.peer.daemon import DEFAULT_SOCK_PATH  # noqa: E402
from omniclaw.peer.pair import (  # noqa: E402
    IdentityRecord,
    PeerRecord,
    create_pairing_payload,
    load_identity,
    load_peer_record,
    payload_from_uri,
    payload_to_uri,
    save_identity,
    save_peer_record,
)


# ---- function-tool schemas (consumed by --json-tools) ---------------------


_TOOL_SCHEMAS = [
    {
        "name": "peer_ping",
        "description": "Round-trip ping the paired Android peer. Returns rtt_ms. Fast (<200ms) when reachable.",
        "parameters": {"type": "object", "properties": {}},
        "sensitivity": "S0",
    },
    {
        "name": "peer_caps",
        "description": "Get the paired phone's advertised tool capabilities (peer.hello). Use to discover what tools the phone exposes before invoking them.",
        "parameters": {"type": "object", "properties": {}},
        "sensitivity": "S0",
    },
    {
        "name": "peer_status",
        "description": "Health snapshot of the local peer daemon: ws port, socket path, whether a peer is paired, peer endpoint.",
        "parameters": {"type": "object", "properties": {}},
        "sensitivity": "S0",
    },
    {
        "name": "peer_tools_invoke",
        "description": "Directly invoke a single phone-side tool (no agent reasoning on the phone). Returns the tool's output. Use when you know exactly which phone tool to call. Sensitivity matches the underlying tool: send_*/pay_*/transfer_*/delete_* are S2+ and require user confirmation.",
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "args": {"type": "object"},
                "deadline_ms": {"type": "integer", "default": 30000},
            },
            "required": ["tool_name"],
        },
        "sensitivity": "S1",
    },
    {
        "name": "peer_task_run",
        "description": "Delegate an open-ended intent to the phone's agent (it plans + executes its own tools). Streams events; final result is whatever the phone agent decided. Use for natural-language phone tasks.",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {"type": "string"},
                "args": {"type": "object"},
                "deadline_ms": {"type": "integer", "default": 60000},
            },
            "required": ["intent"],
        },
        "sensitivity": "S1",
    },
    {
        "name": "peer_pair_show",
        "description": "Generate a pairing URI for the user to scan from the phone. Prints jarvis://pair?... URI. Run once at first setup.",
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Tailscale hostname or LAN IP (auto-detected if omitted)"},
                "port": {"type": "integer", "default": 18790},
                "qr": {"type": "boolean", "default": False},
            },
        },
        "sensitivity": "S2",
    },
    {
        "name": "peer_pair_accept",
        "description": "Accept a pairing URI scanned from the other device.",
        "parameters": {
            "type": "object",
            "properties": {"uri": {"type": "string"}},
            "required": ["uri"],
        },
        "sensitivity": "S2",
    },
    {
        "name": "peer_verify",
        "description": "Run end-to-end checks: daemon alive, socket permissions, ping_self loopback, peer_reachable (if paired). Returns checks dict.",
        "parameters": {"type": "object", "properties": {}},
        "sensitivity": "S0",
    },
]


# ---- daemon RPC over unix socket ------------------------------------------


class DaemonError(RuntimeError):
    pass


def _send_to_daemon(req: dict[str, Any], sock_path: Path = DEFAULT_SOCK_PATH, timeout_s: float = 30.0) -> dict[str, Any]:
    if not sock_path.exists():
        raise DaemonError(
            f"daemon socket not found at {sock_path}; "
            f"is the peer daemon running? "
            f"(launchctl load ~/Library/LaunchAgents/ai.jarvis.peer.plist)"
        )
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout_s)
    try:
        s.connect(str(sock_path))
        s.sendall(json.dumps(req).encode("utf-8") + b"\n")
        s.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).strip()
        if not raw:
            raise DaemonError("empty response from daemon")
        try:
            return json.loads(raw.decode("utf-8").splitlines()[0])
        except json.JSONDecodeError as exc:
            raise DaemonError(f"non-json reply from daemon: {raw!r}") from exc
    finally:
        s.close()


# ---- emit helpers ----------------------------------------------------------


def _emit(out: dict[str, Any], pretty: bool) -> None:
    if pretty:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(out, ensure_ascii=False))


def _fail(msg: str, pretty: bool = False, code: int = 1) -> int:
    _emit({"ok": False, "error": msg}, pretty)
    return code


# ---- subcommand handlers ---------------------------------------------------


def cmd_json_tools(args: argparse.Namespace) -> int:
    _emit({"ok": True, "tools": _TOOL_SCHEMAS}, args.pretty)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        out = _send_to_daemon({"op": "status"}, sock_path=args.sock)
    except DaemonError as exc:
        return _fail(str(exc), args.pretty)
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


def cmd_ping(args: argparse.Namespace) -> int:
    op = "ping_self" if args.self else "ping_peer"
    try:
        out = _send_to_daemon({"op": op}, sock_path=args.sock)
    except DaemonError as exc:
        return _fail(str(exc), args.pretty)
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


def cmd_caps(args: argparse.Namespace) -> int:
    try:
        out = _send_to_daemon({"op": "caps"}, sock_path=args.sock)
    except DaemonError as exc:
        return _fail(str(exc), args.pretty)
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        out = _send_to_daemon({"op": "verify"}, sock_path=args.sock)
    except DaemonError as exc:
        return _fail(str(exc), args.pretty)
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


def cmd_tools_invoke(args: argparse.Namespace) -> int:
    try:
        tool_args = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as exc:
        return _fail(f"--args is not valid JSON: {exc}", args.pretty)
    if not isinstance(tool_args, dict):
        return _fail("--args must be a JSON object", args.pretty)
    try:
        out = _send_to_daemon(
            {
                "op": "tools_invoke",
                "tool_name": args.tool_name,
                "args": tool_args,
                "deadline_ms": args.deadline_ms,
            },
            sock_path=args.sock,
            timeout_s=max(args.deadline_ms / 1000.0 + 5, 10),
        )
    except DaemonError as exc:
        return _fail(str(exc), args.pretty)
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


def cmd_task_run(args: argparse.Namespace) -> int:
    try:
        task_args = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as exc:
        return _fail(f"--args is not valid JSON: {exc}", args.pretty)
    if not isinstance(task_args, dict):
        return _fail("--args must be a JSON object", args.pretty)
    try:
        out = _send_to_daemon(
            {
                "op": "task_run",
                "intent": args.intent,
                "args": task_args,
                "deadline_ms": args.deadline_ms,
            },
            sock_path=args.sock,
            timeout_s=max(args.deadline_ms / 1000.0 + 5, 30),
        )
    except DaemonError as exc:
        return _fail(str(exc), args.pretty)
    _emit(out, args.pretty)
    return 0 if out.get("ok") else 2


def cmd_pair_show(args: argparse.Namespace) -> int:
    """Pair-show works WITHOUT the daemon (file-only). If the daemon is up
    we route through it so it picks the right port; otherwise compute locally.
    """
    if args.sock.exists():
        try:
            out = _send_to_daemon(
                {"op": "pair_show", "host": args.host, "port": args.port, "fingerprint": args.fingerprint},
                sock_path=args.sock,
            )
        except DaemonError:
            out = None
        if out and out.get("ok"):
            uri = out["uri"]
            if args.qr:
                _print_qr(uri)
            if args.png:
                _write_qr_png(uri, args.png)
                out = dict(out)
                out["png"] = str(args.png)
            _emit(out, args.pretty)
            return 0

    identity = load_identity() or _bootstrap_identity()
    payload = create_pairing_payload(
        host=args.host or _detect_host(),
        port=args.port,
        role="mac",
        device_id=identity.device_id,
        fingerprint=args.fingerprint or "",
    )
    uri = payload_to_uri(payload)
    if args.qr:
        _print_qr(uri)
    result = {
        "ok": True,
        "uri": uri,
        "host": payload.host,
        "port": payload.port,
        "secret_b64": payload.secret_b64,
    }
    if args.png:
        _write_qr_png(uri, args.png)
        result["png"] = str(args.png)
    _emit(result, args.pretty)
    return 0


def cmd_pair_accept(args: argparse.Namespace) -> int:
    try:
        payload = payload_from_uri(args.uri)
    except ValueError as exc:
        return _fail(f"invalid pairing URI: {exc}", args.pretty)
    record = PeerRecord(
        peer_device_id=payload.device_id,
        peer_role=payload.role,
        peer_caps=[],
        shared_secret_b64=payload.secret_b64,
        fingerprint=payload.fingerprint,
        last_seen_endpoint=f"ws://{payload.host}:{payload.port}",
    )
    save_peer_record(record)
    daemon_reload = None
    if args.sock.exists():
        try:
            daemon_reload = _send_to_daemon({"op": "reload"}, sock_path=args.sock)
        except DaemonError:
            daemon_reload = None
    _emit(
        {
            "ok": True,
            "peer_device_id": payload.device_id,
            "endpoint": record.last_seen_endpoint,
            "daemon_reload": daemon_reload,
        },
        args.pretty,
    )
    return 0


def _bootstrap_identity() -> IdentityRecord:
    import uuid
    identity = IdentityRecord(
        device_id=f"mac-{uuid.uuid4().hex[:8]}",
        role="mac",
        priority=10,
    )
    save_identity(identity)
    return identity


def _detect_host() -> str:
    # try tailscale, then LAN IP; fall back to localhost.
    try:
        import subprocess
        proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            self_node = data.get("Self") or {}
            dns = self_node.get("DNSName")
            if dns:
                return dns.rstrip(".")
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _print_qr(uri: str) -> None:
    try:
        import qrcode  # type: ignore
        qr = qrcode.QRCode(border=1)
        qr.add_data(uri)
        qr.make(fit=True)
        qr.print_ascii(out=sys.stdout)
    except ImportError:
        sys.stderr.write("(qrcode not installed; install qrcode[pil] to render QR)\n")


def _write_qr_png(uri: str, path: Path) -> None:
    # Render a chunky, low-error-correction QR PNG. ZXing on phone cameras
    # decodes pixel-perfect PNGs orders of magnitude faster than the
    # anti-aliased Unicode-block rendering you get on a Retina terminal.
    #
    # box_size=40 + ERROR_CORRECT_L gives the largest possible per-module
    # area for our pairing URI (~150 chars). Resulting PNG is roughly
    # 1500-1800 px square depending on QR version. Modules end up large
    # enough that the phone can lock from arm's length without precise
    # focusing - which is what the user reported needing after the v0.1.0
    # build's denser QR repeatedly failed on a One UI camera. ECC level L
    # is fine here because pairing URIs are presented inside Preview at
    # full quality with no print/dirt damage to recover from.
    import qrcode  # type: ignore
    from qrcode.constants import ERROR_CORRECT_L  # type: ignore

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_L,
        box_size=40,
        border=6,
    )
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(str(path))


# ---- argument parser -------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Peer-bridge CLI for the OpenClaw agent. Talks to the local daemon over a Unix socket.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sock",
        type=Path,
        default=DEFAULT_SOCK_PATH,
        help=f"Daemon Unix socket path (default: {DEFAULT_SOCK_PATH})",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument(
        "--json-tools",
        action="store_true",
        help="Dump function-tool schemas (OpenAI format) and exit. Used by the agent.",
    )

    sub = parser.add_subparsers(dest="command", required=False)

    p = sub.add_parser("status", help="Daemon health snapshot.")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("ping", help="Round-trip ping the paired peer.")
    p.add_argument("--self", action="store_true", help="Loopback to our own server.")
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("caps", help="Fetch peer.hello -> peer's tool capabilities.")
    p.set_defaults(func=cmd_caps)

    p = sub.add_parser("verify", help="End-to-end verify: socket, ping_self, peer_reachable.")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("tools.invoke", help="Direct tool call on the peer.")
    p.add_argument("tool_name", help="Tool name advertised by peer.caps.")
    p.add_argument("--args", default="{}", help="JSON object of tool arguments.")
    p.add_argument("--deadline-ms", type=int, default=30_000)
    p.set_defaults(func=cmd_tools_invoke)

    p = sub.add_parser("task.run", help="Delegate a high-level intent to the peer's agent.")
    p.add_argument("intent", help="Free-form intent string.")
    p.add_argument("--args", default="{}", help="JSON object of intent arguments.")
    p.add_argument("--deadline-ms", type=int, default=60_000)
    p.set_defaults(func=cmd_task_run)

    p = sub.add_parser("pair", help="Pairing operations (show / accept).")
    pair_sub = p.add_subparsers(dest="pair_command", required=True)

    ps = pair_sub.add_parser("show", help="Generate pairing URI for the user to scan.")
    ps.add_argument("--host", default=None, help="Override host (Tailscale FQDN or LAN IP).")
    ps.add_argument("--port", type=int, default=18790)
    ps.add_argument("--fingerprint", default="")
    ps.add_argument("--qr", action="store_true", help="Print QR code to stdout.")
    ps.add_argument(
        "--png",
        type=Path,
        default=None,
        help="Also write a high-resolution QR PNG to this path. ZXing scans this much faster than the terminal QR.",
    )
    ps.set_defaults(func=cmd_pair_show)

    pa = pair_sub.add_parser("accept", help="Accept a pairing URI from the peer.")
    pa.add_argument("uri", help="jarvis://pair?... URI scanned from the peer.")
    pa.set_defaults(func=cmd_pair_accept)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.json_tools:
        return cmd_json_tools(args)

    if not getattr(args, "command", None):
        parser.print_usage(file=sys.stderr)
        return _fail("no command given (try `--json-tools` to discover capabilities)", args.pretty)

    if args.command == "pair" and not getattr(args, "func", None):
        parser.print_usage(file=sys.stderr)
        return _fail("pair requires a subcommand: show or accept", args.pretty)

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # pragma: no cover - last-resort
        return _fail(f"{type(exc).__name__}: {exc}", args.pretty)


if __name__ == "__main__":
    sys.exit(main())
