#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


IMAGE_PATH_FIELDS = ("question_image_paths", "explanation_image_paths")
OPTION_IMAGE_PATH_FIELD = "option_image_paths"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rewrite stale absolute SUPERChem temp benchmark image paths.")
    parser.add_argument(
        "--jsonl",
        type=Path,
        default=Path("/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl"),
        help="SUPERChem temp benchmark JSONL to rewrite in place.",
    )
    parser.add_argument(
        "--old-prefix",
        default="/home/dministrator/.openclaw/benchmarks/superchem/assets",
        help="Stale absolute assets prefix to replace.",
    )
    parser.add_argument(
        "--new-prefix",
        default="../../../benchmarks/superchem/assets",
        help="Relative assets prefix to write into the JSONL.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing the JSONL.")
    return parser.parse_args()


def rewrite_path(value: Any, *, old_prefix: str, new_prefix: str) -> tuple[Any, int]:
    if not isinstance(value, str):
        return value, 0
    if value == old_prefix:
        return new_prefix, 1
    prefix = old_prefix.rstrip("/") + "/"
    if value.startswith(prefix):
        return new_prefix.rstrip("/") + "/" + value[len(prefix) :], 1
    return value, 0


def rewrite_record(record: dict[str, Any], *, old_prefix: str, new_prefix: str) -> tuple[dict[str, Any], int]:
    updated = dict(record)
    changes = 0
    for field in IMAGE_PATH_FIELDS:
        values = updated.get(field)
        if not isinstance(values, list):
            continue
        rewritten = []
        for item in values:
            new_item, changed = rewrite_path(item, old_prefix=old_prefix, new_prefix=new_prefix)
            rewritten.append(new_item)
            changes += changed
        updated[field] = rewritten

    option_paths = updated.get(OPTION_IMAGE_PATH_FIELD)
    if isinstance(option_paths, dict):
        rewritten_options: dict[str, Any] = {}
        for option, values in option_paths.items():
            if not isinstance(values, list):
                rewritten_options[str(option)] = values
                continue
            rewritten_values = []
            for item in values:
                new_item, changed = rewrite_path(item, old_prefix=old_prefix, new_prefix=new_prefix)
                rewritten_values.append(new_item)
                changes += changed
            rewritten_options[str(option)] = rewritten_values
        updated[OPTION_IMAGE_PATH_FIELD] = rewritten_options
    return updated, changes


def repair_jsonl(path: Path, *, old_prefix: str, new_prefix: str, dry_run: bool = False) -> dict[str, int | str | bool]:
    records: list[dict[str, Any]] = []
    records_seen = 0
    records_changed = 0
    rewritten_paths = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records_seen += 1
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"Expected JSON object at record {records_seen}")
        updated, changes = rewrite_record(record, old_prefix=old_prefix, new_prefix=new_prefix)
        if changes:
            records_changed += 1
            rewritten_paths += changes
        records.append(updated)

    if not dry_run:
        path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
    return {
        "jsonl": str(path),
        "dry_run": dry_run,
        "records_seen": records_seen,
        "records_changed": records_changed,
        "rewritten_paths": rewritten_paths,
    }


def main() -> int:
    args = parse_args()
    summary = repair_jsonl(
        args.jsonl.expanduser().resolve(),
        old_prefix=str(args.old_prefix).rstrip("/"),
        new_prefix=str(args.new_prefix).rstrip("/"),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
