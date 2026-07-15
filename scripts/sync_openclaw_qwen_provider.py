#!/usr/bin/env python3

"""Synchronize local OpenClaw Qwen provider model entries."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


OPENCLAW_HOME = Path("/Users/xutao/.openclaw")
OPENCLAW_CONFIG_PATH = OPENCLAW_HOME / "openclaw.json"
AGENTS_DIR = OPENCLAW_HOME / "agents"
TOKEN_PLAN_BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_IDS = ("qwen3.6-plus", "deepseek-v4-pro", "qwen3.7-max", "qwen3.7-plus")
QWEN_BASE_URL_REF = "${QWEN_BASE_URL}"
QWEN_API_KEY_REF = {
    "source": "env",
    "provider": "default",
    "id": "QWEN_API_KEY",
}


def qwen_model_payload(model_id: str) -> dict[str, Any]:
    return {
        "id": model_id,
        "name": model_id,
        "reasoning": True,
        "input": ["text"],
        "cost": {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
        },
        "contextWindow": 1000000,
        "maxTokens": 65536,
        "api": "openai-completions",
        "compat": {
            "thinkingFormat": "qwen",
        },
    }


def ensure_qwen_models(qwen_provider: dict[str, Any]) -> bool:
    desired_models = [qwen_model_payload(model_id) for model_id in QWEN_MODEL_IDS]
    if qwen_provider.get("models") == desired_models:
        return False
    qwen_provider["models"] = desired_models
    return True


def sync_config_payload(payload: dict[str, Any]) -> bool:
    changed = False

    qwen_provider = payload.setdefault("models", {}).setdefault("providers", {}).setdefault("qwen", {})
    if qwen_provider.get("baseUrl") != QWEN_BASE_URL_REF:
        qwen_provider["baseUrl"] = QWEN_BASE_URL_REF
        changed = True
    if qwen_provider.get("apiKey") != QWEN_API_KEY_REF:
        qwen_provider["apiKey"] = copy.deepcopy(QWEN_API_KEY_REF)
        changed = True
    if qwen_provider.get("api") != "openai-completions":
        qwen_provider["api"] = "openai-completions"
        changed = True
    changed = ensure_qwen_models(qwen_provider) or changed

    default_aliases = payload.setdefault("agents", {}).setdefault("defaults", {}).setdefault("models", {})
    for model_id in QWEN_MODEL_IDS:
        ref = f"qwen/{model_id}"
        alias_payload = {"alias": model_id}
        if default_aliases.get(ref) != alias_payload:
            default_aliases[ref] = alias_payload
            changed = True

    return changed


def sync_models_payload(payload: dict[str, Any], *, clear_all: bool = False) -> bool:
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return False
    qwen_provider = providers.get("qwen")
    if not isinstance(qwen_provider, dict):
        return False
    if not clear_all and qwen_provider.get("baseUrl") != TOKEN_PLAN_BASE_URL:
        return False
    del providers["qwen"]
    return True


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_file(path: Path, sync_payload: Any, *, dry_run: bool) -> bool:
    payload = load_json(path)
    before = copy.deepcopy(payload)
    changed = bool(sync_payload(payload))
    if changed and payload != before and not dry_run:
        dump_json(path, payload)
    return changed and payload != before


def iter_agent_model_files(agents_dir: Path, *, agent: str | None = None) -> list[Path]:
    if agent:
        return [agents_dir / agent / "agent" / "models.json"]
    if not agents_dir.is_dir():
        return []
    return sorted(agents_dir.glob("*/agent/models.json"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=OPENCLAW_CONFIG_PATH)
    parser.add_argument("--agents-dir", type=Path, default=AGENTS_DIR)
    parser.add_argument("--agent", help="Clear one agent's Qwen provider cache")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    changed_paths: list[Path] = []

    if sync_file(args.config, sync_config_payload, dry_run=args.dry_run):
        changed_paths.append(args.config)

    def sync_agent_payload(payload: dict[str, Any]) -> bool:
        return sync_models_payload(payload, clear_all=args.agent is not None)

    for models_file in iter_agent_model_files(args.agents_dir, agent=args.agent):
        if sync_file(models_file, sync_agent_payload, dry_run=args.dry_run):
            changed_paths.append(models_file)

    action = "Would update" if args.dry_run else "Updated"
    if changed_paths:
        for path in changed_paths:
            print(f"{action}: {path}")
    else:
        print("OpenClaw Qwen provider config already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
