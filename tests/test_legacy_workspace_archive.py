from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarking.runtime.legacy_workspace_archive import (
    LEGACY_ARCHIVE_KIND,
    LegacyWorkspaceArchiveError,
    archive_legacy_workspace,
    archive_legacy_workspaces,
    verify_legacy_workspace_archive,
    verify_source_matches_archive,
)

ARCHIVED_AT = datetime(2026, 7, 27, 1, 2, 3, tzinfo=UTC)


def make_workspace(root: Path, name: str) -> Path:
    workspace = root / name
    (workspace / ".git" / "objects").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (workspace / "scratch" / "record" / "session" / "outputs").mkdir(parents=True)
    (workspace / "scratch" / "record" / "session" / "outputs" / "result.xyz").write_bytes(b"3\nresult\n")
    (workspace / "empty").mkdir()
    return workspace


def test_archive_preserves_complete_workspace_and_verifies_inventory(tmp_path: Path) -> None:
    source = make_workspace(tmp_path, "benchmark-single-skills-on")
    archive_root = tmp_path / "archives"

    result = archive_legacy_workspace(source, archive_root=archive_root, archived_at=ARCHIVED_AT)

    archive_dir = Path(result["archive_dir"])
    assert archive_dir.name == "benchmark-single-skills-on-20260727T010203Z"
    assert source.is_dir()
    assert (archive_dir / "workspace" / ".git" / "HEAD").read_bytes() == (source / ".git" / "HEAD").read_bytes()
    assert (archive_dir / "workspace" / "empty").is_dir()
    manifest = json.loads((archive_dir / "legacy-archive-manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == LEGACY_ARCHIVE_KIND
    assert manifest["contains_git_metadata"] is True
    assert verify_legacy_workspace_archive(archive_dir)["status"] == "verified"
    assert verify_source_matches_archive(source, archive_dir)["status"] == "source_matches_archive"


def test_archive_verification_detects_changed_copy_and_source(tmp_path: Path) -> None:
    source = make_workspace(tmp_path, "benchmark-single-skills-off")
    result = archive_legacy_workspace(source, archive_root=tmp_path / "archives", archived_at=ARCHIVED_AT)
    archive_dir = Path(result["archive_dir"])
    archived_file = archive_dir / "workspace" / "scratch" / "record" / "session" / "outputs" / "result.xyz"
    archived_file.write_text("changed\n", encoding="utf-8")

    with pytest.raises(LegacyWorkspaceArchiveError, match="inventory"):
        verify_legacy_workspace_archive(archive_dir)

    other_source = make_workspace(tmp_path, "other-workspace")
    other_result = archive_legacy_workspace(
        other_source,
        archive_root=tmp_path / "other-archives",
        archived_at=ARCHIVED_AT,
    )
    other_source.joinpath("new.txt").write_text("late change\n", encoding="utf-8")
    with pytest.raises(LegacyWorkspaceArchiveError, match="changed after"):
        verify_source_matches_archive(other_source, Path(other_result["archive_dir"]))


def test_multi_workspace_delete_happens_only_after_every_source_verifies(tmp_path: Path) -> None:
    first = make_workspace(tmp_path, "benchmark-single-skills-on")
    second = make_workspace(tmp_path, "benchmark-single-skills-off")

    report = archive_legacy_workspaces(
        [first, second],
        archive_root=tmp_path / "archives",
        delete_sources=True,
        archived_at=ARCHIVED_AT,
    )

    assert report["status"] == "archived_and_deleted"
    assert not first.exists()
    assert not second.exists()
    assert len(report["archives"]) == 2
    for archive in report["archives"]:
        assert verify_legacy_workspace_archive(Path(archive["archive_dir"]))["status"] == "verified"


def test_legacy_archive_script_is_directly_executable() -> None:
    project_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "archive_legacy_benchmark_workspaces.py"), "--help"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Archive complete legacy benchmark workspaces" in completed.stdout
