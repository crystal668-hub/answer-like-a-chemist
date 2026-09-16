from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from benchmarking.runtime.vgb_worker import VerifierWorker

from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime.observability import increment, measure

DEFAULT_RELEASE_CONFIG = (
    runtime_paths.project_root
    / "benchmarking"
    / "resources"
    / "verifier_grounded"
    / "release.json"
)
RUNTIME_API_BODY = r"""
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
elif action == "reference_answers":
    track = vgb.load_track(request["track"])
    reference_answers = []
    for task_id in request["task_ids"]:
        task = track.task(task_id, include_gold=True)
        gold_answers = task.get("gold_answers")
        if not isinstance(gold_answers, list):
            raise ValueError(f"Task is missing public gold answers: {task_id}")
        answers = [
            {key: value for key, value in answer.items() if key != "scoring_profile"}
            for answer in gold_answers
        ]
        if len(answers) == 1:
            answer = {"answer": answers[0].pop("value")}
            answer.update(answers[0])
        else:
            answer = {"answers": answers}
        reference_answers.append({"task_id": task_id, **answer})
    result = {"reference_answers": reference_answers}
else:
    raise ValueError(f"Unsupported verifier runtime action: {action}")
""".strip()
RUNTIME_API_SCRIPT = RUNTIME_API_BODY + "\nprint(json.dumps(result, ensure_ascii=False))"


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


class InvocationValidationCache:
    """Invocation-owned cache for successful immutable runtime validation."""

    def __init__(self) -> None:
        self._entries: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.hit_count = 0
        self.miss_count = 0
        self.failure_count = 0

    def validate(self, config: ReleaseConfig) -> dict[str, Any]:
        key = _validation_fingerprint(config)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self.hit_count += 1
                increment("vgb_validation_cache_hit_count")
                return dict(cached)
            self.miss_count += 1
            increment("vgb_validation_cache_miss_count")
            try:
                manifest = _validate_runtime_files_uncached(config)
            except Exception:
                self.failure_count += 1
                increment("vgb_validation_failure_count")
                raise
            self._entries[key] = manifest
        increment("vgb_validation_count")
        return dict(manifest)

    def to_meta(self) -> dict[str, int]:
        return {"hit_count": self.hit_count, "miss_count": self.miss_count, "failure_count": self.failure_count}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


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
    validation_cache: InvocationValidationCache | None = None,
) -> dict[str, Any]:
    return _invoke_api(
        config,
        {"action": "describe", "tracks": list(config.tracks)},
        timeout=180.0,
        require_manifest=require_manifest,
        validation_cache=validation_cache,
    )


def load_public_reference_answers(
    track: str,
    *,
    release_config: ReleaseConfig | None = None,
    validation_cache: InvocationValidationCache | None = None,
) -> list[dict[str, Any]]:
    config = release_config or load_release_config()
    track_config = config.tracks.get(track)
    if track_config is None:
        raise VerifierGroundedRuntimeError(f"Unknown pinned verifier track: {track}")
    task_ids = track_config.get("task_ids")
    if not isinstance(task_ids, list):
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier task inventory is invalid for track {track!r}"
        )
    result = _invoke_api(
        config,
        {"action": "reference_answers", "track": track, "task_ids": task_ids},
        timeout=180.0,
        require_manifest=True,
        validation_cache=validation_cache,
    )
    answers = result.get("reference_answers")
    if not isinstance(answers, list) or not all(isinstance(item, dict) for item in answers):
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier reference-answer inventory is invalid for track {track!r}"
        )
    actual_task_ids = [str(item.get("task_id") or "") for item in answers]
    if not isinstance(task_ids, list) or actual_task_ids != task_ids:
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier reference-answer inventory does not match track {track!r}"
        )
    return [dict(item) for item in answers]


def evaluate_answer(
    *,
    track: str,
    task_id: str,
    answer_text: str,
    release_identity: dict[str, Any],
    release_config: ReleaseConfig | None = None,
    validation_cache: InvocationValidationCache | None = None,
    worker: VerifierWorker | None = None,
) -> dict[str, Any]:
    config = release_config or load_release_config()
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
    payload = {"action": "evaluate_one", "track": track, "task_id": task_id, "answer_text": answer_text}
    timeout = float(track_config.get("timeout_seconds") or 120.0)
    if worker is None:
        result = _invoke_api(config, payload, timeout=timeout, require_manifest=True,
                             validation_cache=validation_cache)
    else:
        result = worker.invoke(config, payload, timeout=timeout)
    if not isinstance(result, dict):
        raise VerifierGroundedRuntimeError("Pinned verifier runtime returned a non-object result")
    return result


def validate_runtime_files(config: ReleaseConfig, *, validation_cache: InvocationValidationCache | None = None) -> dict[str, Any]:
    if validation_cache is not None:
        return validation_cache.validate(config)
    return _validate_runtime_files_uncached(config)


def _validate_runtime_files_uncached(config: ReleaseConfig) -> dict[str, Any]:
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
    validation_cache: InvocationValidationCache | None = None,
) -> dict[str, Any]:
    if require_manifest:
        validate_runtime_files(config, validation_cache=validation_cache)
    if not config.runtime_python.is_file():
        raise VerifierGroundedRuntimeError(
            f"Pinned verifier runtime Python is missing: {config.runtime_python}"
        )
    try:
        action = str(payload.get("action") or "unknown")
        increment("vgb_process_count")
        increment(f"vgb_process_count.{action}")
        with measure("vgb_process"):
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
        increment("vgb_process_failure_count")
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


def _validation_fingerprint(config: ReleaseConfig) -> tuple[Any, ...]:
    def stat_fingerprint(path: Path) -> tuple[Any, ...] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns)

    try:
        manifest_digest = sha256_file(config.runtime_manifest)
    except OSError:
        manifest_digest = None
    return (
        tuple(sorted({
            **config.identity,
            "source_commit": config.source_commit,
            "source_tag": config.source_tag,
            "wheel_filename": config.wheel_filename,
            "wheel_size": config.wheel_size,
            "wheel_path": str(config.wheel_path),
        }.items())),
        stat_fingerprint(config.wheel_path),
        manifest_digest,
        stat_fingerprint(config.runtime_python),
    )


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
