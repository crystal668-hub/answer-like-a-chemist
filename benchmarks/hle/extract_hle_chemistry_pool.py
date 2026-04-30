#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SOURCE_DATASET = "cais/hle"
FIELD_CONTRACT_VERSION = "hle-chemistry-pool-v1"
CHEMISTRY_PATTERN = re.compile(r"\b(?:chemistry|chemical|biochemistry|organic|inorganic|analytical)\b", re.IGNORECASE)


class ExtractionError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract HLE chemistry records into benchmark-ready JSONL.")
    parser.add_argument("--dataset", default=SOURCE_DATASET, help="Hugging Face dataset name")
    parser.add_argument("--split", default="test", help="Hugging Face split")
    parser.add_argument("--input-jsonl", help="Optional local JSONL export of HLE rows")
    parser.add_argument("--output-jsonl", required=True, help="Path to the extracted HLE chemistry JSONL")
    parser.add_argument(
        "--manifest-out",
        help="Optional manifest path. Defaults to <output-jsonl> with suffix .manifest.json",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_chemistry_record(row: dict[str, Any]) -> bool:
    haystack = " ".join([normalize_text(row.get("category")), normalize_text(row.get("raw_subject"))])
    return bool(CHEMISTRY_PATTERN.search(haystack))


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExtractionError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ExtractionError(f"Expected object at {path}:{line_number}, got {type(payload).__name__}")
            yield payload


def iter_hf_rows(*, dataset: str, split: str) -> Iterable[dict[str, Any]]:
    try:
        datasets_module = importlib.import_module("datasets")
    except ImportError as exc:
        raise ExtractionError(
            "Loading cais/hle from Hugging Face requires the `datasets` package or --input-jsonl."
        ) from exc
    load_dataset = getattr(datasets_module, "load_dataset")
    loaded = load_dataset(dataset, split=split)
    for row in loaded:
        if isinstance(row, dict):
            yield row


def transform_row(row: dict[str, Any], *, row_idx: int) -> tuple[dict[str, Any] | None, dict[str, int]]:
    if not is_chemistry_record(row):
        return None, {"excluded_non_chemistry": 1}
    source_id = normalize_text(row.get("id")) or f"row-{row_idx}"
    question = normalize_text(row.get("question"))
    answer = normalize_text(row.get("answer"))
    if not question:
        return None, {"excluded_missing_question": 1}
    if not answer:
        return None, {"excluded_missing_answer": 1}
    return (
        {
            "id": f"hle-chemistry-{source_id}",
            "source_dataset": SOURCE_DATASET,
            "source_id": source_id,
            "problem": question,
            "question": question,
            "answer": answer,
            "answer_type": normalize_text(row.get("answer_type")),
            "image": normalize_text(row.get("image")),
            "author_name": normalize_text(row.get("author_name")),
            "rationale": normalize_text(row.get("rationale")),
            "raw_subject": normalize_text(row.get("raw_subject")),
            "category": normalize_text(row.get("category")),
            "canary": normalize_text(row.get("canary")),
            "eval_kind": "hle",
        },
        {"selected_records": 1},
    )


def extract_pool(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    counts = Counter(
        {
            "scanned_source_rows": 0,
            "selected_records": 0,
            "excluded_non_chemistry": 0,
            "excluded_missing_question": 0,
            "excluded_missing_answer": 0,
        }
    )
    for row_idx, row in enumerate(rows):
        counts["scanned_source_rows"] += 1
        record, row_counts = transform_row(row, row_idx=row_idx)
        counts.update(row_counts)
        if record is not None:
            records.append(record)
    record_ids = [record["id"] for record in records]
    if len(record_ids) != len(set(record_ids)):
        raise ExtractionError("Duplicate sample ids detected in extracted HLE chemistry pool")
    return records, dict(counts)


def extract_pool_from_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    return extract_pool(iter_jsonl(path))


def build_manifest(
    *,
    dataset: str,
    split: str,
    input_jsonl: Path | None,
    output_path: Path,
    counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "source_dataset": dataset,
        "split": split,
        "input_jsonl": input_jsonl.as_posix() if input_jsonl is not None else None,
        "output_path": output_path.as_posix(),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "field_contract_version": FIELD_CONTRACT_VERSION,
        "selection": {
            "chemistry_match_fields": ["category", "raw_subject"],
            "chemistry_pattern": CHEMISTRY_PATTERN.pattern,
        },
        "counts": counts,
        "notes": [
            "HLE source rows are gated on Hugging Face; authenticated access may be required.",
            "Image fields are retained as source metadata; no local image bundle is generated by this extractor.",
            "Evaluation uses the HLE judge-style binary correctness rule.",
        ],
    }


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_manifest_path(output_jsonl: Path, explicit_manifest: str | None) -> Path:
    if explicit_manifest:
        return Path(explicit_manifest).expanduser().resolve()
    if output_jsonl.suffix:
        return output_jsonl.with_suffix(".manifest.json")
    return output_jsonl.with_name(output_jsonl.name + ".manifest.json")


def main() -> int:
    args = parse_args()
    output_jsonl = Path(args.output_jsonl).expanduser().resolve()
    manifest_out = resolve_manifest_path(output_jsonl, args.manifest_out)
    input_jsonl = Path(args.input_jsonl).expanduser().resolve() if args.input_jsonl else None

    if input_jsonl is not None:
        records, counts = extract_pool_from_jsonl(input_jsonl)
    else:
        records, counts = extract_pool(iter_hf_rows(dataset=args.dataset, split=args.split))
    manifest = build_manifest(
        dataset=args.dataset,
        split=args.split,
        input_jsonl=input_jsonl,
        output_path=output_jsonl,
        counts=counts,
    )
    write_jsonl(output_jsonl, records)
    write_json(manifest_out, manifest)
    print(
        json.dumps(
            {
                "output_jsonl": output_jsonl.as_posix(),
                "manifest": manifest_out.as_posix(),
                "counts": counts,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
