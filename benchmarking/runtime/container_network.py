"""One immutable network configuration for a run's probes and attempts."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values

from benchmarking.runtime import paths
from benchmarking.runtime.openclaw_env import (
    PROXY_KEYS,
    build_openclaw_subprocess_env,
    proxy_environment_report,
)


def runtime_environment(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    base = os.environ if base_env is None else base_env
    values = {
        **dotenv_values(Path(base.get("OPENCLAW_ENV_FILE", str(paths.openclaw_home / ".env")))),
        **base,
    }
    return {key: value for key, value in values.items() if isinstance(value, str)}


def _container_proxy_url(value: str, *, platform: str) -> str:
    if platform != "darwin" or not value:
        return value
    parts = urlsplit(value)
    if parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return value
    credentials = parts.netloc.rpartition("@")[0] + "@" if "@" in parts.netloc else ""
    port = f":{parts.port}" if parts.port is not None else ""
    return urlunsplit(parts._replace(netloc=f"{credentials}host.docker.internal{port}"))


@dataclass(frozen=True)
class ContainerNetworkConfig:
    proxy_environment: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    network_mode: str = "host"

    def apply(self, environment: Mapping[str, str]) -> dict[str, str]:
        return {**{k: v for k, v in environment.items() if k not in PROXY_KEYS}, **dict(self.proxy_environment)}

    def to_meta(self) -> dict:
        return {"network_mode": self.network_mode, "proxy_env": proxy_environment_report(dict(self.proxy_environment))}


def resolve_container_network(
    *, base_env: Mapping[str, str] | None = None, system_proxy_text: str | None = None,
    platform: str | None = None,
) -> ContainerNetworkConfig:
    env = build_openclaw_subprocess_env(
        base_env=runtime_environment(base_env), system_proxy_text=system_proxy_text,
    )
    effective_platform = platform or sys.platform
    proxies = {}
    for upper in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        lower = upper.lower()
        value = env.get(lower, env.get(upper))
        if value is None:
            continue
        if upper != "NO_PROXY":
            value = _container_proxy_url(value, platform=effective_platform)
        proxies[upper] = proxies[lower] = value
    if "NODE_USE_ENV_PROXY" in env:
        proxies["NODE_USE_ENV_PROXY"] = env["NODE_USE_ENV_PROXY"]
    return ContainerNetworkConfig(tuple(sorted(proxies.items())))
