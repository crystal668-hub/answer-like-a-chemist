from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit


PROXY_KEYS = (
    "NODE_USE_ENV_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
DEFAULT_NO_PROXY_ENTRIES = ("localhost", "127.0.0.1", "::1")


def parse_scutil_proxy_output(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        values[key.strip()] = value.strip()

    proxies: dict[str, str] = {}
    if values.get("HTTPEnable") == "1" and values.get("HTTPProxy") and values.get("HTTPPort"):
        proxies["HTTP_PROXY"] = f"http://{values['HTTPProxy']}:{values['HTTPPort']}"
    if values.get("HTTPSEnable") == "1" and values.get("HTTPSProxy") and values.get("HTTPSPort"):
        proxies["HTTPS_PROXY"] = f"http://{values['HTTPSProxy']}:{values['HTTPSPort']}"
    return proxies


def macos_system_proxy_env() -> dict[str, str]:
    try:
        completed = subprocess.run(
            ["scutil", "--proxy"],
            text=True,
            capture_output=True,
            check=False,
            timeout=3,
        )
    except Exception:
        return {}
    if completed.returncode != 0:
        return {}
    return parse_scutil_proxy_output(completed.stdout)


def _has_explicit_proxy(environment: Mapping[str, str], key: str) -> bool:
    return bool(str(environment.get(key) or environment.get(key.lower()) or "").strip())


def _merge_no_proxy(existing: str) -> str:
    seen: set[str] = set()
    entries: list[str] = []
    for value in [*(str(existing or "").split(",")), *DEFAULT_NO_PROXY_ENTRIES]:
        item = value.strip()
        if item and item not in seen:
            seen.add(item)
            entries.append(item)
    return ",".join(entries)


def build_openclaw_subprocess_env(
    *,
    base_env: Mapping[str, str] | None = None,
    config_path: Path | str | None = None,
    system_proxy_text: str | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base_env is None else base_env)
    if config_path is not None:
        environment["OPENCLAW_CONFIG_PATH"] = str(Path(config_path).expanduser())

    system_proxies = parse_scutil_proxy_output(system_proxy_text) if system_proxy_text is not None else macos_system_proxy_env()
    for key in ("HTTP_PROXY", "HTTPS_PROXY"):
        if not _has_explicit_proxy(environment, key):
            value = system_proxies.get(key)
            if value:
                environment[key] = value

    has_any_proxy = any(_has_explicit_proxy(environment, key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
    if has_any_proxy and "NODE_USE_ENV_PROXY" not in environment:
        environment["NODE_USE_ENV_PROXY"] = "1"
    if has_any_proxy and "NO_PROXY" not in environment and "no_proxy" not in environment:
        environment["NO_PROXY"] = _merge_no_proxy("")
    elif has_any_proxy and "NO_PROXY" in environment:
        environment["NO_PROXY"] = _merge_no_proxy(environment["NO_PROXY"])
    return environment


def _redact_proxy_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:
        return re.sub(r"//[^/@]+@", "//***@", raw)
    if "@" not in parts.netloc:
        return raw
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))


def proxy_environment_report(env: Mapping[str, str]) -> dict[str, str]:
    report: dict[str, str] = {}
    for key in PROXY_KEYS:
        if key in env:
            value = str(env.get(key) or "")
            report[key] = _redact_proxy_value(value) if "PROXY" in key.upper() and key != "NODE_USE_ENV_PROXY" else value
    return report
