from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "skills" / "chemistry-routing-matrix.json"


@lru_cache(maxsize=1)
def load_chemistry_routing_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def route_skill_for_text(text: str) -> str | None:
    normalized = _normalize(text)
    for entry in load_chemistry_routing_matrix().get("skills", []):
        for trigger in entry.get("primary_triggers") or []:
            if _trigger_matches(normalized, str(trigger)):
                return str(entry["skill"])
    return None


def requirements_for_text(text: str) -> list[dict[str, str]]:
    normalized = _normalize(text)
    requirements: list[dict[str, str]] = []
    for entry in load_chemistry_routing_matrix().get("skills", []):
        matched = _matched_trigger(normalized, entry.get("primary_triggers") or [])
        if not matched:
            continue
        requirements.append(
            {
                "skill": str(entry["skill"]),
                "trigger": matched,
                "reason": str(entry.get("route_summary") or ""),
            }
        )
    return requirements


def render_compact_skill_routing_table() -> str:
    matrix = load_chemistry_routing_matrix()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in matrix.get("skills", []):
        grouped.setdefault(str(entry.get("tier") or "extended"), []).append(entry)

    lines = ["Experimental chemistry skill routing rules:"]
    for tier in (
        "existing",
        "core",
        "software",
        "database",
        "format",
        "materials-specialist",
        "ml-potential",
        "generative-materials",
        "molecular-ml",
        "materials-ml",
        "workflow",
        "extended",
    ):
        entries = grouped.get(tier)
        if not entries:
            continue
        rendered = "; ".join(f"`{entry['skill']}`: {entry['route_summary']}" for entry in entries)
        lines.append(f"- {tier}: {rendered}")
    lines.append(
        "Use the first matching primary route as the main skill. Read the full SKILL.md only after selecting a route. "
        "If a triggered route is skipped, record status: skipped with trigger, reason, and risk."
    )
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("_", "-")).strip()


def _trigger_matches(normalized_text: str, trigger: str) -> bool:
    normalized_trigger = _normalize(trigger)
    if not normalized_trigger:
        return False
    if re.search(r"[a-z0-9]", normalized_trigger):
        return re.search(rf"(?<![a-z0-9]){re.escape(normalized_trigger)}(?![a-z0-9])", normalized_text) is not None
    return normalized_trigger in normalized_text


def _matched_trigger(normalized_text: str, triggers: list[Any]) -> str:
    for trigger in triggers:
        trigger_text = str(trigger)
        if _trigger_matches(normalized_text, trigger_text):
            return trigger_text
    return ""
