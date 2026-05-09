from __future__ import annotations

import base64
import binascii
import hashlib
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .datasets import BenchmarkRecord, source_pair_key


class RuntimeBundleError(RuntimeError):
    pass


@dataclass
class RuntimeBundle:
    bundle_dir: Path
    question_markdown: Path
    image_files: list[Path]

    def to_meta(self) -> dict[str, Any]:
        return {
            "bundle_dir": str(self.bundle_dir),
            "question_markdown": str(self.question_markdown),
            "image_files": [str(path) for path in self.image_files],
        }


RUNTIME_BUNDLE_LOCK = threading.Lock()
SUPERCHEM_MEDIA_UPLOAD_URL_RE = re.compile(
    r"https?://superchem\.pku\.edu\.cn/media/uploads/[^\s)>'\"]+|/media/uploads/[^\s)>'\"]+",
    re.IGNORECASE,
)
SUPERCHEM_MARKDOWN_IMAGE_URL_RE = re.compile(r"!\[[^\]]*]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
SUPERCHEM_ASSET_BASE_URL = "https://superchem.pku.edu.cn/"
SUPERCHEM_LEGACY_FALLBACK_MAX_IMAGES = 32
DATA_URI_IMAGE_RE = re.compile(r"^data:(?P<mime>image/[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$", re.DOTALL)


def _slugify(value: str, *, limit: int = 64) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    cleaned = cleaned or "item"
    if len(cleaned) <= limit:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned[: limit - 9]}-{digest}".strip("-")


def _resolve_local_image_path(raw_path: str) -> Path | None:
    candidate = Path(raw_path).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    return None


def _resolve_record_local_image_path(record: BenchmarkRecord, raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        source_dir = Path(record.source_file).expanduser().resolve().parent
        candidate = (source_dir / path).resolve()
        if candidate.is_file():
            return candidate
    return _resolve_local_image_path(raw_path)


def _resolve_record_relative_image_path(record: BenchmarkRecord, raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return None
    source_dir = Path(record.source_file).expanduser().resolve().parent
    candidate = (source_dir / path).resolve()
    if candidate.is_file():
        return candidate
    return None


def _dedupe_text_preserve_order(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip().rstrip(".,;:")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _extract_superchem_media_locators(text: Any) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    candidates: list[str] = []
    for match in SUPERCHEM_MARKDOWN_IMAGE_URL_RE.finditer(raw):
        url = match.group("url")
        if SUPERCHEM_MEDIA_UPLOAD_URL_RE.fullmatch(url):
            candidates.append(url)
    for match in SUPERCHEM_MEDIA_UPLOAD_URL_RE.finditer(raw):
        candidates.append(match.group(0))
    return _dedupe_text_preserve_order(candidates)


def _superchem_absolutize_media_locator(locator: str) -> str:
    text = str(locator or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("/media/uploads/"):
        return SUPERCHEM_ASSET_BASE_URL.rstrip("/") + text
    return text


def _superchem_infer_extension(locator: str) -> str:
    parsed = urlparse(locator)
    suffix = Path(parsed.path).suffix.lower()
    if suffix and len(suffix) <= 8:
        return suffix
    return ".bin"


def _superchem_asset_cache_relative_path(locator: str) -> Path:
    absolute = _superchem_absolutize_media_locator(locator)
    digest = hashlib.sha1(absolute.encode("utf-8")).hexdigest()
    return Path("_shared") / digest[:2] / f"{digest}{_superchem_infer_extension(absolute)}"


def _superchem_path_matches_locator(raw_path: str, locator: str) -> bool:
    expected_suffix = _superchem_asset_cache_relative_path(locator).as_posix()
    return Path(str(raw_path or "")).as_posix().endswith(expected_suffix)


def _superchem_payload_image_path_items(record: BenchmarkRecord, *, include_explanation: bool = False) -> list[str]:
    payload = record.payload
    paths: list[str] = []
    for item in payload.get("question_image_paths") or []:
        text = str(item or "").strip()
        if text:
            paths.append(text)
    option_paths = payload.get("option_image_paths") or {}
    if isinstance(option_paths, dict):
        for items in option_paths.values():
            for item in items or []:
                text = str(item or "").strip()
                if text:
                    paths.append(text)
    if include_explanation:
        for item in payload.get("explanation_image_paths") or []:
            text = str(item or "").strip()
            if text:
                paths.append(text)
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _superchem_visible_image_locators(record: BenchmarkRecord) -> list[str]:
    payload = record.payload
    locators: list[str] = []
    locators.extend(_extract_superchem_media_locators(payload.get("question") or record.prompt))
    options = payload.get("options") or {}
    if isinstance(options, dict):
        for key in sorted(options):
            locators.extend(_extract_superchem_media_locators(options.get(key)))
    return _dedupe_text_preserve_order(locators)


def _superchem_find_payload_path_for_locator(record: BenchmarkRecord, locator: str) -> str | None:
    for path in _superchem_payload_image_path_items(record, include_explanation=False):
        if _superchem_path_matches_locator(path, locator):
            return path
    return None


def _superchem_legacy_runtime_image_path_items(record: BenchmarkRecord) -> list[str]:
    paths = _superchem_payload_image_path_items(record, include_explanation=False)
    option_paths = record.payload.get("option_image_paths") or {}
    has_shared_bucket = isinstance(option_paths, dict) and "_shared" in option_paths
    if has_shared_bucket or len(paths) > SUPERCHEM_LEGACY_FALLBACK_MAX_IMAGES:
        return []
    return paths


def _superchem_image_path_items(record: BenchmarkRecord) -> list[str]:
    locators = _superchem_visible_image_locators(record)
    if locators:
        paths: list[str] = []
        for locator in locators:
            path = _superchem_find_payload_path_for_locator(record, locator)
            if path:
                paths.append(path)
        return _dedupe_text_preserve_order(paths)
    return _superchem_legacy_runtime_image_path_items(record)


def superchem_image_paths(record: BenchmarkRecord) -> list[Path]:
    paths: list[Path] = []
    for item in _superchem_image_path_items(record):
        resolved = _resolve_record_relative_image_path(record, item)
        if resolved is not None:
            paths.append(resolved)
    return paths


def _rewrite_superchem_media_locators(text: str, image_rewrites: dict[str, str]) -> str:
    rewritten = text
    for locator, relpath in image_rewrites.items():
        rewritten = rewritten.replace(locator, relpath)
    return rewritten


def build_superchem_question_markdown(
    record: BenchmarkRecord,
    *,
    image_relpaths: list[str],
    image_rewrites: dict[str, str] | None = None,
) -> str:
    payload = record.payload
    options = payload.get("options") or {}
    image_rewrites = image_rewrites or {}
    question_text = _rewrite_superchem_media_locators(str(payload.get("question") or record.prompt).strip(), image_rewrites)
    lines = [
        "# SUPERChem Benchmark Record",
        f"Record ID: {record.record_id}",
        f"Source UUID: {source_pair_key(record)}",
        f"Modality: {payload.get('modality') or 'text_only'}",
        "",
        "Question:",
        question_text,
        "",
        "Options:",
    ]
    if isinstance(options, dict):
        for key in sorted(options):
            value = str(options.get(key) or "").strip() or "[see image]"
            value = _rewrite_superchem_media_locators(value, image_rewrites)
            lines.append(f"- {key}. {value}")
    if image_relpaths:
        lines.extend(["", "Local images to inspect:"])
        for item in image_relpaths:
            lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def _extension_from_image_mime(mime_type: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
    }
    return mapping.get(mime_type.lower(), ".bin")


def build_hle_question_markdown(record: BenchmarkRecord, *, image_relpaths: list[str]) -> str:
    lines = [
        "# HLE Benchmark Record",
        f"Record ID: {record.record_id}",
        "",
        "Question:",
        str(record.payload.get("question") or record.prompt).strip(),
    ]
    if image_relpaths:
        lines.extend(["", "Local images to inspect:"])
        for item in image_relpaths:
            lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def ensure_runtime_bundle(record: BenchmarkRecord, *, bundle_root: Path) -> RuntimeBundle | None:
    if record.dataset not in {"superchem", "hle"}:
        return None
    if record.dataset == "hle":
        image_value = str(record.payload.get("image") or record.grading.config.get("image") or "").strip()
        if not image_value:
            return None
    bundle_dir = bundle_root / _slugify(record.record_id, limit=80)
    question_markdown = bundle_dir / "question.md"
    image_dir = bundle_dir / "images"
    image_files: list[Path] = []

    with RUNTIME_BUNDLE_LOCK:
        bundle_dir.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)
        image_relpaths: list[str] = []
        if record.dataset == "superchem":
            missing_paths: list[str] = []
            visible_locators = _superchem_visible_image_locators(record)
            if visible_locators:
                raw_paths: list[str] = []
                for locator in visible_locators:
                    raw_path = _superchem_find_payload_path_for_locator(record, locator)
                    if raw_path is None:
                        missing_paths.append(locator)
                        continue
                    raw_paths.append(raw_path)
            else:
                raw_paths = _superchem_legacy_runtime_image_path_items(record)
            image_rewrites: dict[str, str] = {}
            for index, raw_path in enumerate(raw_paths, start=1):
                source_path = _resolve_record_relative_image_path(record, raw_path)
                if source_path is None:
                    missing_paths.append(raw_path)
                    continue
                extension = source_path.suffix or ".bin"
                target_path = image_dir / f"img{index:02d}{extension}"
                shutil.copy2(source_path, target_path)
                image_files.append(target_path)
                image_relpath = str(target_path.relative_to(bundle_dir))
                image_relpaths.append(image_relpath)
                if visible_locators and index <= len(visible_locators):
                    locator = visible_locators[index - 1]
                    image_rewrites[locator] = image_relpath
                    image_rewrites[_superchem_absolutize_media_locator(locator)] = image_relpath
            expects_images = bool(
                str(record.payload.get("modality") or "").lower() == "multimodal"
                or record.payload.get("has_images")
                or record.payload.get("source_has_images")
                or visible_locators
                or raw_paths
            )
            if expects_images and (missing_paths or not image_files):
                raise RuntimeBundleError(
                    f"SUPERChem multimodal record `{record.record_id}` has unavailable image inputs: "
                    + ", ".join(missing_paths or ["<no image paths>"])
                )
            question_markdown.write_text(
                build_superchem_question_markdown(
                    record,
                    image_relpaths=image_relpaths,
                    image_rewrites=image_rewrites,
                ),
                encoding="utf-8",
            )
        elif record.dataset == "hle":
            match = DATA_URI_IMAGE_RE.match(image_value)
            if not match:
                parsed = urlparse(image_value)
                if parsed.scheme in {"http", "https"}:
                    raise RuntimeBundleError(f"HLE record `{record.record_id}` references a remote image that is not localized.")
                source_path = _resolve_record_local_image_path(record, image_value)
                if source_path is None:
                    raise RuntimeBundleError(f"HLE record `{record.record_id}` image is unavailable: {image_value}")
                target_path = image_dir / f"hle-image-01{source_path.suffix or '.bin'}"
                shutil.copy2(source_path, target_path)
            else:
                try:
                    image_bytes = base64.b64decode(match.group("data"), validate=True)
                except binascii.Error as exc:
                    raise RuntimeBundleError(f"HLE record `{record.record_id}` has invalid base64 image data.") from exc
                target_path = image_dir / f"hle-image-01{_extension_from_image_mime(match.group('mime'))}"
                target_path.write_bytes(image_bytes)
            image_files.append(target_path)
            image_relpaths.append(str(target_path.relative_to(bundle_dir)))
            question_markdown.write_text(
                build_hle_question_markdown(record, image_relpaths=image_relpaths),
                encoding="utf-8",
            )
    return RuntimeBundle(bundle_dir=bundle_dir, question_markdown=question_markdown, image_files=image_files)
