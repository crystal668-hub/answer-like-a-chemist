#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime.vgb_bridge import (
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
RESOURCE_DATASET_ROOT = (
    ROOT / "benchmarking" / "resources" / "verifier_grounded" / "datasets"
)
VERIFIER_RUNTIME_ROOT = runtime_paths.project_state_root / "verifier-grounded-runtimes"
RUNTIME_HISTORY_VERSIONS = 2


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


def build_dataset_records(
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


def sync_datasets(
    *,
    config: ReleaseConfig,
    description: dict[str, Any],
    resource_root: Path = RESOURCE_DATASET_ROOT,
    benchmarks_root: Path = runtime_paths.benchmarks_root,
) -> list[Path]:
    written: list[Path] = []
    for track_name, track_config in config.tracks.items():
        dataset = str(track_config["dataset"])
        records = build_dataset_records(
            config=config,
            description=description,
            track_name=track_name,
        )
        content = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        )
        resource_path = resource_root / f"{dataset}.jsonl"
        runtime_path = benchmarks_root / dataset / "data" / f"{dataset}.jsonl"
        for path in (resource_path, runtime_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
    return written


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
        description="Install a pinned verifier wheel and sync sanitized OpenClaw datasets."
    )
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--release-config", type=Path)
    parser.add_argument("--resource-root", type=Path, default=RESOURCE_DATASET_ROOT)
    parser.add_argument("--benchmarks-root", type=Path, default=runtime_paths.benchmarks_root)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_release_config(args.release_config) if args.release_config else load_release_config()
    description = install_runtime(config=config, source_wheel=args.wheel)
    paths = sync_datasets(
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
                "runtime_cleanup": cleanup,
            }
        )
    )


if __name__ == "__main__":
    main()
