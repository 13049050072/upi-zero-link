"""Proxy input normalization shared by the CLI, protocol, and service server."""
from __future__ import annotations

from urllib.parse import quote


def normalize_proxy(value: str) -> str:
    """Accept a proxy URL or provider-style host:port:user:password value."""
    proxy = value.strip()
    if not proxy or "://" in proxy:
        return proxy

    parts = proxy.split(":", 3)
    if len(parts) != 4:
        return proxy
    host, port, username, password = (part.strip() for part in parts)
    if not host or not port or not username or not password:
        return proxy
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        return proxy
    return "http://%s:%s@%s:%s" % (
        quote(username, safe=""),
        quote(password, safe=""),
        host,
        port,
    )
