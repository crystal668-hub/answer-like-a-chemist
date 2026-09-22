from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

LEGACY_VGB_TRACK_ALIASES = {
    "rdkit": "open_generation_rdkit",
    "xtb": "open_generation_xtb",
    "verifier_grounded_rdkit": "open_generation_rdkit",
    "verifier_grounded_xtb_xyz": "open_generation_xtb",
    "verifier_grounded_property_calculation": "property_calculation_advanced",
    "verifier_grounded_property_calculation_easy": "property_calculation_basic",
    "property_calculation_advanced": "property_calculation_advanced",
    "property_calculation_basic": "property_calculation_basic",
    "open_generation_rdkit": "open_generation_rdkit",
    "open_generation_xtb": "open_generation_xtb",
}
LEGACY_NON_VGB_IDENTIFIERS = {
    "chembench",
    "chembench_open_ended",
    "frontierscience",
    "frontierscience_olympiad",
    "frontierscience_research",
    "hle",
    "hle_chemistry",
    "superchem",
    "superchem_multimodal",
    "superchem_multiple_choice_rpf",
}


def _source_identity(payload: dict[str, Any]) -> str:
    source_file = str(payload.get("source_file") or "").strip()
    if not source_file:
        return ""
    path = Path(source_file)
    if path.parent.name != "data":
        return ""
    return path.parent.parent.name


@lru_cache(maxsize=1)
def _release_tracks() -> dict[str, dict[str, Any]]:
    from benchmarking.runtime.vgb_bridge import load_release_config

    return load_release_config().tracks


def resolve_result_track(
    payload: dict[str, Any],
    *,
    tracks: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Normalize current and historical result identity without rewriting evidence."""
    release_tracks = tracks if tracks is not None else _release_tracks()
    explicit = str(payload.get("track") or "").strip()
    if explicit in release_tracks:
        return explicit

    record_id = str(payload.get("record_id") or "").strip()
    for track, config in release_tracks.items():
        if record_id in (config.get("task_ids") or []):
            return track

    candidates = (
        explicit,
        str(payload.get("subset") or "").strip(),
        str(payload.get("dataset") or "").strip(),
        _source_identity(payload),
    )
    for candidate in candidates[:3]:
        mapped = LEGACY_VGB_TRACK_ALIASES.get(candidate)
        if mapped in release_tracks:
            return mapped

    historical_values = {candidate.casefold() for candidate in candidates[:3] if candidate}
    if historical_values & LEGACY_NON_VGB_IDENTIFIERS:
        legacy = next(
            candidate
            for candidate in candidates[:3]
            if candidate.casefold() in LEGACY_NON_VGB_IDENTIFIERS
        )
        return f"legacy:{legacy}"

    source_track = LEGACY_VGB_TRACK_ALIASES.get(candidates[3])
    if source_track in release_tracks:
        return source_track

    legacy = next((candidate for candidate in candidates if candidate), "")
    if not legacy:
        legacy = str(payload.get("eval_kind") or "unclassified").strip() or "unclassified"
    return legacy if legacy.startswith("legacy:") else f"legacy:{legacy}"


def canonical_track_options() -> list[str]:
    return list(_release_tracks())
