#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarking.evaluators import safe_json_extract


class SessionIsolationError(RuntimeError):
    pass


VALID_AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)
INVALID_AGENT_ID_CHARS_RE = re.compile(r"[^a-z0-9_-]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run single-LLM OpenClaw turns with benchmark session isolation.")
    parser.add_argument("--agent", required=True, help="OpenClaw agent id.")
    parser.add_argument("--config-file", required=True, help="OpenClaw config path for this benchmark run.")
    parser.add_argument("--session-id", required=True, help="Run-scoped OpenClaw session id.")
    parser.add_argument("--message", required=True, help="Prompt to send to OpenClaw.")
    parser.add_argument("--thinking", help="Forward OpenClaw thinking override.")
    parser.add_argument("--timeout", type=int, help="Forward OpenClaw timeout override in seconds.")
    parser.add_argument("--json", action="store_true", help="Forward OpenClaw JSON output and attach isolation audit.")
    return parser.parse_args()


def sanitize_agent_id(value: str) -> str:
    trimmed = str(value or "").strip()
    if not trimmed:
        return "main"
    lowered = trimmed.lower()
    if VALID_AGENT_ID_RE.match(trimmed):
        return lowered
    normalized = INVALID_AGENT_ID_CHARS_RE.sub("-", lowered).strip("-")
    return normalized[:64] or "main"


def main_session_keys_for_agent(agent_id: str) -> list[str]:
    keys: list[str] = []
    for candidate in (agent_id.strip(), agent_id.strip().lower(), sanitize_agent_id(agent_id)):
        if not candidate:
            continue
        key = f"agent:{candidate}:main"
        if key not in keys:
            keys.append(key)
    return keys


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SessionIsolationError(f"{label} is not valid JSON: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise SessionIsolationError(f"{label} must contain a JSON object: {path}")
    return payload


def load_openclaw_config(config_path: Path) -> dict[str, Any]:
    payload = load_json_object(config_path, label="OpenClaw config")
    if not payload:
        raise SessionIsolationError(f"OpenClaw config does not exist or is empty: {config_path}")
    return payload


def resolve_agent_entry(agent_id: str, *, config_path: Path) -> dict[str, Any]:
    payload = load_openclaw_config(config_path)
    agents = payload.get("agents", {})
    entries = agents.get("list", []) if isinstance(agents, dict) else []
    normalized = agent_id.strip().lower()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "").strip()
        if entry_id == agent_id or entry_id.lower() == normalized:
            return entry
    raise SessionIsolationError(f"Could not find OpenClaw agent `{agent_id}` in config: {config_path}")


def session_store_path_for_agent(agent_id: str, *, config_path: Path) -> Path:
    entry = resolve_agent_entry(agent_id, config_path=config_path)
    agent_dir = str(entry.get("agentDir") or "").strip()
    if agent_dir:
        return Path(agent_dir).expanduser().resolve().parent / "sessions" / "sessions.json"
    return Path.home() / ".openclaw" / "agents" / sanitize_agent_id(agent_id) / "sessions" / "sessions.json"


def requested_model_for_agent(agent_id: str, *, config_path: Path) -> tuple[str | None, str | None]:
    entry = resolve_agent_entry(agent_id, config_path=config_path)
    provider = str(entry.get("provider") or "").strip()
    model = str(entry.get("model") or "").strip()
    if not provider and "/" in model:
        provider, model = model.split("/", 1)
    elif provider and model.startswith(f"{provider}/"):
        model = model[len(provider) + 1 :]
    return (provider or None, model or None)


def entry_matches_requested_session(
    entry: dict[str, Any],
    *,
    requested_session_id: str,
    requested_provider: str | None,
    requested_model: str | None,
) -> bool:
    current_session_id = entry.get("sessionId")
    session_file = entry.get("sessionFile")
    session_file_matches_requested = False
    if isinstance(session_file, str) and session_file.strip():
        session_file_matches_requested = Path(session_file).name == f"{requested_session_id}.jsonl"
    current_provider = entry.get("modelProvider")
    current_model = entry.get("model")
    provider_matches_requested = requested_provider is None or current_provider == requested_provider
    model_matches_requested = requested_model is None or current_model == requested_model
    return (
        isinstance(current_session_id, str)
        and current_session_id == requested_session_id
        and session_file_matches_requested
        and provider_matches_requested
        and model_matches_requested
    )


def base_audit(agent_id: str, requested_session_id: str, store_path: Path) -> dict[str, Any]:
    return {
        "requested_session_id": requested_session_id,
        "agent_id": agent_id,
        "session_store_path": str(store_path),
        "preflight_removed_stale_main_entry": False,
        "preflight_previous_session_id": "",
        "postflight_entry_session_id": "",
        "postflight_entry_session_file": "",
        "session_isolation_ok": False,
    }


def reset_agent_main_session_if_stale(
    agent_id: str,
    requested_session_id: str,
    *,
    config_path: Path,
) -> dict[str, Any]:
    store_path = session_store_path_for_agent(agent_id, config_path=config_path)
    audit = base_audit(agent_id, requested_session_id, store_path)
    store = load_json_object(store_path, label="OpenClaw session store")
    if not store:
        return audit

    keys = main_session_keys_for_agent(agent_id)
    current_entries = [(key, store.get(key)) for key in keys if isinstance(store.get(key), dict)]
    if not current_entries:
        return audit

    requested_provider, requested_model = requested_model_for_agent(agent_id, config_path=config_path)
    stale_entries = [
        (key, entry)
        for key, entry in current_entries
        if isinstance(entry, dict)
        and not entry_matches_requested_session(
            entry,
            requested_session_id=requested_session_id,
            requested_provider=requested_provider,
            requested_model=requested_model,
        )
    ]
    if not stale_entries:
        return audit

    first_stale = stale_entries[0][1]
    audit["preflight_removed_stale_main_entry"] = True
    audit["preflight_previous_session_id"] = str(first_stale.get("sessionId") or "")
    updated = dict(store)
    for key in keys:
        updated.pop(key, None)
    atomic_write_json(store_path, updated)
    return audit


def inspect_postflight_session(
    agent_id: str,
    requested_session_id: str,
    *,
    config_path: Path,
) -> dict[str, Any]:
    store_path = session_store_path_for_agent(agent_id, config_path=config_path)
    audit = base_audit(agent_id, requested_session_id, store_path)
    store = load_json_object(store_path, label="OpenClaw session store")
    for key in main_session_keys_for_agent(agent_id):
        entry = store.get(key)
        if not isinstance(entry, dict):
            continue
        session_id = str(entry.get("sessionId") or "")
        session_file = str(entry.get("sessionFile") or "")
        audit["postflight_entry_session_id"] = session_id
        audit["postflight_entry_session_file"] = session_file
        audit["session_isolation_ok"] = session_id == requested_session_id and Path(session_file).name == f"{requested_session_id}.jsonl"
        return audit
    return audit


def merge_isolation_audit(payload: Any, audit: dict[str, Any]) -> Any:
    if not isinstance(payload, dict):
        return payload
    target = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    if isinstance(target, dict):
        meta = target.setdefault("meta", {})
        if isinstance(meta, dict):
            existing = meta.get("session_isolation")
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(audit)
            meta["session_isolation"] = merged
    return payload


def parse_openclaw_json_output(output: str) -> Any:
    stripped = str(output or "").strip()
    if not stripped:
        raise SessionIsolationError("OpenClaw returned empty JSON output.")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return safe_json_extract(stripped)


def resolve_openclaw_executable() -> str:
    executable = shutil.which("openclaw")
    if executable:
        return executable
    raise SessionIsolationError("Missing openclaw executable in PATH.")


def run_openclaw(args: argparse.Namespace, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    command = [
        resolve_openclaw_executable(),
        "agent",
        "--local",
        "--agent",
        args.agent,
        "--session-id",
        args.session_id,
        "--message",
        args.message,
    ]
    if args.thinking:
        command.extend(["--thinking", args.thinking])
    if args.timeout is not None:
        command.extend(["--timeout", str(max(1, int(args.timeout)))])
    if args.json:
        command.append("--json")
    return subprocess.run(command, env=env, capture_output=True, text=True, check=False)


def main() -> int:
    args = parse_args()
    config_path = Path(args.config_file).expanduser().resolve()
    env = os.environ.copy()
    env["OPENCLAW_CONFIG_PATH"] = str(config_path)
    try:
        preflight_audit = reset_agent_main_session_if_stale(args.agent, args.session_id, config_path=config_path)
        result = run_openclaw(args, env=env)
        if result.returncode != 0:
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode
        postflight_audit = inspect_postflight_session(args.agent, args.session_id, config_path=config_path)
        audit = {**preflight_audit, **postflight_audit}
        audit["preflight_removed_stale_main_entry"] = preflight_audit["preflight_removed_stale_main_entry"]
        audit["preflight_previous_session_id"] = preflight_audit["preflight_previous_session_id"]
        if args.json:
            output = result.stdout.strip() or result.stderr.strip()
            payload = parse_openclaw_json_output(output)
            payload = merge_isolation_audit(payload, audit)
            print(json.dumps(payload, ensure_ascii=False))
        else:
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
        return 0
    except Exception as exc:
        if args.json:
            payload = {
                "result": {
                    "payloads": [],
                    "meta": {
                        "session_isolation": {
                            "requested_session_id": args.session_id,
                            "agent_id": args.agent,
                            "session_store_path": "",
                            "preflight_removed_stale_main_entry": False,
                            "preflight_previous_session_id": "",
                            "postflight_entry_session_id": "",
                            "postflight_entry_session_file": "",
                            "session_isolation_ok": False,
                            "error": str(exc),
                        }
                    },
                }
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
