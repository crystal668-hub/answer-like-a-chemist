#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from urllib.request import urlopen


DATASET_NAME = "ZehuaZhao/SUPERChem"
SOURCE_PAGE = "https://huggingface.co/datasets/ZehuaZhao/SUPERChem"
DATASET_SERVER_BASE = "https://datasets-server.huggingface.co"
HF_API_BASE = "https://huggingface.co/api/datasets"
FIELD_CONTRACT_VERSION = "superchem-pool-v1"
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_CONFIG = "default"
DEFAULT_SPLIT = "train"
CHECKPOINT_TAG_RE = re.compile(r"<\s*checkpoint\b", re.IGNORECASE)
OPTION_LETTER_RE = re.compile(r"[A-Z]")
MEDIA_UPLOAD_PATH_RE = re.compile(r"^/media/uploads/[^?#]+\.(?:png|jpg|jpeg|gif|webp|bmp|svg|tif|tiff)$", re.IGNORECASE)


class ExtractionError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract SUPERChem into a benchmark-ready JSONL pool with multimodal records only."
    )
    parser.add_argument("--dataset", default=DATASET_NAME, help="Hugging Face dataset name")
    parser.add_argument("--output-jsonl", required=True, help="Path to the extracted SUPERChem question pool JSONL")
    parser.add_argument(
        "--assets-dir",
        help="Directory for localized images. Defaults to <output-jsonl parent>/../assets",
    )
    parser.add_argument(
        "--manifest-out",
        help="Optional manifest path. Defaults to <output-jsonl> with suffix .manifest.json",
    )
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Rows API page size")
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout in seconds")
    return parser.parse_args()


def build_url(path: str, **query: Any) -> str:
    return f"{DATASET_SERVER_BASE}{path}?{urlencode(query)}"


def raw_dataset_file_url(dataset: str, filename: str) -> str:
    return f"https://huggingface.co/datasets/{dataset}/resolve/main/{filename}"


def fetch_json(url: str, *, timeout_seconds: int) -> dict[str, Any]:
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ExtractionError(f"Expected JSON object from {url}, got {type(payload).__name__}")
    return payload


def download_binary(url: str, *, timeout_seconds: int) -> bytes:
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
        return response.read()


def fetch_splits(*, dataset: str, timeout_seconds: int) -> list[dict[str, Any]]:
    payload = fetch_json(build_url("/splits", dataset=dataset), timeout_seconds=timeout_seconds)
    splits = payload.get("splits") or []
    if not isinstance(splits, list):
        raise ExtractionError("Dataset splits payload is malformed")
    return [item for item in splits if isinstance(item, dict)]


def fetch_rows_page(
    *,
    dataset: str,
    config: str,
    split: str,
    offset: int,
    length: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    return fetch_json(
        build_url(
            "/rows",
            dataset=dataset,
            config=config,
            split=split,
            offset=offset,
            length=length,
        ),
        timeout_seconds=timeout_seconds,
    )


def fetch_dataset_metadata(*, dataset: str, timeout_seconds: int) -> dict[str, Any]:
    return fetch_json(f"{HF_API_BASE}/{dataset}", timeout_seconds=timeout_seconds)


def iter_rows_via_api(*, dataset: str, page_size: int, timeout_seconds: int) -> Iterable[tuple[str, str, int, dict[str, Any]]]:
    if page_size <= 0:
        raise ExtractionError("page_size must be positive")

    splits = fetch_splits(dataset=dataset, timeout_seconds=timeout_seconds)
    for split_info in splits:
        config = str(split_info.get("config") or DEFAULT_CONFIG)
        split = str(split_info.get("split") or DEFAULT_SPLIT)
        if not config or not split:
            continue

        offset = 0
        total_rows: int | None = None
        while total_rows is None or offset < total_rows:
            page = fetch_rows_page(
                dataset=dataset,
                config=config,
                split=split,
                offset=offset,
                length=page_size,
                timeout_seconds=timeout_seconds,
            )
            total_rows = int(page.get("num_rows_total") or 0)
            rows = page.get("rows") or []
            if not isinstance(rows, list):
                raise ExtractionError(f"Malformed rows payload for config={config} split={split}")
            if not rows:
                break
            for row_entry in rows:
                if not isinstance(row_entry, dict):
                    continue
                row = row_entry.get("row") or {}
                if isinstance(row, dict):
                    yield config, split, int(row_entry.get("row_idx") or 0), row
            offset += len(rows)


def load_rows_from_zip(payload: bytes) -> list[tuple[str, str, int, dict[str, Any]]]:
    rows: list[tuple[str, str, int, dict[str, Any]]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            lower = name.lower()
            if lower.endswith(".jsonl"):
                with archive.open(name) as handle:
                    for row_idx, raw_line in enumerate(io.TextIOWrapper(handle, encoding="utf-8"), start=0):
                        line = raw_line.strip()
                        if not line:
                            continue
                        payload_item = json.loads(line)
                        if isinstance(payload_item, dict):
                            rows.append((DEFAULT_CONFIG, DEFAULT_SPLIT, row_idx, payload_item))
                if rows:
                    return rows
            if lower.endswith(".json"):
                with archive.open(name) as handle:
                    payload_item = json.load(io.TextIOWrapper(handle, encoding="utf-8"))
                if isinstance(payload_item, list):
                    for row_idx, row in enumerate(payload_item):
                        if isinstance(row, dict):
                            rows.append((DEFAULT_CONFIG, DEFAULT_SPLIT, row_idx, row))
                    if rows:
                        return rows
            if lower.endswith(".csv"):
                with archive.open(name) as handle:
                    reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"))
                    for row_idx, row in enumerate(reader):
                        rows.append((DEFAULT_CONFIG, DEFAULT_SPLIT, row_idx, dict(row)))
                if rows:
                    return rows
    raise ExtractionError("SUPERChem zip fallback did not contain a readable JSON/JSONL/CSV table.")


def load_rows_from_parquet(payload: bytes) -> list[tuple[str, str, int, dict[str, Any]]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ExtractionError(
            "SUPERChem rows API failed and parquet fallback requires `pyarrow`. Install pyarrow and retry."
        ) from exc

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_path = Path(temp_dir_name) / "dataset.parquet"
        temp_path.write_bytes(payload)
        table = pq.read_table(temp_path)
        rows = table.to_pylist()
    result: list[tuple[str, str, int, dict[str, Any]]] = []
    for row_idx, row in enumerate(rows):
        if isinstance(row, dict):
            result.append((DEFAULT_CONFIG, DEFAULT_SPLIT, row_idx, row))
    if not result:
        raise ExtractionError("Parquet fallback loaded no rows.")
    return result


def iter_rows_via_fallback_files(*, dataset: str, timeout_seconds: int) -> Iterable[tuple[str, str, int, dict[str, Any]]]:
    metadata = fetch_dataset_metadata(dataset=dataset, timeout_seconds=timeout_seconds)
    siblings = metadata.get("siblings") or []
    filenames = [str(item.get("rfilename") or "") for item in siblings if isinstance(item, dict)]
    errors: list[str] = []
    for candidate in filenames:
        lower = candidate.lower()
        if lower.endswith(".zip"):
            try:
                payload = download_binary(raw_dataset_file_url(dataset, candidate), timeout_seconds=timeout_seconds)
                return load_rows_from_zip(payload)
            except Exception as exc:
                errors.append(f"{candidate}: {exc!r}")
    for candidate in filenames:
        if candidate.lower().endswith(".parquet"):
            try:
                payload = download_binary(raw_dataset_file_url(dataset, candidate), timeout_seconds=timeout_seconds)
                return load_rows_from_parquet(payload)
            except Exception as exc:
                errors.append(f"{candidate}: {exc!r}")
    details = "; ".join(errors) if errors else "no candidate files found"
    raise ExtractionError(f"No readable tabular source file found in SUPERChem dataset metadata. Details: {details}")


def iter_source_rows(*, dataset: str, page_size: int, timeout_seconds: int) -> Iterable[tuple[str, str, int, dict[str, Any]]]:
    try:
        yield from iter_rows_via_api(dataset=dataset, page_size=page_size, timeout_seconds=timeout_seconds)
        return
    except Exception as api_exc:
        try:
            yield from iter_rows_via_fallback_files(dataset=dataset, timeout_seconds=timeout_seconds)
            return
        except Exception as fallback_exc:
            raise ExtractionError(
                f"Failed to load SUPERChem rows via datasets-server and fallback files. "
                f"rows_api_error={api_exc!r}; fallback_error={fallback_exc!r}"
            ) from fallback_exc


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def looks_like_image_locator(value: Any) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return Path(urlparse(text).path).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"}
    return bool(MEDIA_UPLOAD_PATH_RE.match(text))


def parse_json_if_needed(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def normalize_options(value: Any) -> dict[str, str]:
    parsed = parse_json_if_needed(value)
    if isinstance(parsed, dict):
        result: dict[str, str] = {}
        for key, option_value in parsed.items():
            letter = normalize_text(key).upper()
            if not letter:
                continue
            if isinstance(option_value, dict):
                text = normalize_text(
                    option_value.get("text")
                    or option_value.get("label")
                    or option_value.get("content")
                    or option_value.get("value")
                )
            else:
                text = normalize_text(option_value)
            result[letter] = text
        return {key: result[key] for key in sorted(result)}
    if isinstance(parsed, list):
        result = {}
        for index, option_value in enumerate(parsed):
            letter = chr(ord("A") + index)
            if isinstance(option_value, dict):
                text = normalize_text(
                    option_value.get("text")
                    or option_value.get("label")
                    or option_value.get("content")
                    or option_value.get("value")
                )
            else:
                text = normalize_text(option_value)
            result[letter] = text
        return result
    return {}


def extract_image_urls(value: Any) -> list[str]:
    parsed = parse_json_if_needed(value)
    if parsed in (None, "", [], {}):
        return []
    if isinstance(parsed, str):
        return [parsed.strip()] if parsed.strip() else []
    if isinstance(parsed, dict):
        for key in ("url", "src", "image_url", "path", "file", "href"):
            candidate = normalize_text(parsed.get(key))
            if candidate:
                return [candidate]
        key_urls = [normalize_text(key) for key in parsed.keys() if looks_like_image_locator(key)]
        if key_urls:
            return key_urls
        urls: list[str] = []
        for nested in parsed.values():
            urls.extend(extract_image_urls(nested))
        return urls
    if isinstance(parsed, list):
        urls: list[str] = []
        for item in parsed:
            urls.extend(extract_image_urls(item))
        return urls
    return []


def normalize_option_images(value: Any, option_keys: Iterable[str]) -> dict[str, list[str]]:
    parsed = parse_json_if_needed(value)
    valid_keys = {str(key).strip().upper() for key in option_keys if str(key).strip()}
    result: dict[str, list[str]] = {}
    if parsed in (None, "", [], {}):
        return result
    if isinstance(parsed, dict):
        normalized_keys = {normalize_text(key).upper() for key in parsed.keys() if normalize_text(key)}
        if normalized_keys and normalized_keys.issubset(valid_keys):
            for key, option_value in parsed.items():
                letter = normalize_text(key).upper()
                if letter in valid_keys:
                    urls = extract_image_urls(option_value)
                    if urls:
                        result[letter] = urls
            return result
        shared_urls = extract_image_urls(parsed)
        if shared_urls:
            return {"_shared": shared_urls}
        for key, option_value in parsed.items():
            letter = normalize_text(key).upper()
            if letter in valid_keys:
                urls = extract_image_urls(option_value)
                if urls:
                    result[letter] = urls
        return result
    if isinstance(parsed, list):
        per_option: dict[str, list[str]] = {}
        for item in parsed:
            if isinstance(item, dict):
                candidate_key = normalize_text(item.get("option") or item.get("label") or item.get("key")).upper()
                if candidate_key in valid_keys:
                    urls = extract_image_urls(item.get("images") or item.get("url") or item)
                    if urls:
                        per_option.setdefault(candidate_key, []).extend(urls)
        if per_option:
            return per_option
        shared_urls = extract_image_urls(parsed)
        if shared_urls:
            return {"_shared": shared_urls}
        return result
    return result


def normalize_answer_letters(value: Any, valid_options: Iterable[str]) -> list[str]:
    valid = {key.upper() for key in valid_options}
    parsed = parse_json_if_needed(value)
    candidates: list[str] = []
    if isinstance(parsed, list):
        candidates = [normalize_text(item).upper() for item in parsed]
    elif isinstance(parsed, dict):
        candidates = [normalize_text(item).upper() for item in parsed.keys()]
    else:
        text = normalize_text(parsed).upper()
        if text:
            candidates = OPTION_LETTER_RE.findall(text)
    ordered = [item for item in candidates if item in valid]
    deduped = sorted(set(ordered))
    if not deduped:
        raise ExtractionError(f"Could not parse answer letters from value: {value!r}")
    return deduped


def has_checkpoint_tags(text: str) -> bool:
    return bool(CHECKPOINT_TAG_RE.search(text or ""))


def build_prompt(question: str, options: dict[str, str]) -> str:
    lines = [question.strip(), "", "Options:"]
    for letter, text in options.items():
        option_text = text.strip() or "[see image]"
        lines.append(f"{letter}. {option_text}")
    return "\n".join(lines).strip()


def sample_id_for(uuid: str, modality: str) -> str:
    suffix = "mm" if modality == "multimodal" else "txt"
    return f"superchem-{uuid}-{suffix}"


def infer_extension(url: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() == "filename":
            query_suffix = Path(value).suffix.lower()
            if query_suffix and len(query_suffix) <= 8:
                return query_suffix
    return ".bin"


def absolutize_url(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/media/uploads/"):
        return urljoin("https://superchem.pku.edu.cn/", url.lstrip("/"))
    return urljoin(f"{SOURCE_PAGE}/", url.lstrip("/"))


def cache_relative_path_for_url(image_url: str) -> Path:
    absolute = absolutize_url(image_url)
    digest = hashlib.sha1(absolute.encode("utf-8")).hexdigest()
    extension = infer_extension(absolute)
    return Path("_shared") / digest[:2] / f"{digest}{extension}"


def localize_images(
    *,
    image_urls: list[str],
    assets_dir: Path,
    timeout_seconds: int,
    path_base_dir: Path | None = None,
) -> tuple[list[str], int, int]:
    localized: list[str] = []
    downloaded = 0
    referenced = 0
    seen_local_paths: set[str] = set()
    for image_url in image_urls:
        absolute = absolutize_url(image_url)
        output_path = assets_dir / cache_relative_path_for_url(absolute)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not output_path.is_file():
            output_path.write_bytes(download_binary(absolute, timeout_seconds=timeout_seconds))
            downloaded += 1
        resolved_path = output_path.resolve()
        if path_base_dir is not None:
            stored_path = Path(os.path.relpath(resolved_path, start=path_base_dir.resolve())).as_posix()
        else:
            stored_path = resolved_path.as_posix()
        if stored_path not in seen_local_paths:
            localized.append(stored_path)
            seen_local_paths.add(stored_path)
        referenced += 1
    return localized, downloaded, referenced


def transform_row(
    *,
    dataset: str,
    config: str,
    split: str,
    row_idx: int,
    row: dict[str, Any],
    assets_dir: Path,
    output_base_dir: Path | None = None,
    timeout_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    question_type = normalize_text(row.get("question_type")).lower()
    uuid = normalize_text(row.get("uuid"))
    question = normalize_text(row.get("question_en"))
    explanation = normalize_text(row.get("explanation_en"))
    options = normalize_options(row.get("options_en"))
    if not uuid:
        raise ExtractionError(f"Missing uuid for config={config} split={split} row_idx={row_idx}")
    if question_type != "multiple_choice":
        return [], {"excluded_non_multiple_choice": 1}
    if not question:
        return [], {"excluded_missing_question": 1}
    if not options:
        return [], {"excluded_missing_options": 1}
    if not explanation:
        return [], {"excluded_missing_explanation": 1}
    if not has_checkpoint_tags(explanation):
        return [], {"excluded_missing_checkpoint_tags": 1}

    answer_letters = normalize_answer_letters(row.get("answer_en"), options.keys())
    prompt = build_prompt(question, options)

    question_image_locators = extract_image_urls(row.get("question_images"))
    question_images, question_downloaded, question_refs = localize_images(
        image_urls=question_image_locators,
        assets_dir=assets_dir,
        path_base_dir=output_base_dir,
        timeout_seconds=timeout_seconds,
    )
    option_images_raw = normalize_option_images(row.get("options_images"), options.keys())
    option_images: dict[str, list[str]] = {}
    option_downloaded = 0
    option_refs = 0
    for letter, urls in option_images_raw.items():
        localized, downloaded, refs = localize_images(
            image_urls=urls,
            assets_dir=assets_dir,
            path_base_dir=output_base_dir,
            timeout_seconds=timeout_seconds,
        )
        if localized:
            option_images[letter] = localized
        option_downloaded += downloaded
        option_refs += refs
    explanation_image_locators = extract_image_urls(row.get("explanation_images"))
    explanation_images, explanation_downloaded, explanation_refs = localize_images(
        image_urls=explanation_image_locators,
        assets_dir=assets_dir,
        path_base_dir=output_base_dir,
        timeout_seconds=timeout_seconds,
    )

    has_images = bool(question_images or any(option_images.values()))
    base_payload = {
        "source_dataset": dataset,
        "source_page": SOURCE_PAGE,
        "source_config": config,
        "split": split,
        "source_row_idx": row_idx,
        "source_uuid": uuid,
        "question_type": "multiple_choice",
        "language": "en",
        "prompt": prompt,
        "question": question,
        "options": options,
        "answer": "|".join(answer_letters),
        "reference_reasoning": explanation,
        "question_image_paths": [],
        "option_image_paths": {},
        "explanation_image_paths": explanation_images,
        "source_question_image_paths": question_image_locators,
        "source_option_image_paths": option_images_raw,
        "source_explanation_image_paths": explanation_image_locators,
        "source_has_images": has_images,
        "canary": normalize_text(row.get("canary")),
        "eval_kind": "superchem_multiple_choice_rpf",
    }
    if not has_images:
        return [], {
            "excluded_missing_images_for_multimodal": 1,
        }

    records = [
        {
            **base_payload,
            "id": sample_id_for(uuid, "multimodal"),
            "modality": "multimodal",
            "has_images": True,
            "question_image_paths": question_images,
            "option_image_paths": option_images,
        }
    ]
    return records, {
        "downloaded_question_images": question_downloaded,
        "downloaded_option_images": option_downloaded,
        "downloaded_explanation_images": explanation_downloaded,
        "question_image_references": question_refs,
        "option_image_references": option_refs,
        "explanation_image_references": explanation_refs,
        "source_rows_with_images": 1,
        "selected_source_rows": 1,
        "multimodal_records": 1,
        "selected_records": 1,
    }


def extract_pool(
    *,
    dataset: str,
    assets_dir: Path,
    page_size: int,
    timeout_seconds: int,
    output_base_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_records: list[dict[str, Any]] = []
    stats = Counter()

    for config, split, row_idx, row in iter_source_rows(dataset=dataset, page_size=page_size, timeout_seconds=timeout_seconds):
        stats["scanned_source_rows"] += 1
        records, row_stats = transform_row(
            dataset=dataset,
            config=config,
            split=split,
            row_idx=row_idx,
            row=row,
            assets_dir=assets_dir,
            output_base_dir=output_base_dir,
            timeout_seconds=timeout_seconds,
        )
        stats.update(row_stats)
        all_records.extend(records)

    record_ids = [str(record["id"]) for record in all_records]
    if len(record_ids) != len(set(record_ids)):
        raise ExtractionError("Duplicate sample ids detected in extracted SUPERChem pool")

    return all_records, dict(stats)


def build_manifest(
    *,
    dataset: str,
    output_path: Path,
    assets_dir: Path,
    stats: dict[str, Any],
    page_size: int,
) -> dict[str, Any]:
    return {
        "source_dataset": dataset,
        "source_page": SOURCE_PAGE,
        "output_path": output_path.name,
        "assets_dir": Path(os.path.relpath(assets_dir.resolve(), start=output_path.parent.resolve())).as_posix(),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "field_contract_version": FIELD_CONTRACT_VERSION,
        "page_size": page_size,
        "language": "en",
        "selection": {
            "question_type": "multiple_choice",
            "requires_checkpoint_tags": True,
            "multimodal_records_only": True,
            "requires_localized_question_or_option_images": True,
        },
        "counts": {
            "scanned_source_rows": stats.get("scanned_source_rows", 0),
            "selected_source_rows": stats.get("selected_source_rows", 0),
            "selected_records": stats.get("selected_records", 0),
            "text_only_records": 0,
            "multimodal_records": stats.get("multimodal_records", 0),
            "source_rows_with_images": stats.get("source_rows_with_images", 0),
            "downloaded_question_images": stats.get("downloaded_question_images", 0),
            "downloaded_option_images": stats.get("downloaded_option_images", 0),
            "downloaded_explanation_images": stats.get("downloaded_explanation_images", 0),
            "question_image_references": stats.get("question_image_references", 0),
            "option_image_references": stats.get("option_image_references", 0),
            "explanation_image_references": stats.get("explanation_image_references", 0),
            "excluded_non_multiple_choice": stats.get("excluded_non_multiple_choice", 0),
            "excluded_missing_question": stats.get("excluded_missing_question", 0),
            "excluded_missing_options": stats.get("excluded_missing_options", 0),
            "excluded_missing_explanation": stats.get("excluded_missing_explanation", 0),
            "excluded_missing_checkpoint_tags": stats.get("excluded_missing_checkpoint_tags", 0),
            "excluded_missing_images_for_multimodal": stats.get("excluded_missing_images_for_multimodal", 0),
        },
        "notes": [
            "English records only.",
            "As of 2026-04-18, all text_only (-txt) SUPERChem benchmark records were removed.",
            "The pool now retains only multimodal (-mm) records because image-dependent questions are not meaningful without their required visual inputs.",
            "reference_reasoning preserves the original explanation_en for downstream RPF evaluation.",
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


def resolve_assets_dir(output_jsonl: Path, explicit_assets_dir: str | None) -> Path:
    if explicit_assets_dir:
        return Path(explicit_assets_dir).expanduser().resolve()
    return output_jsonl.parent.parent / "assets"


def main() -> int:
    args = parse_args()
    output_jsonl = Path(args.output_jsonl).expanduser().resolve()
    manifest_out = resolve_manifest_path(output_jsonl, args.manifest_out)
    assets_dir = resolve_assets_dir(output_jsonl, args.assets_dir)

    records, stats = extract_pool(
        dataset=args.dataset,
        assets_dir=assets_dir,
        page_size=args.page_size,
        timeout_seconds=args.timeout_seconds,
        output_base_dir=output_jsonl.parent,
    )
    manifest = build_manifest(
        dataset=args.dataset,
        output_path=output_jsonl,
        assets_dir=assets_dir,
        stats=stats,
        page_size=args.page_size,
    )
    write_jsonl(output_jsonl, records)
    write_json(manifest_out, manifest)
    print(
        json.dumps(
            {
                "output_jsonl": output_jsonl.as_posix(),
                "manifest": manifest_out.as_posix(),
                "assets_dir": assets_dir.as_posix(),
                "counts": manifest["counts"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
