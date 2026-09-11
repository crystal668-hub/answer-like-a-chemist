from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "skills" / "chemistry-routing-matrix.json"
EXPECTED_INVENTORY_VERSION = 3
RUNTIME_OR_ORCHESTRATION_SKILLS = {"benchmark-cleanroom", "debateclaw-v1", "chemqa-review"}
DISPLAY_FIELDS = (
    "display_order",
    "display_domain_id",
    "display_domain_label",
    "display_family_id",
    "display_family_label",
)
DISPLAY_FIELDS_SET = set(DISPLAY_FIELDS)


def _require_nonempty_string(entry: dict[str, Any], key: str) -> str:
    value = str(entry.get(key) or "").strip()
    if not value:
        raise ValueError(f"chemistry routing matrix entry {entry.get('skill')!r} requires {key}")
    return value


def _validate_inventory(payload: dict[str, Any]) -> None:
    if payload.get("version") != EXPECTED_INVENTORY_VERSION:
        raise ValueError(
            f"unsupported chemistry routing matrix version: {payload.get('version')!r}; "
            f"expected {EXPECTED_INVENTORY_VERSION}"
        )
    entries = payload.get("skills")
    if not isinstance(entries, list):
        raise ValueError("chemistry routing matrix skills must be a list")

    skill_ids: set[str] = set()
    display_orders: set[int] = set()
    domain_labels: dict[str, str] = {}
    family_labels: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("chemistry routing matrix skill entries must be objects")
        skill = _require_nonempty_string(entry, "skill")
        if skill in skill_ids:
            raise ValueError(f"duplicate chemistry routing matrix skill: {skill}")
        skill_ids.add(skill)
        if entry.get("single_agent_exposure") is not True:
            continue
        if skill in RUNTIME_OR_ORCHESTRATION_SKILLS:
            raise ValueError(f"runtime/orchestration skill exposed to single agent: {skill}")
        missing = [key for key in DISPLAY_FIELDS if key not in entry]
        if missing:
            raise ValueError(f"chemistry routing matrix skill {skill!r} missing display fields: {', '.join(missing)}")
        order = entry.get("display_order")
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise ValueError(f"chemistry routing matrix skill {skill!r} has invalid display_order")
        if order in display_orders:
            raise ValueError(f"duplicate chemistry routing matrix display_order: {order}")
        display_orders.add(order)
        domain_id = _require_nonempty_string(entry, "display_domain_id")
        domain_label = _require_nonempty_string(entry, "display_domain_label")
        family_id = _require_nonempty_string(entry, "display_family_id")
        family_label = _require_nonempty_string(entry, "display_family_label")
        if domain_id in domain_labels and domain_labels[domain_id] != domain_label:
            raise ValueError(f"inconsistent label for display domain {domain_id!r}")
        if family_id in family_labels and family_labels[family_id] != family_label:
            raise ValueError(f"inconsistent label for display family {family_id!r}")
        domain_labels[domain_id] = domain_label
        family_labels[family_id] = family_label

    exposed_count = sum(1 for entry in entries if isinstance(entry, dict) and entry.get("single_agent_exposure") is True)
    if display_orders != set(range(exposed_count)):
        raise ValueError("chemistry routing matrix display_order must be contiguous from zero")


@lru_cache(maxsize=1)
def load_chemistry_skill_inventory() -> dict[str, Any]:
    payload = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("chemistry routing matrix must be an object")
    _validate_inventory(payload)
    return payload


def benchmark_skill_allowlist() -> tuple[str, ...]:
    return tuple(
        str(entry["skill"])
        for entry in load_chemistry_skill_inventory().get("skills", [])
        if entry.get("single_agent_exposure") is True
    )


def benchmark_skill_routing_inventory() -> dict[str, Any]:
    """Return the complete deterministic routing inventory for benchmark skills.

    This is metadata projection only; it intentionally performs no dependency,
    API, executable, or network health checks.
    """
    entries = []
    for entry in load_chemistry_skill_inventory().get("skills", []):
        if entry.get("single_agent_exposure") is not True:
            continue
        skill_id = str(entry.get("skill") or "").strip()
        if not skill_id:
            continue
        entries.append(
            {
                "skill_id": skill_id,
                "route_metadata": {
                    key: value
                    for key, value in entry.items()
                    if key not in {"skill", "single_agent_exposure", *DISPLAY_FIELDS_SET}
                },
                "source_path": f"skills/{skill_id}",
                "container_source_path": f"/opt/benchmark/skills/{skill_id}",
                "manifest_digest": hashlib.sha256(
                    json.dumps(
                        {key: value for key, value in entry.items() if key not in DISPLAY_FIELDS_SET},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": 1,
        "health_check_applied": False,
        "skills": entries,
        "inventory_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def load_skill_tree() -> tuple[dict[str, Any], ...]:
    domains: OrderedDict[str, dict[str, Any]] = OrderedDict()
    families: dict[tuple[str, str], dict[str, Any]] = {}
    entries = sorted(
        (
            entry
            for entry in load_chemistry_skill_inventory().get("skills", [])
            if entry.get("single_agent_exposure") is True
        ),
        key=lambda entry: entry["display_order"],
    )
    for entry in entries:
        domain_id = entry["display_domain_id"]
        family_id = entry["display_family_id"]
        if domain_id not in domains:
            domains[domain_id] = {
                "id": domain_id,
                "label": entry["display_domain_label"],
                "families": [],
            }
        family_key = (domain_id, family_id)
        if family_key not in families:
            family = {
                "id": family_id,
                "label": entry["display_family_label"],
                "skills": [],
            }
            families[family_key] = family
            domains[domain_id]["families"].append(family)
        families[family_key]["skills"].append(str(entry["skill"]))
    return tuple(
        {
            "id": domain["id"],
            "label": domain["label"],
            "families": tuple(
                {
                    "id": family["id"],
                    "label": family["label"],
                    "skills": tuple(family["skills"]),
                }
                for family in domain["families"]
            ),
        }
        for domain in domains.values()
    )


def lookup_skill_family(family_id: str) -> dict[str, Any]:
    normalized = str(family_id or "").strip().lower()
    for domain in load_skill_tree():
        for family in domain["families"]:
            if str(family["id"]).lower() == normalized:
                return family
    raise KeyError(f"unknown skill family: {family_id}")


def render_top_level_skill_tree(configured_skills: set[str] | None = None) -> str:
    inventory_by_skill = {
        str(entry["skill"]): entry
        for entry in load_chemistry_skill_inventory().get("skills", [])
        if entry.get("single_agent_exposure") is True
    }
    lines = [
        "Chemistry skill catalog:",
        "The catalog describes available capabilities; whether and how to use a skill is your choice.",
    ]
    if configured_skills is None:
        lines.append("All single-agent chemistry skills are listed below.")
    else:
        lines.append("The skills listed below come from the complete benchmark routing inventory.")
    for domain in load_skill_tree():
        rendered_families = []
        for family in domain["families"]:
            family_skills = [
                str(skill)
                for skill in family["skills"]
                if configured_skills is None or str(skill) in configured_skills
            ]
            if family_skills:
                rendered_families.append((family, family_skills))
        if not rendered_families:
            continue
        lines.append(f"- Domain `{domain['id']}`: {domain['label']}")
        for family, family_skills in rendered_families:
            lines.append(f"  - Family `{family['id']}`: {family['label']}")
            for skill in family_skills:
                summary = str(inventory_by_skill[skill].get("route_summary") or "").strip()
                lines.append(f"    - `{skill}`: {summary}")
    return "\n".join(lines)
