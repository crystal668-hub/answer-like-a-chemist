#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarking.runtime import paths as runtime_paths  # noqa: E402
from benchmarking.runtime.vgb_bridge import (  # noqa: E402
    ReleaseConfig,
    VerifierGroundedRuntimeError,
    describe_installed_release,
    load_release_config,
    sha256_file,
    validate_runtime_files,
)

REFERENCE_PLACEHOLDER = "No reference answer is exposed; score with the pinned verifier release."
PUBLIC_ANSWER_SCHEMA_KEYS = {
    "cardinality",
    "fence_language",
    "final_answer_prefix",
    "format",
    "value_type",
}
RESOURCE_TRACK_ROOT = (
    ROOT / "benchmarking" / "resources" / "verifier_grounded" / "tracks"
)
VERIFIER_RUNTIME_ROOT = runtime_paths.project_state_root / "verifier-grounded-runtimes"
RUNTIME_HISTORY_VERSIONS = 2
LEGACY_TRACK_DIRECTORIES = {
    "rdkit": "verifier_grounded_rdkit",
    "xtb": "verifier_grounded_xtb_xyz",
    "property_calculation_advanced": "verifier_grounded_property_calculation",
    "property_calculation_basic": "verifier_grounded_property_calculation_easy",
}


def install_runtime(*, config: ReleaseConfig, source_wheel: Path) -> dict[str, Any]:
    source_wheel = source_wheel.expanduser().resolve()
    if not source_wheel.is_file():
        raise FileNotFoundError(f"Verifier wheel does not exist: {source_wheel}")
    if source_wheel.stat().st_size != config.wheel_size:
        raise VerifierGroundedRuntimeError("Verifier wheel size does not match release config")
    if sha256_file(source_wheel) != config.wheel_sha256:
        raise VerifierGroundedRuntimeError("Verifier wheel SHA256 does not match release config")

    config.wheel_path.parent.mkdir(parents=True, exist_ok=True)
    config.wheel_path.parent.chmod(0o700)
    if source_wheel != config.wheel_path:
        shutil.copy2(source_wheel, config.wheel_path)
    config.wheel_path.chmod(0o600)

    config.runtime_root.mkdir(parents=True, exist_ok=True)
    config.runtime_root.chmod(0o700)
    if not config.runtime_python.is_file():
        subprocess.run(
            ["uv", "venv", str(config.runtime_python.parents[1]), "--python", "3.12"],
            cwd=ROOT,
            check=True,
        )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(config.runtime_python),
            "--reinstall-package",
            config.package,
            str(config.wheel_path),
        ],
        cwd=config.runtime_root,
        check=True,
    )

    description = describe_installed_release(config, require_manifest=False)
    _validate_description(config, description)
    manifest = {
        "schema_version": 1,
        **config.identity,
        "source_commit": config.source_commit,
        "source_tag": config.source_tag,
        "wheel_path": str(config.wheel_path),
        "runtime_python": str(config.runtime_python),
        "tracks": {
            name: {
                "task_count": len(track["prompts"]),
                "task_ids": [prompt["task_id"] for prompt in track["prompts"]],
            }
            for name, track in description["tracks"].items()
        },
    }
    config.runtime_manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config.runtime_manifest.chmod(0o600)
    validate_runtime_files(config)
    return description


def prune_runtime_history(
    *,
    config: ReleaseConfig,
    runtime_root: Path = VERIFIER_RUNTIME_ROOT,
    keep_versions: int = RUNTIME_HISTORY_VERSIONS,
) -> dict[str, list[str]]:
    """Keep all managed runtime instances for the newest distinct versions."""
    if keep_versions < 1:
        raise ValueError("keep_versions must be at least 1")

    root = runtime_root.expanduser().resolve()
    if not root.is_dir():
        return {"kept_versions": [], "removed": []}

    managed: list[tuple[Path, Version]] = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir():
            continue
        manifest_path = path / "runtime-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("package") != config.package:
            continue
        version_text = manifest.get("version")
        wheel_sha256 = manifest.get("wheel_sha256")
        if (
            not isinstance(version_text, str)
            or not version_text
            or not isinstance(wheel_sha256, str)
            or len(wheel_sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in wheel_sha256)
        ):
            continue
        try:
            version = Version(version_text)
        except InvalidVersion:
            continue
        managed.append((path, version))

    managed.sort(key=lambda item: str(item[0]))
    versions = sorted({version for _, version in managed}, reverse=True)
    kept_versions = versions[:keep_versions]
    kept_version_set = set(kept_versions)
    removed: list[str] = []
    failures: list[str] = []
    for path, version in managed:
        if version in kept_version_set:
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            failures.append(f"{path}: {exc}")
        else:
            removed.append(str(path))

    if failures:
        detail = "; ".join(failures)
        raise VerifierGroundedRuntimeError(
            f"Failed to prune verifier runtime history: {detail}"
        )
    return {
        "kept_versions": [str(version) for version in kept_versions],
        "removed": removed,
    }


def build_track_records(
    *,
    config: ReleaseConfig,
    description: dict[str, Any],
    track_name: str,
) -> list[dict[str, Any]]:
    track_config = config.tracks[track_name]
    described_track = description["tracks"][track_name]
    records: list[dict[str, Any]] = []
    for prompt in described_track["prompts"]:
        task_id = str(prompt["task_id"])
        answer_schema = prompt.get("answer_schema")
        if not isinstance(answer_schema, dict):
            raise VerifierGroundedRuntimeError(
                f"Pinned prompt is missing answer_schema: {track_name}/{task_id}"
            )
        public_answer_schema = {
            key: value
            for key, value in answer_schema.items()
            if key in PUBLIC_ANSWER_SCHEMA_KEYS
        }
        records.append(
            {
                "answer": REFERENCE_PLACEHOLDER,
                "eval_kind": "verifier_grounded",
                "id": task_id,
                "prompt": str(prompt["prompt"]).strip(),
                "verifier_grounded": {
                    "answer_schema": public_answer_schema,
                    "release": config.identity,
                    "task_id": task_id,
                    "timeout_seconds": float(track_config["timeout_seconds"]),
                    "track": track_name,
                },
            }
        )
    return records


def sync_tracks(
    *,
    config: ReleaseConfig,
    description: dict[str, Any],
    resource_root: Path = RESOURCE_TRACK_ROOT,
    benchmarks_root: Path = runtime_paths.benchmarks_root,
) -> list[Path]:
    written: list[Path] = []
    for track_name in config.tracks:
        records = build_track_records(
            config=config,
            description=description,
            track_name=track_name,
        )
        content = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        )
        resource_path = resource_root / f"{track_name}.jsonl"
        runtime_path = benchmarks_root / track_name / "data" / f"{track_name}.jsonl"
        for path in (resource_path, runtime_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
    return written


def _validate_track_snapshot(path: Path, *, config: ReleaseConfig, track: str) -> None:
    from benchmarking.core.records import load_records
    from benchmarking.scoring.evaluators.verifier_grounded import (
        validate_verifier_grounded_release,
    )

    records = load_records([path])
    expected = list(config.tracks[track]["task_ids"])
    if [record.record_id for record in records] != expected:
        raise VerifierGroundedRuntimeError(
            f"Track snapshot task inventory does not match pinned release: {track}"
        )
    for record in records:
        validate_verifier_grounded_release(record, release_config=config)


def migrate_legacy_track_layout(
    *,
    config: ReleaseConfig,
    benchmarks_root: Path = runtime_paths.benchmarks_root,
    delete_legacy: bool = True,
) -> dict[str, list[str]]:
    """Stage and validate all canonical track directories before legacy cleanup."""
    root = benchmarks_root.expanduser().resolve()
    staging_root = root / f".track-layout-migration-{uuid.uuid4().hex}"
    staged: list[tuple[Path, Path]] = []
    promoted: list[str] = []
    removed: list[str] = []
    try:
        for track in config.tracks:
            target_dir = root / track
            target_file = target_dir / "data" / f"{track}.jsonl"
            if target_file.is_file():
                _validate_track_snapshot(target_file, config=config, track=track)
                continue
            if target_dir.exists():
                raise VerifierGroundedRuntimeError(
                    f"Canonical track destination exists but is incomplete: {target_dir}"
                )
            legacy_name = LEGACY_TRACK_DIRECTORIES.get(track)
            if not legacy_name:
                raise VerifierGroundedRuntimeError(f"No legacy layout mapping for track: {track}")
            source = root / legacy_name / "data" / f"{legacy_name}.jsonl"
            if not source.is_file():
                raise VerifierGroundedRuntimeError(f"Legacy track snapshot does not exist: {source}")
            staged_dir = staging_root / track
            staged_file = staged_dir / "data" / f"{track}.jsonl"
            staged_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, staged_file)
            _validate_track_snapshot(staged_file, config=config, track=track)
            staged.append((staged_dir, target_dir))

        for staged_dir, target_dir in staged:
            os.replace(staged_dir, target_dir)
            promoted.append(str(target_dir))

        for track in config.tracks:
            _validate_track_snapshot(
                root / track / "data" / f"{track}.jsonl",
                config=config,
                track=track,
            )

        if delete_legacy:
            for legacy_name in LEGACY_TRACK_DIRECTORIES.values():
                legacy_dir = root / legacy_name
                if legacy_dir.is_dir():
                    shutil.rmtree(legacy_dir)
                    removed.append(str(legacy_dir))
        return {"promoted": promoted, "removed": removed}
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)


def _validate_description(config: ReleaseConfig, description: dict[str, Any]) -> None:
    if description.get("package_version") != config.version:
        raise VerifierGroundedRuntimeError("Installed verifier package version is not pinned version")
    described_tracks = description.get("tracks")
    if not isinstance(described_tracks, dict):
        raise VerifierGroundedRuntimeError("Installed verifier description is missing tracks")
    for name, track_config in config.tracks.items():
        track = described_tracks.get(name)
        prompts = track.get("prompts") if isinstance(track, dict) else None
        if not isinstance(prompts, list) or track.get("version") != config.version:
            raise VerifierGroundedRuntimeError(f"Installed verifier track is invalid: {name}")
        task_ids = [prompt.get("task_id") for prompt in prompts if isinstance(prompt, dict)]
        if task_ids != track_config.get("task_ids"):
            raise VerifierGroundedRuntimeError(
                f"Installed verifier task inventory does not match release config: {name}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install a pinned verifier wheel and sync sanitized OpenClaw tracks."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--wheel", type=Path)
    mode.add_argument("--migrate-layout-only", action="store_true")
    parser.add_argument("--release-config", type=Path)
    parser.add_argument("--resource-root", type=Path, default=RESOURCE_TRACK_ROOT)
    parser.add_argument("--benchmarks-root", type=Path, default=runtime_paths.benchmarks_root)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_release_config(args.release_config) if args.release_config else load_release_config()
    if args.migrate_layout_only:
        print(json.dumps(migrate_legacy_track_layout(
            config=config,
            benchmarks_root=args.benchmarks_root,
        )))
        return
    description = install_runtime(config=config, source_wheel=args.wheel)
    paths = sync_tracks(
        config=config,
        description=description,
        resource_root=args.resource_root.expanduser().resolve(),
        benchmarks_root=args.benchmarks_root.expanduser().resolve(),
    )
    cleanup = prune_runtime_history(config=config)
    print(
        json.dumps(
            {
                "runtime": str(config.runtime_root),
                "written": [str(path) for path in paths],
                "layout_migration": migrate_legacy_track_layout(
                    config=config,
                    benchmarks_root=args.benchmarks_root,
                ),
                "runtime_cleanup": cleanup,
            }
        )
    )


if __name__ == "__main__":
    main()
