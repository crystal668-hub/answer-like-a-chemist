#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_paths


REFERENCE_PLACEHOLDER = "Verifier-grounded task; score is computed by local verifier scripts."


def load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def verifier_specs_for_task(task: dict[str, Any], specs_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for constraint in task.get("constraints") or []:
        if not isinstance(constraint, dict):
            continue
        verifier_id = str(constraint.get("verifier_id") or "").strip()
        if verifier_id and verifier_id in specs_by_id:
            selected.append(specs_by_id[verifier_id])
    return selected


def verifier_timeout_seconds(verifier_specs: list[dict[str, Any]], *, buffer_seconds: float = 60.0) -> float:
    if not verifier_specs:
        return 120.0
    total = 0.0
    for spec in verifier_specs:
        total += float(spec.get("timeout_seconds", 60.0))
    return total + buffer_seconds


def build_record(
    *,
    task: dict[str, Any],
    verifier_specs: list[dict[str, Any]],
    source_root: Path,
    task_set: str,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("verifier-grounded task is missing task_id")
    prompt = str(task.get("prompt") or "").strip()
    if not prompt:
        raise ValueError(f"verifier-grounded task `{task_id}` is missing prompt")
    return {
        "id": task_id,
        "prompt": prompt,
        "answer": REFERENCE_PLACEHOLDER,
        "eval_kind": "verifier_grounded",
        "verifier_grounded": {
            "source_repo": str(source_root),
            "task_set": task_set,
            "task": task,
            "verifier_specs": verifier_specs,
            "timeout_seconds": verifier_timeout_seconds(verifier_specs),
        },
    }


def export_verifier_grounded_dataset(
    *,
    source_root: Path,
    task_set: str,
    dataset_name: str,
    output_path: Path,
) -> Path:
    source_root = source_root.expanduser().resolve()
    task_set = task_set.strip()
    dataset_name = dataset_name.strip()
    if not task_set:
        raise ValueError("task_set must be non-empty")
    if not dataset_name:
        raise ValueError("dataset_name must be non-empty")

    task_dir = source_root / "tasks" / task_set
    tasks_payload = load_yaml(task_dir / "tasks.yaml")
    specs_payload = load_yaml(task_dir / "verifier_specs.yaml")
    specs_by_id = {
        str(spec.get("verifier_id")): spec
        for spec in specs_payload.get("verifiers") or []
        if isinstance(spec, dict) and spec.get("verifier_id")
    }
    records = [
        build_record(
            task=task,
            verifier_specs=verifier_specs_for_task(task, specs_by_id),
            source_root=source_root,
            task_set=task_set,
        )
        for task in tasks_payload.get("tasks") or []
        if isinstance(task, dict)
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return output_path


def export_rdkit_dataset(*, source_root: Path, output_path: Path) -> Path:
    return export_verifier_grounded_dataset(
        source_root=source_root,
        task_set="rdkit_baseline",
        dataset_name="verifier_grounded_rdkit",
        output_path=output_path,
    )


def export_xtb_xyz_dataset(*, source_root: Path, output_path: Path) -> Path:
    return export_verifier_grounded_dataset(
        source_root=source_root,
        task_set="xtb_xyz",
        dataset_name="verifier_grounded_xtb_xyz",
        output_path=output_path,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export verifier-grounded tasks as an OpenClaw benchmark JSONL.")
    parser.add_argument(
        "--source-root",
        default="/Users/xutao/verifier-grounded-benchmark",
        type=Path,
        help="Path to the verifier-grounded-benchmark repository.",
    )
    parser.add_argument(
        "--task-set",
        default="rdkit_baseline",
        help="Verifier-grounded task set directory under source-root/tasks.",
    )
    parser.add_argument(
        "--dataset-name",
        default="verifier_grounded_rdkit",
        help="OpenClaw formal benchmark dataset directory name.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSONL path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or (
        runtime_paths.benchmarks_root
        / args.dataset_name
        / "data"
        / f"{args.dataset_name}.jsonl"
    )
    path = export_verifier_grounded_dataset(
        source_root=args.source_root,
        task_set=args.task_set,
        dataset_name=args.dataset_name,
        output_path=output,
    )
    print(path)


if __name__ == "__main__":
    main()
