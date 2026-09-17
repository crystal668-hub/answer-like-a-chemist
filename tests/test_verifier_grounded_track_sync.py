from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from benchmarking.core.records import RecordValidationError, load_records
from benchmarking.runtime.vgb_bridge import (
    VerifierGroundedRuntimeError,
    load_release_config,
)
from scripts.sync_verifier_grounded_tracks import (
    LEGACY_TRACK_DIRECTORIES,
    REFERENCE_PLACEHOLDER,
    RESOURCE_TRACK_ROOT,
    build_track_records,
    migrate_legacy_track_layout,
    prune_runtime_history,
    sync_tracks,
)

FORBIDDEN_KEYS = {
    "example",
    "gold",
    "sample_answer",
    "sample_answers",
    "source_repo",
    "task",
    "verifier_specs",
}


def _description() -> dict[str, Any]:
    config = load_release_config()
    schemas = {
        "rdkit": {
            "format": "final_answer_line",
            "final_answer_prefix": "FINAL ANSWER:",
            "value_type": "smiles",
        },
        "xtb": {
            "format": "final_answer_block",
            "final_answer_prefix": "FINAL ANSWER:",
            "value_type": "xyz",
            "fence_language": "xyz",
        },
        "property_calculation_advanced": {
            "format": "final_answer_line",
            "final_answer_prefix": "FINAL ANSWER:",
            "value_type": "json",
        },
        "property_calculation_basic": {
            "format": "final_answer_line",
            "final_answer_prefix": "FINAL ANSWER:",
            "value_type": "json",
        },
    }
    return {
        "package_version": config.version,
        "tracks": {
            name: {
                "version": config.version,
                "prompts": [
                    {
                        "track": name,
                        "task_id": task_id,
                        "prompt": f"Public prompt for {task_id}. FINAL ANSWER:",
                        "answer_schema": schemas[name],
                    }
                    for task_id in track["task_ids"]
                ],
            }
            for name, track in config.tracks.items()
        },
    }


def _assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        assert FORBIDDEN_KEYS.isdisjoint(value)
        for item in value.values():
            _assert_no_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_keys(item)


def test_build_track_records_exposes_only_public_scoring_identity() -> None:
    config = load_release_config()
    description = _description()

    for track_name, track_config in config.tracks.items():
        rows = build_track_records(
            config=config,
            description=description,
            track_name=track_name,
        )
        assert len(rows) == track_config["task_count"]
        assert [row["id"] for row in rows] == track_config["task_ids"]
        assert all(row["answer"] == REFERENCE_PLACEHOLDER for row in rows)
        assert all(row["verifier_grounded"]["release"] == config.identity for row in rows)
        assert all(row["verifier_grounded"]["track"] == track_name for row in rows)
        assert all("example" not in row["verifier_grounded"]["answer_schema"] for row in rows)
        _assert_no_forbidden_keys(rows)


def test_sync_tracks_writes_tracked_and_runtime_copies(tmp_path: Path) -> None:
    config = load_release_config()
    resource_root = tmp_path / "resources"
    benchmarks_root = tmp_path / "formal-benchmarks"

    written = sync_tracks(
        config=config,
        description=_description(),
        resource_root=resource_root,
        benchmarks_root=benchmarks_root,
    )

    assert len(written) == 8
    for track in config.tracks:
        resource_path = resource_root / f"{track}.jsonl"
        runtime_path = benchmarks_root / track / "data" / f"{track}.jsonl"
        assert resource_path.read_bytes() == runtime_path.read_bytes()


def _write_runtime_manifest(
    root: Path,
    name: str,
    *,
    version: str,
    package: str = "verifier-grounded-benchmark",
) -> Path:
    runtime = root / name
    runtime.mkdir(parents=True)
    (runtime / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "package": package,
                "version": version,
                "wheel_sha256": f"{version.replace('.', ''):0<64}"[:64],
            }
        ),
        encoding="utf-8",
    )
    return runtime


def test_prune_runtime_history_keeps_two_latest_distinct_versions_and_same_version_instances(
    tmp_path: Path,
) -> None:
    config = load_release_config()
    runtime_root = tmp_path / "verifier-grounded-runtimes"
    old = _write_runtime_manifest(runtime_root, "0.8.0-old", version="0.8.0")
    previous = _write_runtime_manifest(runtime_root, "0.9.1-a", version="0.9.1")
    previous_duplicate = _write_runtime_manifest(runtime_root, "0.9.1-b", version="0.9.1")
    latest = _write_runtime_manifest(runtime_root, "0.10.0", version="0.10.0")
    unmanaged = _write_runtime_manifest(
        runtime_root,
        "unmanaged",
        version="0.1.0",
        package="other-package",
    )
    malformed = runtime_root / "malformed"
    malformed.mkdir()
    invalid_hash = runtime_root / "invalid-hash"
    invalid_hash.mkdir()
    (invalid_hash / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "package": config.package,
                "version": "0.1.0",
                "wheel_sha256": "not-a-sha256",
            }
        ),
        encoding="utf-8",
    )
    (runtime_root / "not-a-runtime.txt").write_text("keep", encoding="utf-8")

    result = prune_runtime_history(config=config, runtime_root=runtime_root)

    assert result["kept_versions"] == ["0.10.0", "0.9.1"]
    assert not old.exists()
    assert previous.exists()
    assert previous_duplicate.exists()
    assert latest.exists()
    assert unmanaged.exists()
    assert malformed.exists()
    assert invalid_hash.exists()
    assert (runtime_root / "not-a-runtime.txt").exists()


def test_prune_runtime_history_reports_delete_failures(tmp_path: Path) -> None:
    config = load_release_config()
    runtime_root = tmp_path / "verifier-grounded-runtimes"
    stale = _write_runtime_manifest(runtime_root, "0.8.0", version="0.8.0")
    _write_runtime_manifest(runtime_root, "0.9.1", version="0.9.1")
    _write_runtime_manifest(runtime_root, "0.10.0", version="0.10.0")

    with (
        patch(
            "scripts.sync_verifier_grounded_tracks.shutil.rmtree",
            side_effect=OSError("permission denied"),
        ),
        pytest.raises(VerifierGroundedRuntimeError, match=str(stale)),
    ):
        prune_runtime_history(config=config, runtime_root=runtime_root)


def test_checked_in_tracks_match_pinned_release_inventory() -> None:
    config = load_release_config()
    for track_name, track_config in config.tracks.items():
        path = RESOURCE_TRACK_ROOT / f"{track_name}.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert [row["id"] for row in rows] == track_config["task_ids"]
        assert len(rows) == track_config["task_count"]
        assert all(row["verifier_grounded"]["track"] == track_name for row in rows)
        _assert_no_forbidden_keys(rows)

        records = load_records([path])
        assert len(records) == track_config["task_count"]
        assert all(record.grading.kind == "verifier_grounded" for record in records)


def test_rdkit_chain_distance_prompt_exposes_smarts_and_uff_protocol() -> None:
    path = RESOURCE_TRACK_ROOT / "rdkit.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    record = next(row for row in rows if row["id"] == "rdkit_chain_end_to_end_max_013")

    assert (
        "[C;X4;!R]-[C;X4;!R]-[C;X4;!R]-[C;X4;!R]-[C;X4;!R]-[C;X4;!R]"
        in record["prompt"]
    )
    assert "Universal Force Field (UFF)" in record["prompt"]
    assert "lowest-energy converged UFF conformer" in record["prompt"]


def _write_legacy_layout(root: Path) -> None:
    for track, legacy_name in LEGACY_TRACK_DIRECTORIES.items():
        source = RESOURCE_TRACK_ROOT / f"{track}.jsonl"
        target = root / legacy_name / "data" / f"{legacy_name}.jsonl"
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())


def test_migrate_legacy_layout_validates_all_tracks_before_removal(tmp_path: Path) -> None:
    config = load_release_config()
    _write_legacy_layout(tmp_path)

    result = migrate_legacy_track_layout(config=config, benchmarks_root=tmp_path)

    assert len(result["promoted"]) == 4
    assert len(result["removed"]) == 4
    for track, legacy_name in LEGACY_TRACK_DIRECTORIES.items():
        assert (tmp_path / track / "data" / f"{track}.jsonl").is_file()
        assert not (tmp_path / legacy_name).exists()


def test_migrate_legacy_layout_preserves_all_legacy_dirs_on_validation_failure(
    tmp_path: Path,
) -> None:
    config = load_release_config()
    _write_legacy_layout(tmp_path)
    broken = tmp_path / LEGACY_TRACK_DIRECTORIES["xtb"] / "data" / (
        LEGACY_TRACK_DIRECTORIES["xtb"] + ".jsonl"
    )
    broken.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RecordValidationError):
        migrate_legacy_track_layout(config=config, benchmarks_root=tmp_path)

    assert all((tmp_path / legacy_name).is_dir() for legacy_name in LEGACY_TRACK_DIRECTORIES.values())


def test_migrate_legacy_layout_rejects_incomplete_destination_without_cleanup(
    tmp_path: Path,
) -> None:
    config = load_release_config()
    _write_legacy_layout(tmp_path)
    (tmp_path / "rdkit").mkdir()

    with pytest.raises(VerifierGroundedRuntimeError, match="destination exists but is incomplete"):
        migrate_legacy_track_layout(config=config, benchmarks_root=tmp_path)

    assert all((tmp_path / legacy_name).is_dir() for legacy_name in LEGACY_TRACK_DIRECTORIES.values())
