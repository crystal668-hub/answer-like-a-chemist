"""Resolve explicitly configured model endpoints before Docker scheduling."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from benchmarking.runtime import paths
from benchmarking.runtime.container_runtime import (
    ContainerRuntimeError,
    DockerContainerRuntime,
)
from benchmarking.runtime.openclaw_env import build_openclaw_subprocess_env


def check_provider_dns(*, runtime: DockerContainerRuntime, image: str, config_path: Path, model: str) -> dict:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if "/" not in model:
        models = ((payload.get("agents") or {}).get("defaults") or {}).get("models") or {}
        model = next((key for key, value in models.items() if isinstance(value, dict) and value.get("alias") == model), model)
    provider = model.partition("/")[0]
    config = ((payload.get("models") or {}).get("providers") or {}).get(provider) or {}
    base_url = config.get("baseUrl")
    if not isinstance(base_url, str) or not base_url:
        return {"status": "skipped", "reason": "no_explicit_provider_endpoint", "provider": provider}
    env = {
        **dotenv_values(Path(os.environ.get("OPENCLAW_ENV_FILE", str(paths.openclaw_home / ".env")))),
        **build_openclaw_subprocess_env(),
    }
    base_url = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda match: str(env.get(match[1]) or ""), base_url)
    try:
        endpoint = urlsplit(base_url)
        hostname = endpoint.hostname
        if endpoint.scheme not in {"http", "https"} or not hostname:
            raise ValueError("invalid endpoint")
    except ValueError as exc:
        raise ContainerRuntimeError("Provider endpoint is invalid or has an unset environment variable", code="provider_endpoint_invalid") from exc
    if env.get("NODE_USE_ENV_PROXY") == "1" and any(env.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")):
        # A proxy may resolve the target remotely; local failure is not authoritative.
        return {"status": "skipped", "reason": "proxy_resolution", "provider": provider, "hostname": hostname}
    return {**runtime.check_dns(image=image, hostname=hostname), "provider": provider}
