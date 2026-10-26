"""主機網路介面：後臺網段限制與後臺網址。"""

from __future__ import annotations

import ipaddress
import os
import socket
import time

import psutil

_CACHE_SECONDS = 30.0
_cache: tuple[float, list[ipaddress.IPv4Interface]] | None = None


def local_ipv4_interfaces() -> list[ipaddress.IPv4Interface]:
    """主機各 IPv4 介面（含網段），快取 30 秒。"""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_SECONDS:
        return _cache[1]
    interfaces: list[ipaddress.IPv4Interface] = []
    for addresses in psutil.net_if_addrs().values():
        for address in addresses:
            if address.family != socket.AF_INET or not address.netmask:
                continue
            try:
                interfaces.append(ipaddress.IPv4Interface(f"{address.address}/{address.netmask}"))
            except ValueError:
                continue
    _cache = (now, interfaces)
    return interfaces


def lan_ipv4_addresses() -> list[str]:
    """可供其他裝置連線的區網位址（排除迴路與 169.254 自動位址）。"""
    result = []
    for interface in local_ipv4_interfaces():
        ip = interface.ip
        if ip.is_loopback or ip.is_link_local:
            continue
        result.append(str(ip))
    return sorted(set(result))


def is_admin_client_allowed(host: str | None) -> bool:
    """本機迴路或與主機任一 IPv4 介面同網段的來源才可存取後臺。"""
    if os.environ.get("AP_DRAWART_ADMIN_ALLOW_ALL") == "1":
        return True
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.is_loopback:
            return True
        if ip.ipv4_mapped is None:
            return False
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    return any(ip in interface.network for interface in local_ipv4_interfaces())
