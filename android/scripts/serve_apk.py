#!/usr/bin/env python3
"""Serve the Ben debug APK over the local network so a phone can download
and install it WITHOUT a USB cable.

Usage:
    .venv/bin/python android/scripts/serve_apk.py [path/to/Ben.apk] [--port N]

What it does:
    1. Starts a tiny HTTP server bound to all interfaces (default: random port).
    2. Detects the Mac's LAN IP.
    3. Prints a scannable ASCII QR code for "http://<lan-ip>:<port>/<apk>".
    4. Logs each download so you know when the phone has finished pulling.

Phone side:
    - Be on the same WiFi as the Mac.
    - Open the camera app, scan the QR (or use any QR scanner). Tap the URL.
    - Browser downloads the APK, then taps to install. Make sure
      "Install unknown apps" is allowed for your browser, AND that Samsung
      Auto Blocker / Play Protect are off if they previously blocked you
      (see BEN_ANDROID_SETUP.md "Sideload on Android 14 / One UI" section).

If the phone's browser shows "connection refused", your Mac firewall is
probably blocking inbound traffic on the chosen port. Disable it for the
session, or pick a port that's already allowed.
"""
from __future__ import annotations

import argparse
import http.server
import socket
import socketserver
import sys
from pathlib import Path

import qrcode  # type: ignore[import-untyped]


def _lan_ip() -> str:
    """Best-effort LAN IP detection: open a UDP socket to a public IP and
    read the chosen local address. Doesn't actually send traffic."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _build_handler(serve_dir: Path) -> type[http.server.SimpleHTTPRequestHandler]:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):  # type: ignore[no-untyped-def]
            super().__init__(*a, directory=str(serve_dir), **kw)

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
            sys.stderr.write(
                f"[serve_apk] {self.client_address[0]} - {fmt % args}\n"
            )

        def end_headers(self) -> None:
            # Help mobile browsers offer "open with package installer" rather
            # than treating the file as octet-stream of unknown type.
            if self.path.lower().endswith(".apk"):
                self.send_header(
                    "Content-Type",
                    "application/vnd.android.package-archive",
                )
            super().end_headers()

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "apk",
        nargs="?",
        default=str(Path.home() / "Desktop" / "Ben.apk"),
        help="Path to the APK to serve (default: ~/Desktop/Ben.apk).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="TCP port (default: pick a free one).",
    )
    args = parser.parse_args()

    apk_path = Path(args.apk).expanduser().resolve()
    if not apk_path.is_file():
        print(f"[serve_apk] APK not found: {apk_path}", file=sys.stderr)
        return 1

    serve_dir = apk_path.parent
    handler_cls = _build_handler(serve_dir)

    # ThreadingTCPServer so multiple devices can hit the URL at once
    # (e.g. user retries while a partial download is still in flight).
    class _Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    with _Server(("0.0.0.0", args.port), handler_cls) as httpd:
        port = httpd.server_address[1]
        ip = _lan_ip()
        url = f"http://{ip}:{port}/{apk_path.name}"
        size_mb = apk_path.stat().st_size // (1024 * 1024)

        print()
        print(f"  Serving {apk_path.name} ({size_mb} MB) at:")
        print(f"    {url}")
        print()
        print("  Scan with your phone's camera (or any QR scanner):")
        print()

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        # invert=True prints with a light background so terminal themes don't
        # blow it out; works in both light and dark terminals.
        qr.print_ascii(invert=True)

        print()
        print("  Then tap the URL on the phone -> browser downloads the APK ->")
        print("  tap to install. (Make sure Auto Blocker + Play Protect are off")
        print("  if they previously blocked you; see BEN_ANDROID_SETUP.md.)")
        print()
        print("  Press Ctrl+C here once the phone has finished installing.")
        print()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve_apk] stopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
