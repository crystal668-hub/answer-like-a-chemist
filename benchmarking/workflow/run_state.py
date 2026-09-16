from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from benchmarking.analysis.launcher import analysis_paths
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.core.reporting import GroupRecordResult
from benchmarking.runtime.vgb_bridge import (
    ReleaseConfig,
    VerifierGroundedRuntimeError,
    load_public_reference_answers,
    load_release_config,
)
from benchmarking.workflow.errors import BenchmarkError
from benchmarking.workflow.experiments import EXPERIMENT_GROUPS
from benchmarking.runtime.atomic_io import atomic_write_json, atomic_write_text, atomic_write_json_stream


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def slugify(value: str, *, limit: int = 64) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    cleaned = cleaned or "item"
    if len(cleaned) <= limit:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned[: limit - 9]}-{digest}".strip("-")


LEGACY_SUMMARY_CSV_FILENAMES = (
    "summary_by_group.csv",
    "summary_by_group_and_subset.csv",
)


class ResultSink:
    """Canonical per-record writer that skips byte-identical rewrites."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)
        self.per_record_root = self.output_root / "per-record"
        self.write_count = 0
        self.unchanged_count = 0

    def save_json(self, path: Path, payload: Any) -> None:
        path = Path(path)
        try:
            relative = path.relative_to(self.per_record_root)
        except ValueError as exc:
            raise ValueError(f"result path is outside the per-record root: {path}") from exc
        if ".." in relative.parts:
            raise ValueError(f"result path escapes the per-record root: {path}")
        current = path
        while True:
            if current.is_symlink():
                raise OSError(f"refusing result write through symlink: {path}")
            if current == self.output_root:
                break
            if current.parent == current:
                raise ValueError(f"result path has no output-root boundary: {path}")
            current = current.parent
        content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            self.unchanged_count += 1
            return
        atomic_write_text(path, content)
        self.write_count += 1

    def write(self, result: GroupRecordResult) -> None:
        self.save_json(
            self.per_record_root / result.group_id / f"{slugify(result.record_id)}.json",
            asdict(result),
        )

    def to_meta(self) -> dict[str, int]:
        return {
            "write_count": self.write_count,
            "unchanged_count": self.unchanged_count,
        }


def remove_legacy_summary_csvs(output_root: Path) -> None:
    for filename in LEGACY_SUMMARY_CSV_FILENAMES:
        path = output_root / filename
        if path.exists():
            path.unlink()


def count_per_record_outputs(output_root: Path, *, group_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    per_record_root = output_root / "per-record"
    for group_id in group_ids:
        group_dir = per_record_root / group_id
        counts[group_id] = len(list(group_dir.glob("*.json"))) if group_dir.is_dir() else 0
    return counts


def pending_records_for_group(
    records: list[BenchmarkRecord],
    *,
    output_root: Path,
    group_id: str,
    merge_existing_per_record: bool,
) -> list[BenchmarkRecord]:
    if not merge_existing_per_record:
        return list(records)
    group_root = output_root / "per-record" / group_id
    return [record for record in records if not (group_root / f"{slugify(record.record_id)}.json").is_file()]


def load_group_record_result(path: Path) -> GroupRecordResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "schema_version" not in payload:
        runner_meta = payload.get("runner_meta") or {}
        raw = payload.get("raw") or {}
        evaluation = payload.get("evaluation") or {}
        primary_metric = str(evaluation.get("primary_metric") or "")
        fallback_used = bool(runner_meta.get("fallback_used"))
        fallback_source = str(runner_meta.get("fallback_source") or "")
        run_status_present = isinstance(raw.get("run_status"), dict)
        scored = bool(runner_meta.get("scored", primary_metric != "execution_error"))
        explicit_evaluable = runner_meta.get("evaluable")
        explicit_reliability = str(runner_meta.get("answer_reliability") or "").strip()
        explicit_recovery_mode = str(runner_meta.get("recovery_mode") or "").strip()
        explicit_degraded = runner_meta.get("degraded_execution")
        evaluable = bool(explicit_evaluable) if explicit_evaluable is not None else scored
        if fallback_used:
            run_lifecycle_status = "completed" if scored else "failed"
            protocol_completion_status = "failed" if run_status_present else "missing"
            recovery_mode = explicit_recovery_mode or fallback_source or "none"
            if recovery_mode == "run-status-final-answer-preview":
                answer_availability = "preview_only"
                default_reliability = "low_confidence_recovered"
            else:
                answer_availability = "recovered_candidate"
                default_reliability = "high_confidence_recovered"
            answer_reliability = explicit_reliability or default_reliability
            degraded_execution = bool(explicit_degraded) if explicit_degraded is not None else True
        elif scored:
            run_lifecycle_status = "completed"
            protocol_completion_status = "completed"
            answer_availability = "native_final"
            answer_reliability = explicit_reliability or "native"
            recovery_mode = explicit_recovery_mode or "none"
            degraded_execution = bool(explicit_degraded) if explicit_degraded is not None else False
        else:
            run_lifecycle_status = "failed"
            protocol_completion_status = "failed" if run_status_present else "missing"
            answer_availability = "missing"
            answer_reliability = explicit_reliability or "none"
            evaluable = False if explicit_evaluable is None else bool(explicit_evaluable)
            recovery_mode = explicit_recovery_mode or "none"
            degraded_execution = bool(explicit_degraded) if explicit_degraded is not None else True
        payload = {
            **payload,
            # Upconvert schema-v1 per-record payloads so historical outputs remain loadable.
            "schema_version": 3,
            "run_lifecycle_status": run_lifecycle_status,
            "protocol_completion_status": protocol_completion_status,
            "protocol_acceptance_status": None,
            "answer_availability": answer_availability,
            "answer_reliability": answer_reliability,
            "evaluable": evaluable,
            "scored": scored,
            "recovery_mode": recovery_mode,
            "degraded_execution": degraded_execution,
            "execution_error_kind": None if scored else "execution_error",
        }
    if "skills_enabled" not in payload:
        group = EXPERIMENT_GROUPS.get(str(payload.get("group_id") or ""))
        payload["skills_enabled"] = bool(getattr(group, "skills_enabled", str(payload.get("group_id") or "") == "chemqa_skills_on"))
    return GroupRecordResult(**payload)



def resolve_aggregate_group_ids(
    selected_group_ids: list[str],
    *,
    output_root: Path,
    merge_existing_per_record: bool,
) -> list[str]:
    if not merge_existing_per_record:
        return list(selected_group_ids)
    present = set(selected_group_ids)
    per_record_root = output_root / "per-record"
    if per_record_root.is_dir():
        for group_dir in per_record_root.iterdir():
            if group_dir.is_dir() and not group_dir.is_symlink() and any(group_dir.glob("*.json")):
                present.add(group_dir.name)
    active = [group_id for group_id in EXPERIMENT_GROUPS if group_id in present]
    return active + sorted(present - set(active))



def iter_results_from_output_root(output_root: Path, *, group_ids: list[str]):
    """Yield canonical per-record results in deterministic group/file order."""
    for group_id in group_ids:
        group_dir = output_root / "per-record" / group_id
        if not group_dir.is_dir():
            continue
        for path in sorted(group_dir.glob("*.json")):
            yield load_group_record_result(path)


def write_results_json_stream(path: Path, metadata: dict[str, Any], result_paths: list[Path]) -> None:
    """Write a compatible top-level payload while decoding one record at a time."""
    def writer(handle):
        handle.write("{\n")
        keys = list(metadata)
        for index, key in enumerate(keys):
            if index:
                handle.write(",\n")
            handle.write(json.dumps(key, ensure_ascii=False) + ": ")
            json.dump(metadata[key], handle, ensure_ascii=False, indent=2)
        if keys:
            handle.write(",\n")
        handle.write('"results": [')
        for index, result_path in enumerate(result_paths):
            if index:
                handle.write(",")
            handle.write("\n")
            json.dump(asdict(load_group_record_result(result_path)), handle, ensure_ascii=False, indent=2)
        if result_paths:
            handle.write("\n")
        handle.write("]\n}")
    atomic_write_json_stream(path, writer)


def load_results_from_output_root(output_root: Path, *, group_ids: list[str]) -> list[GroupRecordResult]:
    return list(iter_results_from_output_root(output_root, group_ids=group_ids))


def apply_verifier_grounded_reporting_references(
    results: list[GroupRecordResult],
    *,
    release_config: ReleaseConfig | None = None,
) -> list[GroupRecordResult]:
    property_results = [
        item
        for item in results
        if str(getattr(item, "dataset", "")).startswith("verifier_grounded_property_calculation")
    ]
    if not property_results:
        return results
    try:
        config = release_config or load_release_config()
    except VerifierGroundedRuntimeError as exc:
        raise BenchmarkError(f"Unable to load public property-calculation gold: {exc}") from exc
    dataset_tracks = {
        str(track_config.get("dataset") or ""): track
        for track, track_config in config.tracks.items()
        if track.startswith("property_calculation") and isinstance(track_config, dict)
    }
    for dataset, track in dataset_tracks.items():
        track_results = [
            item for item in property_results if str(getattr(item, "dataset", "")) == dataset
        ]
        if not track_results:
            continue
        try:
            samples = load_public_reference_answers(
                track,
                release_config=config,
            )
        except VerifierGroundedRuntimeError as exc:
            raise BenchmarkError(f"Unable to load public property-calculation gold: {exc}") from exc

        references: dict[str, str] = {}
        for sample in samples:
            task_id = str(sample.get("task_id") or "").strip()
            answer = {key: value for key, value in sample.items() if key != "task_id"}
            if task_id and answer:
                references[task_id] = json.dumps(
                    answer,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        missing = sorted(
            {
                str(getattr(item, "record_id", "") or "")
                for item in track_results
                if str(getattr(item, "record_id", "") or "") not in references
            }
        )
        if missing:
            raise BenchmarkError(
                "Verifier-grounded property-calculation results are missing public gold for: "
                + ", ".join(missing)
            )
        for item in track_results:
            item.reference_answer = references[str(getattr(item, "record_id", "") or "")]

    unmatched_datasets = sorted(
        {
            str(getattr(item, "dataset", "") or "")
            for item in property_results
            if str(getattr(item, "dataset", "") or "") not in dataset_tracks
        }
    )
    if unmatched_datasets:
        raise BenchmarkError(
            "Verifier-grounded property-calculation datasets are missing pinned track metadata: "
            + ", ".join(unmatched_datasets)
        )
    return results


def verifier_grounded_reporting_reference_map(*, release_config: ReleaseConfig | None = None, validation_cache: Any | None = None) -> dict[tuple[str, str], str]:
    """Load public property gold once for streaming per-record enrichment."""
    try:
        config = release_config or load_release_config()
    except VerifierGroundedRuntimeError as exc:
        raise BenchmarkError(f"Unable to load public property-calculation gold: {exc}") from exc
    mapping: dict[tuple[str, str], str] = {}
    for track, track_config in config.tracks.items():
        if not track.startswith("property_calculation") or not isinstance(track_config, dict):
            continue
        dataset = str(track_config.get("dataset") or "")
        if not dataset:
            continue
        try:
            if validation_cache is None:
                samples = load_public_reference_answers(track, release_config=config)
            else:
                samples = load_public_reference_answers(track, release_config=config, validation_cache=validation_cache)
        except VerifierGroundedRuntimeError as exc:
            raise BenchmarkError(f"Unable to load public property-calculation gold: {exc}") from exc
        for sample in samples:
            task_id = str(sample.get("task_id") or "").strip()
            answer = {key: value for key, value in sample.items() if key != "task_id"}
            if task_id and answer:
                mapping[(dataset, task_id)] = json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
    return mapping


def apply_verifier_grounded_reporting_reference(item: GroupRecordResult, references: dict[tuple[str, str], str]) -> GroupRecordResult:
    if str(getattr(item, "dataset", "")).startswith("verifier_grounded_property_calculation"):
        key = (str(item.dataset), str(item.record_id))
        if key not in references:
            raise BenchmarkError(f"Verifier-grounded property-calculation result is missing public gold for: {item.record_id}")
        item.reference_answer = references[key]
    return item


def write_wave_status(
    output_root: Path,
    *,
    wave_index: int,
    wave_group_ids: list[str],
    status: str,
    started_at: str,
    completed_at: str | None = None,
    per_record_counts: dict[str, int] | None = None,
    inter_wave_delay_seconds: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "wave_index": wave_index,
        "groups": wave_group_ids,
        "status": status,
        "started_at": started_at,
    }
    if completed_at is not None:
        payload["completed_at"] = completed_at
    if per_record_counts is not None:
        payload["per_record_counts"] = per_record_counts
    if inter_wave_delay_seconds is not None:
        payload["inter_wave_delay_seconds"] = inter_wave_delay_seconds
    save_json(output_root / "waves" / f"wave-{wave_index:02d}.json", payload)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def automated_evaluation_launch_failed(output_root: Path, exc: Exception) -> dict[str, Any]:
    analysis_dir = output_root / "analysis"
    status_path = analysis_dir / "status.json"
    return {
        "status": "launch_failed",
        "error": f"{type(exc).__name__}: {exc}",
        "analysis_dir": str(analysis_dir),
        "status_path": str(status_path),
        "input_bundle_path": str(analysis_dir / "input-bundle.json"),
        "events_path": str(analysis_dir / "codex-events.jsonl"),
        "report_path": str(analysis_dir / "report.json"),
        "markdown_report_path": str(analysis_dir / "report.md"),
    }


def automated_evaluation_skipped(output_root: Path) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": "disabled_by_cli",
        "output_root": str(output_root),
        **analysis_paths(output_root),
    }


def describe_result_group(group_id, results, catalog):
    """Read historical group identity without registering an obsolete runtime."""
    if group_id in catalog:
        return asdict(catalog[group_id])
    for result in results:
        if result.group_id == group_id:
            return {"id": group_id, "label": result.group_label, "runner": result.runner,
                    "websearch": result.websearch, "skills_enabled": result.skills_enabled}
    raise ValueError(f"No persisted metadata for group {group_id!r}")
