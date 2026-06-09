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
    if "baseUrl" not in qwen_provider:
        qwen_provider["baseUrl"] = "${QWEN_BASE_URL}"
        changed = True
    if "apiKey" not in qwen_provider:
        qwen_provider["apiKey"] = {
            "source": "env",
            "provider": "default",
            "id": "QWEN_API_KEY",
        }
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


def sync_models_payload(payload: dict[str, Any]) -> bool:
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return False
    qwen_provider = providers.get("qwen")
    if not isinstance(qwen_provider, dict):
        return False
    if qwen_provider.get("baseUrl") != TOKEN_PLAN_BASE_URL:
        return False
    return ensure_qwen_models(qwen_provider)


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


def iter_agent_model_files(agents_dir: Path) -> list[Path]:
    if not agents_dir.is_dir():
        return []
    return sorted(agents_dir.glob("*/agent/models.json"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=OPENCLAW_CONFIG_PATH)
    parser.add_argument("--agents-dir", type=Path, default=AGENTS_DIR)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    changed_paths: list[Path] = []

    if sync_file(args.config, sync_config_payload, dry_run=args.dry_run):
        changed_paths.append(args.config)

    for models_file in iter_agent_model_files(args.agents_dir):
        if sync_file(models_file, sync_models_payload, dry_run=args.dry_run):
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
