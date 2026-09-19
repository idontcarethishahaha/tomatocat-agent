"""Process-wide network policy for TUN-based routing."""

from __future__ import annotations

import os


_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "FTP_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "ftp_proxy",
)


def configure_tun_routing() -> tuple[str, ...]:
    """Prevent explicit proxies from layering on top of the system TUN route."""
    removed = tuple(key for key in _PROXY_ENV_KEYS if os.environ.get(key))
    for key in _PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    return removed
