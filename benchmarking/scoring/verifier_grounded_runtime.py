from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import runtime_paths


DEFAULT_RELEASE_CONFIG = (
    runtime_paths.project_root
    / "benchmarking"
    / "resources"
    / "verifier_grounded"
    / "release.json"
)
RUNTIME_API_SCRIPT = r"""
import importlib.metadata
import json
import sys

import verifier_grounded_benchmark as vgb

request = json.load(sys.stdin)
action = request["action"]
if action == "describe":
    tracks = {}
    for name in request["tracks"]:
        track = vgb.load_track(name)
        tracks[name] = {
            "version": track.definition.version,
            "prompts": track.prompts(),
        }
    result = {
        "package_version": importlib.metadata.version("verifier-grounded-benchmark"),
        "tracks": tracks,
    }
elif action == "evaluate_one":
    track = vgb.load_track(request["track"])
    result = track.evaluate_one({
        "task_id": request["task_id"],
        "response": request["answer_text"],
    })
else:
    raise ValueError(f"Unsupported verifier runtime action: {action}")
print(json.dumps(result, ensure_ascii=False))
""".strip()


class VerifierGroundedRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseConfig:
    package: str
    version: str
    source_commit: str
    source_tag: str
    wheel_filename: str
    wheel_sha256: str
    wheel_size: int
    tracks: dict[str, dict[str, Any]]

    @property
    def wheel_path(self) -> Path:
        return (
            runtime_paths.data_root
            / "verifier-grounded-releases"
            / self.version
            / self.wheel_filename
        )

    @property
    def runtime_root(self) -> Path:
        return (
            runtime_paths.project_state_root
            / "verifier-grounded-runtimes"
            / f"{self.version}-{self.wheel_sha256[:12]}"
        )

    @property
    def runtime_python(self) -> Path:
        return self.runtime_root / ".venv" / "bin" / "python"

    @property
    def runtime_manifest(self) -> Path:
        return self.runtime_root / "runtime-manifest.json"

    @property
    def identity(self) -> dict[str, str]:
        return {
            "package": self.package,
            "version": self.version,
            "wheel_sha256": self.wheel_sha256,
        }


def load_release_config(path: Path = DEFAULT_RELEASE_CONFIG) -> ReleaseConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifierGroundedRuntimeError(f"Unable to load verifier release config: {path}") from exc
    wheel = payload.get("wheel")
    tracks = payload.get("tracks")
    if not isinstance(wheel, dict) or not isinstance(tracks, dict):
        raise VerifierGroundedRuntimeError("Verifier release config is missing wheel or tracks")
    config = ReleaseConfig(
        package=str(payload.get("package") or ""),
        version=str(payload.get("version") or ""),
        source_commit=str(payload.get("source_commit") or ""),
        source_tag=str(payload.get("source_tag") or ""),
        wheel_filename=str(wheel.get("filename") or ""),
        wheel_sha256=str(wheel.get("sha256") or ""),
        wheel_size=int(wheel.get("size") or 0),
        tracks={str(name): dict(value) for name, value in tracks.items() if isinstance(value, dict)},
    )
    if not all(
        (
            config.package,
            config.version,
            config.source_commit,
            config.source_tag,
            config.wheel_filename,
            config.wheel_sha256,
        )
    ):
        raise VerifierGroundedRuntimeError("Verifier release config has empty identity fields")
    return config


def describe_installed_release(
    config: ReleaseConfig,
    *,
    require_manifest: bool = True,
) -> dict[str, Any]:
    return _invoke_api(
        config,
        {"action": "describe", "tracks": list(config.tracks)},
        timeout=180.0,
        require_manifest=require_manifest,
    )


def evaluate_answer(
    *,
    track: str,
    task_id: str,
    answer_text: str,
    release_identity: dict[str, Any],
) -> dict[str, Any]:
    config = load_release_config()
    if release_identity != config.identity:
        raise VerifierGroundedRuntimeError(
            "Benchmark record release identity does not match the pinned verifier release"
        )
    track_config = config.tracks.get(track)
    if track_config is None:
        raise VerifierGroundedRuntimeError(f"Unknown pinned verifier track: {track}")
    task_ids = track_config.get("task_ids")
    if not isinstance(task_ids, list) or task_id not in task_ids:
        raise VerifierGroundedRuntimeError(
            f"Task {task_id!r} is not part of pinned verifier track {track!r}"
        )
    result = _invoke_api(
        config,
        {
            "action": "evaluate_one",
            "track": track,
            "task_id": task_id,
            "answer_text": answer_text,
        },
        timeout=float(track_config.get("timeout_seconds") or 120.0),
        require_manifest=True,
    )
    if not isinstance(result, dict):
        raise VerifierGroundedRuntimeError("Pinned verifier runtime returned a non-object result")
    return result


def validate_runtime_files(config: ReleaseConfig) -> dict[str, Any]:
    if not config.wheel_path.is_file():
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier wheel is missing: {config.wheel_path}"
        )
    if config.wheel_path.stat().st_size != config.wheel_size:
        raise VerifierGroundedRuntimeError("Pinned verifier wheel size does not match release config")
    if sha256_file(config.wheel_path) != config.wheel_sha256:
        raise VerifierGroundedRuntimeError("Pinned verifier wheel SHA256 does not match release config")
    if not config.runtime_python.is_file():
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier runtime is missing: {config.runtime_python}"
        )
    try:
        manifest = json.loads(config.runtime_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier runtime manifest is missing or invalid: {config.runtime_manifest}"
        ) from exc
    expected = {
        **config.identity,
        "source_commit": config.source_commit,
        "source_tag": config.source_tag,
        "wheel_path": str(config.wheel_path),
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise VerifierGroundedRuntimeError("Pinned verifier runtime manifest does not match release config")
    return manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _invoke_api(
    config: ReleaseConfig,
    payload: dict[str, Any],
    *,
    timeout: float,
    require_manifest: bool,
) -> dict[str, Any]:
    if require_manifest:
        validate_runtime_files(config)
    if not config.runtime_python.is_file():
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier runtime Python is missing: {config.runtime_python}"
        )
    try:
        completed = subprocess.run(
            [str(config.runtime_python), "-I", "-c", RUNTIME_API_SCRIPT],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            cwd=config.runtime_root,
            env=_runtime_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerifierGroundedRuntimeError(f"Pinned verifier runtime failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or str(completed.returncode)
        raise VerifierGroundedRuntimeError(f"Pinned verifier runtime failed: {detail}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier runtime produced invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(result, dict):
        raise VerifierGroundedRuntimeError("Pinned verifier runtime produced a non-object result")
    return result


def _runtime_env() -> dict[str, str]:
    allowed = {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "OMP_NUM_THREADS",
        "PATH",
        "TMPDIR",
        "XTBHOME",
        "XTBPATH",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["PYTHONNOUSERSITE"] = "1"
    return env
