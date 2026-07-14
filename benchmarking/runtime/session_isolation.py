from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


class SessionIsolationError(RuntimeError):
    pass


VALID_AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)
INVALID_AGENT_ID_CHARS_RE = re.compile(r"[^a-z0-9_-]+")


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


def explicit_session_keys_for_agent(agent_id: str, session_id: str) -> list[str]:
    keys: list[str] = []
    for candidate in (agent_id.strip(), agent_id.strip().lower(), sanitize_agent_id(agent_id)):
        if not candidate:
            continue
        key = f"agent:{candidate}:explicit:{session_id}"
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


def entry_matches_requested_session_identity(
    entry: dict[str, Any],
    *,
    requested_session_id: str,
) -> bool:
    current_session_id = entry.get("sessionId")
    session_file = entry.get("sessionFile")
    session_file_matches_requested = False
    if isinstance(session_file, str) and session_file.strip():
        session_file_matches_requested = Path(session_file).name == f"{requested_session_id}.jsonl"
    return isinstance(current_session_id, str) and current_session_id == requested_session_id and session_file_matches_requested


def base_audit(agent_id: str, requested_session_id: str, store_path: Path) -> dict[str, Any]:
    return {
        "requested_session_id": requested_session_id,
        "agent_id": agent_id,
        "session_store_path": str(store_path),
        "preflight_removed_stale_main_entry": False,
        "preflight_previous_session_id": "",
        "postflight_entry_session_id": "",
        "postflight_entry_session_file": "",
        "postflight_entry_model_provider": "",
        "postflight_entry_model": "",
        "requested_model_provider": "",
        "requested_model": "",
        "postflight_model_matches_requested": False,
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
    session_store_path: Path | None = None,
) -> dict[str, Any]:
    store_path = (
        session_store_path.expanduser().resolve()
        if session_store_path is not None
        else session_store_path_for_agent(agent_id, config_path=config_path)
    )
    audit = base_audit(agent_id, requested_session_id, store_path)
    store = load_json_object(store_path, label="OpenClaw session store")
    requested_provider, requested_model = requested_model_for_agent(agent_id, config_path=config_path)
    audit["requested_model_provider"] = requested_provider or ""
    audit["requested_model"] = requested_model or ""
    keys = [
        *explicit_session_keys_for_agent(agent_id, requested_session_id),
        *main_session_keys_for_agent(agent_id),
    ]
    entries = [store[key] for key in keys if isinstance(store.get(key), dict)]
    if entries:
        entry = next(
            (
                candidate
                for candidate in entries
                if entry_matches_requested_session_identity(
                    candidate,
                    requested_session_id=requested_session_id,
                )
            ),
            entries[0],
        )
        session_id = str(entry.get("sessionId") or "")
        session_file = str(entry.get("sessionFile") or "")
        model_provider = str(entry.get("modelProvider") or "")
        model = str(entry.get("model") or "")
        audit["postflight_entry_session_id"] = session_id
        audit["postflight_entry_session_file"] = session_file
        audit["postflight_entry_model_provider"] = model_provider
        audit["postflight_entry_model"] = model
        audit["postflight_model_matches_requested"] = (
            (requested_provider is None or model_provider == requested_provider)
            and (requested_model is None or model == requested_model)
        )
        audit["session_isolation_ok"] = entry_matches_requested_session_identity(
            entry,
            requested_session_id=requested_session_id,
        )
    return audit


def merge_preflight_postflight_audit(preflight: dict[str, Any], postflight: dict[str, Any]) -> dict[str, Any]:
    audit = {**preflight, **postflight}
    audit["preflight_removed_stale_main_entry"] = bool(preflight.get("preflight_removed_stale_main_entry"))
    audit["preflight_previous_session_id"] = str(preflight.get("preflight_previous_session_id") or "")
    return audit
