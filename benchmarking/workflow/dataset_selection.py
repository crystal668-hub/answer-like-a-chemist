from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Iterable

from benchmarking.core.datasets import (
    BenchmarkRecord,
    RecordValidationError,
    classify_subset,
    dataset_name_from_file,
    load_records as load_benchmark_records,
    source_pair_key,
)
from benchmarking.core.answer_processing import normalize_space
from benchmarking.runtime import paths as runtime_paths
from benchmarking.workflow.errors import BenchmarkError
from benchmarking.workflow.run_state import slugify


SUBSET_ORDER = (
    "chembench",
    "frontierscience_Olympiad",
    "frontierscience_Research",
    "superchem_multimodal",
    "hle_chemistry",
)
SUPERCHEM_SUBSETS = ("superchem_multimodal",)


def discover_dataset_files(root: Path) -> list[Path]:
    return sorted(path.resolve() for path in root.glob("*/data/*.jsonl") if path.is_file())


def load_records(paths: Iterable[Path]) -> list[BenchmarkRecord]:
    try:
        return load_benchmark_records(paths)
    except RecordValidationError as exc:
        raise BenchmarkError(str(exc)) from exc


def sample_superchem_pairs(
    grouped: dict[str, list[BenchmarkRecord]],
    *,
    per_subset_count: int,
    seed: int,
) -> list[BenchmarkRecord]:
    if not all(grouped.get(subset) for subset in SUPERCHEM_SUBSETS):
        return []

    by_uuid: dict[str, dict[str, BenchmarkRecord]] = {}
    for subset in SUPERCHEM_SUBSETS:
        for record in grouped.get(subset, []):
            by_uuid.setdefault(source_pair_key(record), {})[subset] = record

    paired = [pair for pair in by_uuid.values() if all(subset in pair for subset in SUPERCHEM_SUBSETS)]
    if not paired:
        return []
    if len(paired) < per_subset_count:
        raise BenchmarkError(f"SUPERChem 成对题目仅有 {len(paired)} 题，无法随机抽取 {per_subset_count} 题。")

    rng = random.Random(seed)
    sampled_pairs = rng.sample(paired, per_subset_count)
    sampled: list[BenchmarkRecord] = []
    for pair in sampled_pairs:
        for subset in SUPERCHEM_SUBSETS:
            sampled.append(pair[subset])
    return sampled


def sample_records_per_subset(
    records: list[BenchmarkRecord],
    *,
    per_subset_count: int,
    seed: int,
) -> list[BenchmarkRecord]:
    if per_subset_count <= 0:
        raise BenchmarkError("--random-count-per-subset 必须是正整数")

    grouped: dict[str, list[BenchmarkRecord]] = {}
    for record in records:
        grouped.setdefault(classify_subset(record), []).append(record)

    available_supported = [subset for subset in SUBSET_ORDER if grouped.get(subset)]
    if not available_supported:
        raise BenchmarkError("当前选定的数据范围内没有可用于按子集抽样的记录。")

    rng = random.Random(seed)
    sampled: list[BenchmarkRecord] = []
    handled_subsets: set[str] = set()
    superchem_sampled = sample_superchem_pairs(grouped, per_subset_count=per_subset_count, seed=seed)
    if superchem_sampled:
        sampled.extend(superchem_sampled)
        handled_subsets.update(SUPERCHEM_SUBSETS)
    for subset in available_supported:
        if subset in handled_subsets:
            continue
        subset_records = grouped[subset]
        if len(subset_records) < per_subset_count:
            raise BenchmarkError(
                f"子集 `{subset}` 仅有 {len(subset_records)} 题，无法随机抽取 {per_subset_count} 题。"
            )
        sampled.extend(rng.sample(subset_records, per_subset_count))
    return sampled


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
    dataset_files: Iterable[Path],
    records: Iterable[BenchmarkRecord],
    single_agent_model: str,
    timestamp: str,
) -> Path:
    resolved_files = [Path(path).expanduser().resolve() for path in dataset_files]
    temporary_root = runtime_paths.temp_benchmarks_root.resolve()
    category = (
        "temporary"
        if resolved_files and all(path.is_relative_to(temporary_root) for path in resolved_files)
        else "formal"
    )
    datasets = sorted({record.dataset for record in records if record.dataset})
    benchmark = slugify(datasets[0] if len(datasets) == 1 else "mixed-datasets")
    model = slugify(str(single_agent_model).rsplit("/", 1)[-1])
    run_id = f"{benchmark}-{model}-{timestamp}"
    return Path(output_dir).expanduser().resolve() / category / benchmark / model / run_id


def select_dataset_files(args: Any) -> list[Path]:
    root = Path(args.benchmark_root).expanduser().resolve()
    if args.files:
        files = [Path(item.strip()).expanduser().resolve() for item in args.files.split(",") if item.strip()]
        missing = [str(path) for path in files if not path.is_file()]
        if missing:
            raise BenchmarkError(f"Missing benchmark files: {', '.join(missing)}")
        return files

    discovered = discover_dataset_files(root)
    if args.datasets:
        wanted = {item.strip() for item in args.datasets.split(",") if item.strip()}
        discovered = [path for path in discovered if dataset_name_from_file(path) in wanted]
    return discovered


def print_dataset_listing(paths: list[Path]) -> None:
    payload = [{"dataset": dataset_name_from_file(path), "path": str(path)} for path in paths]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def print_selected_records(records: list[BenchmarkRecord]) -> None:
    payload = [
        {
            "record_id": record.record_id,
            "subset": classify_subset(record),
            "dataset": record.dataset,
            "eval_kind": record.eval_kind,
            "source_file": record.source_file,
            "prompt_preview": normalize_space(record.prompt)[:200],
        }
        for record in records
    ]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def filter_records_by_subsets(
    records: list[BenchmarkRecord],
    raw_subsets: str | None,
) -> list[BenchmarkRecord]:
    wanted = {item.strip() for item in str(raw_subsets or "").split(",") if item.strip()}
    if not wanted:
        return list(records)

    available = {classify_subset(record) for record in records}
    unknown = sorted(wanted - available)
    if unknown:
        known = ", ".join(sorted(available)) or "<none>"
        raise BenchmarkError(f"Unknown subset(s): {', '.join(unknown)}. Available subsets: {known}")
    return [record for record in records if classify_subset(record) in wanted]


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
        raise BenchmarkError(f"Ambiguous record id(s) across selected datasets: {', '.join(ambiguous)}")

    unknown = [record_id for record_id in requested if record_id not in records_by_id]
    if unknown:
        raise BenchmarkError(f"Unknown record id(s): {', '.join(unknown)}")
    return [records_by_id[record_id] for record_id in requested]
