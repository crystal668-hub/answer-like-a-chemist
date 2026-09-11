"""Best-effort evidence persistence and runner-owned environment cleanup."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any


def write_evidence(path: Path, payload: Any) -> None:
    for parent in (path.parent, path.parent.parent):
        if parent.is_symlink():
            raise OSError(f"evidence parent is a symlink: {parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_evidence(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or any(
            parent.is_symlink() for parent in (path.parent, path.parent.parent)
        ):
            raise ValueError("evidence is a symlink")
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("evidence must be an object")
        return payload
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def cleanup_owned_environment(scratch: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"status": "complete", "paths": [], "errors": []}
    for relative in ("venv", ".uv-cache", ".runtime-bin", "tmp/cache/uv"):
        path = scratch / relative
        try:
            for parent in (scratch, *path.relative_to(scratch).parents):
                checked = parent if parent == scratch else scratch / parent
                if checked.is_symlink():
                    raise ValueError(f"cleanup parent is a symlink: {checked}")
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            report["paths"].append(relative)
        except (OSError, ValueError) as exc:
            report["errors"].append({"path": relative, "error": str(exc)})
    if report["errors"]:
        report["status"] = "failed"
    report.update(
        venv_removed="venv" in report["paths"],
        cache_removed=all(p in report["paths"] for p in (".uv-cache", "tmp/cache/uv")),
        tool_bin_removed=".runtime-bin" in report["paths"],
    )
    return report


def register_environment(scratch: Path, identity: dict[str, Any], backend: str) -> None:
    write_evidence(
        scratch / "notes/environment-owner.json",
        {
            "schema_version": 1,
            "identity": identity,
            "backend": backend,
            "paths": ["venv", ".uv-cache", ".runtime-bin", "tmp/cache/uv"],
        },
    )
