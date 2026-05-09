#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import re


IMAGE_PATH_FIELDS = ("question_image_paths", "explanation_image_paths")
OPTION_IMAGE_PATH_FIELD = "option_image_paths"
MEDIA_UPLOAD_URL_RE = re.compile(
    r"https?://superchem\.pku\.edu\.cn/media/uploads/[^\s)>'\"]+|/media/uploads/[^\s)>'\"]+",
    re.IGNORECASE,
)
MARKDOWN_IMAGE_URL_RE = re.compile(r"!\[[^\]]*]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
SUPERCHEM_ASSET_BASE_URL = "https://superchem.pku.edu.cn/"


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
    parser.add_argument(
        "--prune-unused",
        action="store_true",
        help="Keep only image paths whose /media/uploads locator appears in this record text.",
    )
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


def dedupe_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip().rstrip(".,;:")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def extract_media_locators(text: Any) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    candidates: list[str] = []
    for match in MARKDOWN_IMAGE_URL_RE.finditer(raw):
        url = match.group("url")
        if MEDIA_UPLOAD_URL_RE.fullmatch(url):
            candidates.append(url)
    for match in MEDIA_UPLOAD_URL_RE.finditer(raw):
        candidates.append(match.group(0))
    return dedupe_preserve_order(candidates)


def absolutize_media_locator(locator: str) -> str:
    text = str(locator or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("/media/uploads/"):
        return SUPERCHEM_ASSET_BASE_URL.rstrip("/") + text
    return text


def infer_extension(locator: str) -> str:
    parsed = urlparse(locator)
    suffix = Path(parsed.path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    return ".bin"


def asset_cache_relative_path(locator: str) -> Path:
    absolute = absolutize_media_locator(locator)
    digest = hashlib.sha1(absolute.encode("utf-8")).hexdigest()
    return Path("_shared") / digest[:2] / f"{digest}{infer_extension(absolute)}"


def path_matches_locator(path: Any, locator: str) -> bool:
    expected_suffix = asset_cache_relative_path(locator).as_posix()
    return Path(str(path or "")).as_posix().endswith(expected_suffix)


def find_matching_paths(paths: list[Any], locators: list[str]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for locator in locators:
        for path in paths:
            if not isinstance(path, str) or not path_matches_locator(path, locator):
                continue
            if path not in seen:
                result.append(path)
                seen.add(path)
            break
    return result


def flatten_option_paths(option_paths: Any) -> list[Any]:
    if not isinstance(option_paths, dict):
        return []
    paths: list[Any] = []
    for values in option_paths.values():
        if isinstance(values, list):
            paths.extend(values)
    return paths


def prune_record(record: dict[str, Any]) -> tuple[dict[str, Any], int]:
    updated = dict(record)
    changes = 0

    question_paths = updated.get("question_image_paths")
    if isinstance(question_paths, list):
        question_locators = extract_media_locators(updated.get("question"))
        pruned = find_matching_paths(question_paths, question_locators) if question_locators else []
        if pruned != question_paths:
            changes += len(question_paths) - len(pruned)
            updated["question_image_paths"] = pruned

    option_paths = updated.get(OPTION_IMAGE_PATH_FIELD)
    options = updated.get("options")
    if isinstance(option_paths, dict) and isinstance(options, dict):
        flattened_paths = flatten_option_paths(option_paths)
        pruned_options: dict[str, list[Any]] = {}
        kept_count = 0
        original_count = sum(len(values) for values in option_paths.values() if isinstance(values, list))
        for option, text in options.items():
            option_locators = extract_media_locators(text)
            if not option_locators:
                continue
            matches = find_matching_paths(flattened_paths, option_locators)
            if matches:
                pruned_options[str(option)] = matches
                kept_count += len(matches)
        if pruned_options != option_paths:
            changes += original_count - kept_count
            updated[OPTION_IMAGE_PATH_FIELD] = pruned_options

    explanation_paths = updated.get("explanation_image_paths")
    if isinstance(explanation_paths, list):
        explanation_locators = extract_media_locators(updated.get("reference_reasoning"))
        pruned = find_matching_paths(explanation_paths, explanation_locators) if explanation_locators else []
        if pruned != explanation_paths:
            changes += len(explanation_paths) - len(pruned)
            updated["explanation_image_paths"] = pruned

    return updated, changes


def repair_jsonl(
    path: Path,
    *,
    old_prefix: str,
    new_prefix: str,
    dry_run: bool = False,
    prune_unused: bool = False,
) -> dict[str, int | str | bool]:
    records: list[dict[str, Any]] = []
    records_seen = 0
    records_changed = 0
    rewritten_paths = 0
    records_pruned = 0
    pruned_paths = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records_seen += 1
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"Expected JSON object at record {records_seen}")
        updated, changes = rewrite_record(record, old_prefix=old_prefix, new_prefix=new_prefix)
        if prune_unused:
            updated, prune_changes = prune_record(updated)
            if prune_changes:
                records_pruned += 1
                pruned_paths += prune_changes
        if changes:
            records_changed += 1
            rewritten_paths += changes
        elif prune_unused and prune_changes:
            records_changed += 1
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
        "records_pruned": records_pruned,
        "pruned_paths": pruned_paths,
    }


def main() -> int:
    args = parse_args()
    summary = repair_jsonl(
        args.jsonl.expanduser().resolve(),
        old_prefix=str(args.old_prefix).rstrip("/"),
        new_prefix=str(args.new_prefix).rstrip("/"),
        dry_run=bool(args.dry_run),
        prune_unused=bool(args.prune_unused),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
