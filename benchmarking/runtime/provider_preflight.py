"""Check provider connectivity using the actual container network and transport."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from benchmarking.runtime.container_network import (
    ContainerNetworkConfig,
    runtime_environment,
)
from benchmarking.runtime.container_runtime import (
    ContainerRuntimeError,
    DockerContainerRuntime,
)


def check_provider_connection(
    *, runtime: DockerContainerRuntime, image: str, config_path: Path, model: str,
    network: ContainerNetworkConfig,
) -> dict:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if "/" not in model:
        models = ((payload.get("agents") or {}).get("defaults") or {}).get("models") or {}
        model = next((key for key, value in models.items() if isinstance(value, dict) and value.get("alias") == model), model)
    provider, _, model_id = model.partition("/")
    config = ((payload.get("models") or {}).get("providers") or {}).get(provider) or {}
    base_url = config.get("baseUrl")
    if not isinstance(base_url, str) or not base_url:
        return {"status": "skipped", "reason": "no_explicit_provider_endpoint", "provider": provider}
    env = runtime_environment()

    def expand(value):
        if isinstance(value, str):
            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda match: str(env.get(match[1]) or ""), value)
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        return value

    base_url = expand(base_url)
    try:
        endpoint = urlsplit(base_url)
        hostname = endpoint.hostname
        if endpoint.scheme not in {"http", "https"} or not hostname:
            raise ValueError("invalid endpoint")
    except ValueError as exc:
        raise ContainerRuntimeError("Provider endpoint is invalid or has an unset environment variable", code="provider_endpoint_invalid") from exc
    model_config = next((item for item in config.get("models", []) if item.get("id") == model_id), {})
    request = {**(config.get("request") or {}), **(model_config.get("request") or {})}
    # Connectivity probes carry transport settings, never provider authentication.
    request = expand({key: value for key, value in request.items() if key in {"proxy", "tls", "allowPrivateNetwork"}})
    return {
        **runtime.check_provider_connection(image=image, network=network, model={
            "provider": provider, "id": model_id, "api": model_config.get("api", config.get("api", "openai-responses")),
            "baseUrl": base_url,
        }, request=request),
        "provider": provider, "hostname": hostname, "network": network.to_meta(),
    }
