from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from benchmarking.core.answer_processing import normalize_space
from benchmarking.core.records import (
    BenchmarkRecord,
    RecordValidationError,
    track_name_from_file,
)
from benchmarking.core.records import (
    load_records as load_benchmark_records,
)
from benchmarking.runtime import paths as runtime_paths
from benchmarking.workflow.errors import BenchmarkError
from benchmarking.workflow.run_state import slugify

CANONICAL_BENCHMARK_NAMES = {
    "rdkit": "vgb-rdkit",
    "xtb": "vgb-xtb",
    "property_calculation_advanced": "vgb-property-calculation-advanced",
    "property_calculation_basic": "vgb-property-calculation-basic",
}


def canonical_tracks() -> tuple[str, ...]:
    from benchmarking.runtime.vgb_bridge import load_release_config

    return tuple(load_release_config().tracks)


def discover_track_files(root: Path) -> list[Path]:
    tracks = canonical_tracks()
    return [
        path
        for track in tracks
        if (path := (root / track / "data" / f"{track}.jsonl").resolve()).is_file()
    ]


def load_records(paths: Iterable[Path]) -> list[BenchmarkRecord]:
    try:
        return load_benchmark_records(paths)
    except (OSError, json.JSONDecodeError, RecordValidationError) as exc:
        raise BenchmarkError(str(exc)) from exc


def apply_offset_limit(
    records: list[BenchmarkRecord],
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[BenchmarkRecord]:
    if offset < 0:
        raise BenchmarkError("--offset 不能为负数")
    sliced = records[offset:]
    if limit is not None:
        if limit < 0:
            raise BenchmarkError("--limit 不能为负数")
        sliced = sliced[:limit]
    return sliced


def default_run_output_root(
    *,
    output_dir: str | Path,
    track_files: Iterable[Path],
    records: Iterable[BenchmarkRecord],
    single_agent_model: str,
    timestamp: str,
) -> Path:
    resolved_files = [Path(path).expanduser().resolve() for path in track_files]
    temporary_root = runtime_paths.temp_benchmarks_root.resolve()
    category = (
        "temporary"
        if resolved_files and all(path.is_relative_to(temporary_root) for path in resolved_files)
        else "formal"
    )
    tracks = sorted({record.track for record in records if record.track})
    benchmark = (
        CANONICAL_BENCHMARK_NAMES.get(tracks[0], slugify(tracks[0]))
        if len(tracks) == 1
        else "mixed-tracks"
    )
    model = slugify(str(single_agent_model).rsplit("/", 1)[-1])
    run_id = f"{benchmark}-{model}-{timestamp}"
    return Path(output_dir).expanduser().resolve() / category / benchmark / model / run_id


def _validate_track_file_layout(path: Path) -> None:
    track = track_name_from_file(path)
    if path.parent.name != "data" or not track or path.name != f"{track}.jsonl":
        raise BenchmarkError(
            f"Benchmark file must use <track>/data/<track>.jsonl layout: {path}"
        )
    if track not in set(canonical_tracks()):
        raise BenchmarkError(f"Unsupported VGB track file: {track}")


def select_track_files(args: Any) -> list[Path]:
    allowed = set(canonical_tracks())
    requested = {item.strip() for item in str(args.tracks or "").split(",") if item.strip()}
    unknown = requested - allowed
    if unknown:
        raise BenchmarkError(f"Unsupported VGB track(s): {', '.join(sorted(unknown))}")

    if args.files:
        files = [
            Path(item.strip()).expanduser().resolve()
            for item in args.files.split(",")
            if item.strip()
        ]
        missing = [str(path) for path in files if not path.is_file()]
        if missing:
            raise BenchmarkError(f"Missing benchmark files: {', '.join(missing)}")
        for path in files:
            _validate_track_file_layout(path)
        load_vgb_records(files)
        return files

    root = Path(args.benchmark_root).expanduser().resolve()
    discovered = discover_track_files(root)
    found = {track_name_from_file(path): path for path in discovered}
    required = requested or allowed
    missing = required - set(found)
    if missing:
        raise BenchmarkError(f"Missing VGB track file(s): {', '.join(sorted(missing))}")
    return [found[track] for track in canonical_tracks() if track in required]


def load_vgb_records(paths: Iterable[Path]) -> list[BenchmarkRecord]:
    from benchmarking.runtime.vgb_bridge import load_release_config
    from benchmarking.scoring.errors import EvaluationError
    from benchmarking.scoring.evaluators.verifier_grounded import (
        validate_verifier_grounded_release,
    )

    release = load_release_config()
    records = load_records(paths)
    for record in records:
        try:
            validate_verifier_grounded_release(record, release_config=release)
        except EvaluationError as exc:
            raise BenchmarkError(f"Invalid VGB record {record.record_id!r}: {exc}") from exc
    return records


def print_track_listing(paths: list[Path]) -> None:
    payload = [{"track": track_name_from_file(path), "path": str(path)} for path in paths]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def print_selected_records(records: list[BenchmarkRecord]) -> None:
    payload = [
        {
            "record_id": record.record_id,
            "track": record.track,
            "eval_kind": record.eval_kind,
            "source_file": record.source_file,
            "prompt_preview": normalize_space(record.prompt)[:200],
        }
        for record in records
    ]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def filter_records_by_ids(
    records: list[BenchmarkRecord],
    raw_record_ids: str | None,
) -> list[BenchmarkRecord]:
    requested = [item.strip() for item in str(raw_record_ids or "").split(",") if item.strip()]
    if not requested:
        return list(records)
    if len(requested) != len(set(requested)):
        raise BenchmarkError("--record-ids must not contain duplicate ids")

    records_by_id: dict[str, BenchmarkRecord] = {}
    duplicate_available_ids: set[str] = set()
    for record in records:
        if record.record_id in records_by_id:
            duplicate_available_ids.add(record.record_id)
        records_by_id[record.record_id] = record
    ambiguous = sorted(set(requested) & duplicate_available_ids)
    if ambiguous:
        raise BenchmarkError(f"Ambiguous record id(s) across selected tracks: {', '.join(ambiguous)}")

    unknown = [record_id for record_id in requested if record_id not in records_by_id]
    if unknown:
        raise BenchmarkError(f"Unknown record id(s): {', '.join(unknown)}")
    return [records_by_id[record_id] for record_id in requested]
