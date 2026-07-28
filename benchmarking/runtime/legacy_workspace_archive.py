from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LEGACY_ARCHIVE_KIND = "openclaw-legacy-benchmark-workspace-archive"
LEGACY_ARCHIVE_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "legacy-archive-manifest.json"


class LegacyWorkspaceArchiveError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = metadata.st_mode
        if path.is_symlink() or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise LegacyWorkspaceArchiveError(
                f"Legacy workspace contains an unsupported path: {path}"
            )
        entry: dict[str, Any] = {
            "path": relative,
            "type": "directory" if stat.S_ISDIR(mode) else "file",
            "mode": stat.S_IMODE(mode),
            "mtime_ns": metadata.st_mtime_ns,
        }
        if stat.S_ISREG(mode):
            entry["size"] = metadata.st_size
            entry["sha256"] = _sha256_file(path)
        entries.append(entry)
    return entries


def _inventory_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return {
        "file_count": sum(entry["type"] == "file" for entry in entries),
        "directory_count": sum(entry["type"] == "directory" for entry in entries),
        "total_bytes": sum(int(entry.get("size", 0)) for entry in entries),
        "inventory_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _snapshot(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entries = _inventory(root)
    return entries, _inventory_summary(entries)


def _timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _load_manifest(archive_dir: Path) -> dict[str, Any]:
    manifest_path = archive_dir / MANIFEST_FILENAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LegacyWorkspaceArchiveError(f"Unable to read legacy archive manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise LegacyWorkspaceArchiveError("Legacy archive manifest is not a JSON object")
    if (
        payload.get("kind") != LEGACY_ARCHIVE_KIND
        or payload.get("schema_version") != LEGACY_ARCHIVE_SCHEMA_VERSION
    ):
        raise LegacyWorkspaceArchiveError("Legacy archive manifest kind or schema version is invalid")
    return payload


def verify_legacy_workspace_archive(archive_dir: Path) -> dict[str, Any]:
    archive_dir = archive_dir.expanduser().resolve()
    payload = _load_manifest(archive_dir)
    workspace = archive_dir / "workspace"
    if not workspace.is_dir():
        raise LegacyWorkspaceArchiveError(f"Archived workspace is missing: {workspace}")
    if str(payload.get("archive_workspace") or "") != str(workspace):
        raise LegacyWorkspaceArchiveError("Legacy archive workspace path does not match its manifest")
    expected_entries = payload.get("entries")
    if not isinstance(expected_entries, list):
        raise LegacyWorkspaceArchiveError("Legacy archive manifest entries are missing")
    actual_entries, actual_summary = _snapshot(workspace)
    if actual_entries != expected_entries:
        raise LegacyWorkspaceArchiveError("Legacy archive file inventory does not match its manifest")
    for name, value in actual_summary.items():
        if payload.get(name) != value:
            raise LegacyWorkspaceArchiveError(f"Legacy archive manifest field `{name}` is invalid")
    return {
        "status": "verified",
        "archive_dir": str(archive_dir),
        "archive_workspace": str(workspace),
        **actual_summary,
    }


def archive_legacy_workspace(
    source: Path,
    *,
    archive_root: Path,
    archived_at: datetime | None = None,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    archive_root = archive_root.expanduser().resolve()
    if not source.is_dir():
        raise LegacyWorkspaceArchiveError(f"Legacy workspace does not exist: {source}")
    try:
        archive_root.relative_to(source)
    except ValueError:
        pass
    else:
        raise LegacyWorkspaceArchiveError("Legacy archive root cannot be inside the source workspace")

    timestamp = _timestamp(archived_at)
    archive_name = f"{source.name}-{timestamp}"
    final_archive = archive_root / archive_name
    if final_archive.exists() or final_archive.is_symlink():
        raise LegacyWorkspaceArchiveError(f"Legacy archive already exists: {final_archive}")

    archive_root.mkdir(parents=True, exist_ok=True)
    temporary_archive = archive_root / f".{archive_name}.archive-{uuid.uuid4().hex}"
    try:
        source_entries_before, source_summary_before = _snapshot(source)
        temporary_workspace = temporary_archive / "workspace"
        shutil.copytree(source, temporary_workspace, copy_function=shutil.copy2)
        source_entries_after, source_summary_after = _snapshot(source)
        copied_entries, copied_summary = _snapshot(temporary_workspace)
        if source_entries_before != source_entries_after or source_summary_before != source_summary_after:
            raise LegacyWorkspaceArchiveError("Legacy workspace changed while it was being archived")
        if source_entries_before != copied_entries or source_summary_before != copied_summary:
            raise LegacyWorkspaceArchiveError("Copied legacy workspace failed integrity verification")

        manifest = {
            "kind": LEGACY_ARCHIVE_KIND,
            "schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
            "archive_id": archive_name,
            "archived_at": datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ")
            .replace(tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "source_workspace": str(source),
            "workspace_name": source.name,
            "archive_workspace": str(final_archive / "workspace"),
            "contains_git_metadata": (source / ".git").is_dir(),
            **source_summary_before,
            "entries": source_entries_before,
        }
        (temporary_archive / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_archive, final_archive)
        verification = verify_legacy_workspace_archive(final_archive)
        return {
            "status": "archived",
            "source_workspace": str(source),
            "archive_dir": str(final_archive),
            "manifest": str(final_archive / MANIFEST_FILENAME),
            **{key: verification[key] for key in ("file_count", "directory_count", "total_bytes", "inventory_sha256")},
        }
    except LegacyWorkspaceArchiveError:
        raise
    except Exception as exc:
        raise LegacyWorkspaceArchiveError(f"Unable to archive legacy workspace: {exc}") from exc
    finally:
        if temporary_archive.exists():
            shutil.rmtree(temporary_archive)


def verify_source_matches_archive(source: Path, archive_dir: Path) -> dict[str, Any]:
    source = source.expanduser().resolve()
    archive_dir = archive_dir.expanduser().resolve()
    payload = _load_manifest(archive_dir)
    if str(payload.get("source_workspace") or "") != str(source):
        raise LegacyWorkspaceArchiveError("Archive manifest belongs to a different source workspace")
    verification = verify_legacy_workspace_archive(archive_dir)
    if not source.is_dir():
        raise LegacyWorkspaceArchiveError(f"Legacy workspace does not exist: {source}")
    source_entries, source_summary = _snapshot(source)
    if source_entries != payload.get("entries"):
        raise LegacyWorkspaceArchiveError("Legacy workspace changed after it was archived")
    for name, value in source_summary.items():
        if payload.get(name) != value:
            raise LegacyWorkspaceArchiveError(f"Legacy source field `{name}` no longer matches its archive")
    return {**verification, "status": "source_matches_archive", "source_workspace": str(source)}


def archive_legacy_workspaces(
    sources: Iterable[Path],
    *,
    archive_root: Path,
    delete_sources: bool = False,
    archived_at: datetime | None = None,
) -> dict[str, Any]:
    resolved_sources = [source.expanduser().resolve() for source in sources]
    if not resolved_sources:
        raise LegacyWorkspaceArchiveError("At least one legacy workspace is required")
    if len(set(resolved_sources)) != len(resolved_sources):
        raise LegacyWorkspaceArchiveError("Legacy workspace sources must be unique")

    results = [
        archive_legacy_workspace(source, archive_root=archive_root, archived_at=archived_at)
        for source in resolved_sources
    ]
    if delete_sources:
        for source, result in zip(resolved_sources, results, strict=True):
            verify_source_matches_archive(source, Path(result["archive_dir"]))
        for source in resolved_sources:
            shutil.rmtree(source)
            if source.exists():
                raise LegacyWorkspaceArchiveError(f"Unable to delete archived legacy workspace: {source}")
    return {
        "status": "archived_and_deleted" if delete_sources else "archived",
        "archive_root": str(archive_root.expanduser().resolve()),
        "archives": results,
        "deleted_sources": [str(source) for source in resolved_sources] if delete_sources else [],
    }
