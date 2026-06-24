from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .state import AppState

_TOR_PROXY_CANDIDATES = [
    "socks5h://127.0.0.1:39050",
    "socks5h://127.0.0.1:9050",
    "socks5h://127.0.0.1:9150",
]


@dataclass(slots=True, frozen=True)
class NetworkConfig:
    proxy_mode: str
    proxy_url: str | None
    proxy_policy: str
    geo_lookup_provider: str

    @classmethod
    def from_state(cls, state: AppState) -> "NetworkConfig":
        return cls(
            proxy_mode=state.network_proxy_mode,
            proxy_url=state.network_proxy_url,
            proxy_policy=state.network_proxy_policy,
            geo_lookup_provider=state.geo_lookup_provider,
        )

    def requires_proxy(self) -> bool:
        return self.proxy_policy == "require"

    def websocket_proxy(self) -> str | None:
        if self.proxy_mode == "custom":
            return self.proxy_url
        if self.proxy_mode == "disabled":
            return None
        return None

    def websocket_proxy_argument(self) -> dict[str, object]:
        if self.proxy_mode == "disabled":
            return {"proxy": None}
        if self.proxy_mode == "custom" and self.proxy_url:
            return {"proxy": self.proxy_url}
        return {}

    def redacted_proxy_target(self) -> str:
        if self.proxy_mode == "disabled":
            return "disabled"
        if self.proxy_mode == "system":
            return "system"
        if not self.proxy_url:
            return "custom"
        parsed = urlparse(self.proxy_url)
        if not parsed.hostname:
            return "custom"
        host = parsed.hostname
        port = parsed.port
        suffix = f":{port}" if port is not None else ""
        return f"{parsed.scheme}://{host}{suffix}"

    def make_http_client(self, *, user_agent: str, timeout: float = 10.0) -> httpx.Client:
        if self.proxy_mode == "disabled" and self.requires_proxy():
            raise RuntimeError("network proxy policy is require, but proxy mode is disabled")
        headers = {"User-Agent": user_agent}
        if self.proxy_mode == "custom" and self.proxy_url:
            return httpx.Client(proxy=self.proxy_url, trust_env=False, timeout=timeout, headers=headers)
        if self.proxy_mode == "system":
            return httpx.Client(trust_env=True, timeout=timeout, headers=headers)
        return httpx.Client(trust_env=False, timeout=timeout, headers=headers)


def coerce_proxy_mode(raw: str) -> str:
    value = raw.strip().lower()
    if value not in {"disabled", "system", "custom"}:
        raise ValueError("proxy mode must be one of: disabled, system, custom")
    return value


def coerce_proxy_policy(raw: str) -> str:
    value = raw.strip().lower()
    if value not in {"prefer", "require"}:
        raise ValueError("proxy policy must be one of: prefer, require")
    return value


def resolve_proxy_target(raw: str) -> tuple[str, str | None]:
    value = raw.strip()
    lowered = value.lower()
    if lowered == "disabled":
        return "disabled", None
    if lowered == "system":
        return "system", None
    if lowered == "tor":
        return "custom", first_live_tor_proxy() or _TOR_PROXY_CANDIDATES[1]
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("proxy must be one of: tor, system, disabled, or a proxy URL")
    return "custom", value


def first_live_tor_proxy(timeout: float = 1.0) -> str | None:
    for candidate in _TOR_PROXY_CANDIDATES:
        if test_proxy_url(candidate, timeout=timeout):
            return candidate
    return None


def test_proxy_url(proxy_url: str, timeout: float = 2.0) -> bool:
    parsed = urlparse(proxy_url)
    if not parsed.hostname or parsed.port is None:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout):
            return True
    except OSError:
        return False


def proxy_status_payload(config: NetworkConfig) -> dict[str, Any]:
    if config.proxy_mode == "custom" and config.proxy_url:
        healthy = test_proxy_url(config.proxy_url)
    else:
        healthy = None
    return {
        "mode": config.proxy_mode,
        "policy": config.proxy_policy,
        "target": config.redacted_proxy_target(),
        "healthy": healthy,
        "lookup_provider": config.geo_lookup_provider,
    }
