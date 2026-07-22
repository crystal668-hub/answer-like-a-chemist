#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_registry(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def save_registry(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def budget_state_from_registry(registry: dict[str, Any]) -> dict[str, Any]:
    payload = registry.get("_budget_state") or {}
    if not isinstance(payload, dict):
        payload = {}
    role_counts = payload.get("respawns_by_role") or {}
    if not isinstance(role_counts, dict):
        role_counts = {}
    return {
        "phase_signature": str(payload.get("phase_signature") or ""),
        "respawns_by_role": {
            str(role): int(count or 0)
            for role, count in role_counts.items()
        },
    }


def prepare_respawn_budget_state(
    registry: dict[str, Any],
    *,
    phase_signature: str,
) -> tuple[dict[str, Any], bool]:
    budget_state = budget_state_from_registry(registry)
    if budget_state["phase_signature"] == phase_signature:
        return budget_state, False
    return {
        "phase_signature": phase_signature,
        "respawns_by_role": {},
    }, True


def slot_from_registry_entry(entry: dict[str, Any] | None) -> str:
    if not isinstance(entry, dict):
        return ""
    explicit = str(entry.get("slot") or "").strip()
    if explicit:
        return explicit
    command = list(entry.get("command") or [])
    for index, token in enumerate(command[:-1]):
        if str(token) != "--slot":
            continue
        candidate = str(command[index + 1] or "").strip()
        if candidate:
            return candidate
    return ""


def role_process_is_running(
    role: str,
    entry: dict[str, Any],
    *,
    team: str,
    driver_filename: str = "chemqa_review_openclaw_driver.py",
) -> bool:
    pid = int(entry.get("pid") or 0)
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = proc_cmdline.read_text(encoding="utf-8")
    except OSError:
        return True
    joined = raw.replace("\x00", " ")
    return driver_filename in joined and team in joined and role in joined
