"""mDNS/Bonjour discovery for the `_jarvis._tcp.local.` service.

`zeroconf` is the underlying library. This module is a thin facade so tests
can swap it for a fake.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Callable, Optional


SERVICE_TYPE = "_jarvis._tcp.local."


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

    def to_ws(self, scheme: str = "ws") -> str:
        return f"{scheme}://{self.host}:{self.port}"


def _try_import_zeroconf():
    try:
        import zeroconf  # noqa: F401
        return zeroconf
    except ImportError:
        return None


class Discovery:
    """Best-effort mDNS publisher + browser. Falls back gracefully."""

    def __init__(self, instance_name: str = "jarvis") -> None:
        self.instance_name = instance_name
        self._zc = None
        self._service_info = None

    def publish(self, port: int, props: Optional[dict[str, str]] = None) -> bool:
        """Register this device's WS endpoint. Returns True on success."""
        zc = _try_import_zeroconf()
        if not zc:
            return False
        ServiceInfo = zc.ServiceInfo
        Zeroconf = zc.Zeroconf

        local_ip = self._local_ip()
        full_name = f"{self.instance_name}.{SERVICE_TYPE}"
        info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=full_name,
            port=port,
            properties=props or {},
            addresses=[socket.inet_aton(local_ip)],
            server=f"{self.instance_name}.local.",
        )
        self._zc = Zeroconf()
        self._service_info = info
        try:
            self._zc.register_service(info)
            return True
        except Exception:
            self._zc = None
            self._service_info = None
            return False

    def unpublish(self) -> None:
        if self._zc and self._service_info:
            try:
                self._zc.unregister_service(self._service_info)
            except Exception:
                pass
            self._zc.close()
        self._zc = None
        self._service_info = None

    def browse_once(self, timeout_s: float = 2.0) -> list[Endpoint]:
        """One-shot browse: list peers seen within `timeout_s`."""
        zc = _try_import_zeroconf()
        if not zc:
            return []
        ZeroconfBrowser = zc.ServiceBrowser
        Zeroconf = zc.Zeroconf
        ServiceListener = zc.ServiceListener

        seen: list[Endpoint] = []

        class _Listener:
            def add_service(self, zeroconf, type, name):
                info = zeroconf.get_service_info(type, name, timeout=int(timeout_s * 1000))
                if not info:
                    return
                addrs = info.parsed_addresses()
                if addrs:
                    seen.append(Endpoint(host=addrs[0], port=info.port))

            def remove_service(self, *_args, **_kwargs):
                pass

            def update_service(self, *_args, **_kwargs):
                pass

        zc_inst = Zeroconf()
        try:
            ZeroconfBrowser(zc_inst, SERVICE_TYPE, _Listener())
            import time
            time.sleep(timeout_s)
        finally:
            zc_inst.close()
        return seen

    @staticmethod
    def _local_ip() -> str:
        """Best guess at our LAN IP."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"
