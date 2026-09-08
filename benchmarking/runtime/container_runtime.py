"""Docker lifecycle helpers for isolated benchmark attempts.

The adapter intentionally shells out to the Docker CLI.  This keeps the
orchestrator dependency-free while still making every container operation
injectable and testable.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from benchmarking.runtime.agent_workspace import AttemptIdentity
from benchmarking.runtime.cancellation import CancellationToken


class ContainerRuntimeError(RuntimeError):
    def __init__(self, message: str, *, code: str = "container_runtime_error", details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = {"code": code, "layer": "benchmark_runtime", "source": "docker", **dict(details or {})}


@dataclass(frozen=True)
class ContainerMount:
    source: Path
    target: PurePosixPath
    mode: Literal["ro", "rw"]
    kind: Literal["workspace", "input", "config", "session", "spool", "skills", "skill_runner"]


@dataclass(frozen=True)
class ContainerAttemptSpec:
    identity: AttemptIdentity
    image: str
    command: tuple[str, ...]
    environment: Mapping[str, str] = field(default_factory=dict)
    mounts: tuple[ContainerMount, ...] = ()
    network_mode: str = "host"
    cpu_limit: float | None = None
    memory_limit_bytes: int | None = None
    pids_limit: int | None = None
    timeout_seconds: float | None = None
    stop_grace_seconds: float = 10.0
    labels: Mapping[str, str] = field(default_factory=dict)
    allowed_source_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ContainerAttemptHandle:
    container_id: str
    container_name: str
    identity: AttemptIdentity
    image_digest: str
    started_at: str = ""
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerAttemptResult:
    handle: ContainerAttemptHandle
    return_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False
    oom_killed: bool = False
    inspect: Mapping[str, Any] = field(default_factory=dict)
    stats: Mapping[str, Any] = field(default_factory=dict)
    cleanup: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CleanupReport:
    removed: bool
    container_id: str
    error: str = ""


class CommandRunner(Protocol):
    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]: ...


def _identity_labels(identity: AttemptIdentity) -> dict[str, str]:
    return {f"benchmark.{key}": str(value) for key, value in identity.sentinel_fields().items()}


class DockerContainerRuntime:
    def __init__(self, *, docker_executable: str | None = None, run_subprocess: CommandRunner = subprocess.run) -> None:
        self.docker = docker_executable or shutil.which("docker")
        self._run = run_subprocess

    def _require_docker(self) -> str:
        if not self.docker:
            raise ContainerRuntimeError("docker executable not found", code="docker_unavailable")
        return self.docker

    def _command(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        try:
            result = self._run([self._require_docker(), *args], text=True, capture_output=True, check=False, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise ContainerRuntimeError(str(exc), code="docker_command_timeout") from exc
        except OSError as exc:
            raise ContainerRuntimeError(str(exc), code="docker_command_failed") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise ContainerRuntimeError(detail[:2000] or f"docker exited {result.returncode}", details={"returncode": result.returncode})
        return result

    def check_ready(self) -> dict[str, Any]:
        result = self._command(["version", "--format", "{{json .}}"], timeout=15)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ContainerRuntimeError("docker version returned invalid JSON", code="docker_invalid_response") from exc
        return payload if isinstance(payload, dict) else {"version": payload}

    @staticmethod
    def _validate_mounts(mounts: tuple[ContainerMount, ...], allowed_source_roots: tuple[Path, ...] = ()) -> None:
        allowed = {
            PurePosixPath("/benchmark/workspace"),
            PurePosixPath("/benchmark/input"),
            PurePosixPath("/benchmark/config"),
            PurePosixPath("/benchmark/session"),
            PurePosixPath("/benchmark/result-spool"),
            PurePosixPath("/opt/benchmark/skills"),
            PurePosixPath("/opt/benchmark/scripts/run_skill.py"),
        }
        seen: set[PurePosixPath] = set()
        for mount in mounts:
            target_allowed = mount.target in allowed
            if mount.target.as_posix().startswith("/opt/benchmark/skills/"):
                target_allowed = True
            if mount.target.as_posix().startswith("/benchmark/config/") or mount.target.as_posix().startswith("/benchmark/session/"):
                target_allowed = True
            if not target_allowed or mount.target in seen:
                raise ContainerRuntimeError(f"invalid container mount target: {mount.target}", code="container_mount_invalid")
            if mount.kind in {"input", "config"} and mount.mode != "ro":
                raise ContainerRuntimeError(f"mount must be read-only: {mount.target}", code="container_mount_invalid")
            if mount.source.is_symlink() or not mount.source.exists():
                raise ContainerRuntimeError(f"mount source is missing or symlink: {mount.source}", code="container_mount_invalid")
            source = mount.source.resolve()
            if allowed_source_roots and not any(source == root.resolve() or root.resolve() in source.parents for root in allowed_source_roots):
                raise ContainerRuntimeError(f"mount source is outside allowed roots: {source}", code="container_mount_invalid")
            if stat.S_ISSOCK(mount.source.stat().st_mode) or stat.S_ISCHR(mount.source.stat().st_mode) or stat.S_ISBLK(mount.source.stat().st_mode):
                raise ContainerRuntimeError(f"special mount source is forbidden: {source}", code="container_mount_invalid")
            seen.add(mount.target)

    def create(self, spec: ContainerAttemptSpec) -> ContainerAttemptHandle:
        self._validate_mounts(spec.mounts, spec.allowed_source_roots)
        labels = {**_identity_labels(spec.identity), **{str(k): str(v) for k, v in spec.labels.items()}}
        name = "benchmark-" + "-".join((spec.identity.run_id, spec.identity.record_id, str(spec.identity.attempt_index), spec.identity.session_id))[:180]
        args = ["create", "--name", name, "--network", spec.network_mode, "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m"]
        if spec.cpu_limit is not None:
            args += ["--cpus", str(spec.cpu_limit)]
        if spec.memory_limit_bytes is not None:
            args += ["--memory", str(spec.memory_limit_bytes)]
        if spec.pids_limit is not None:
            args += ["--pids-limit", str(spec.pids_limit)]
        for key, value in {**labels}.items():
            args += ["--label", f"{key}={value}"]
        for key, value in spec.environment.items():
            args += ["--env", f"{key}={value}"]
        for mount in spec.mounts:
            args += ["--mount", f"type=bind,src={mount.source.resolve()},dst={mount.target},readonly={str(mount.mode == 'ro').lower()}"]
        args += [spec.image, *spec.command]
        result = self._command(args)
        container_id = result.stdout.strip().splitlines()[-1]
        if not container_id:
            raise ContainerRuntimeError("docker create returned no container id", code="container_create_failed")
        inspect = self.inspect(container_id)
        configured_image = str(inspect.get("Config", {}).get("Image") or spec.image)
        try:
            image_digest = self.resolve_image_digest(spec.image)
        except ContainerRuntimeError:
            image_digest = configured_image
        return ContainerAttemptHandle(container_id, name, spec.identity, image_digest, labels=labels)

    def start(self, handle: ContainerAttemptHandle) -> None:
        self._command(["start", handle.container_id])

    def inspect(self, container: str) -> dict[str, Any]:
        result = self._command(["inspect", container])
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ContainerRuntimeError("docker inspect returned invalid JSON", code="docker_invalid_response") from exc
        return dict(payload[0]) if isinstance(payload, list) and payload and isinstance(payload[0], dict) else {}

    def stats(self, container: str) -> dict[str, Any]:
        result = self._command(["stats", "--no-stream", "--format", "{{json .}}", container], timeout=30)
        try:
            payload = json.loads(result.stdout.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ContainerRuntimeError("docker stats returned invalid JSON", code="docker_invalid_response") from exc
        return dict(payload) if isinstance(payload, dict) else {}

    def resolve_image_digest(self, image: str) -> str:
        result = self._command(["image", "inspect", "--format", "{{json .RepoDigests}}", image])
        try:
            digests = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ContainerRuntimeError("docker image inspect returned invalid JSON", code="docker_invalid_response") from exc
        if isinstance(digests, list) and digests:
            return str(digests[0])
        if "@sha256:" in image:
            return image
        raise ContainerRuntimeError(f"docker image has no immutable repo digest: {image}", code="container_image_unpinned")

    def collect(self, handle: ContainerAttemptHandle, *, timeout_seconds: float | None = None, cancellation_token: CancellationToken | None = None) -> ContainerAttemptResult:
        timed_out = False
        cancelled = False
        try:
            self._command(["wait", handle.container_id], timeout=timeout_seconds)
        except ContainerRuntimeError as exc:
            if exc.code == "docker_command_timeout":
                timed_out = True
            else:
                raise exc
        if cancellation_token is not None and cancellation_token.is_cancelled:
            cancelled = True
        logs = self._command(["logs", handle.container_id])
        inspect = self.inspect(handle.container_id)
        state = inspect.get("State") if isinstance(inspect.get("State"), dict) else {}
        try:
            stats = self.stats(handle.container_id)
        except ContainerRuntimeError:
            stats = {}
        return ContainerAttemptResult(handle, state.get("ExitCode"), logs.stdout, logs.stderr, timed_out, cancelled, bool(state.get("OOMKilled")), inspect=inspect, stats=stats)

    def stop(self, handle: ContainerAttemptHandle, *, grace_seconds: float) -> None:
        self._command(["stop", "--time", str(max(0, int(grace_seconds))), handle.container_id])

    def kill(self, handle: ContainerAttemptHandle) -> None:
        self._command(["kill", handle.container_id])

    def remove(self, handle: ContainerAttemptHandle, *, force: bool = False) -> CleanupReport:
        try:
            args = ["rm"] + (["-f"] if force else []) + [handle.container_id]
            self._command(args, timeout=30)
            return CleanupReport(True, handle.container_id)
        except ContainerRuntimeError as exc:
            return CleanupReport(False, handle.container_id, str(exc))

    def recover_orphans(self, *, owner: Mapping[str, str]) -> list[CleanupReport]:
        result = self._command(["ps", "-aq", "--filter", "label=benchmark.run_id"])
        reports: list[CleanupReport] = []
        for container_id in result.stdout.splitlines():
            if not container_id.strip():
                continue
            inspect = self.inspect(container_id.strip())
            labels = inspect.get("Config", {}).get("Labels", {}) if isinstance(inspect.get("Config"), dict) else {}
            if all(str(labels.get(f"benchmark.{key}") or "") == str(value) for key, value in owner.items()):
                try:
                    self._command(["rm", "-f", container_id.strip()])
                    reports.append(CleanupReport(True, container_id.strip()))
                except ContainerRuntimeError as exc:
                    reports.append(CleanupReport(False, container_id.strip(), str(exc)))
        return reports


def materialize_container_config(
    source: Path,
    destination: Path,
    *,
    agent_id: str,
    host_workspace: Path,
    host_skills_root: Path,
    skills_enabled: bool,
) -> Path:
    """Rewrite the run config to stable in-container paths.

    Only the requested agent entry is retained. Provider and model settings are
    copied from the run-scoped host config; host workspace and skill paths are
    never exposed to the container.
    """
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContainerRuntimeError("OpenClaw config must be a JSON object", code="container_config_invalid")
    agents = payload.get("agents")
    entries = agents.get("list") if isinstance(agents, dict) else None
    if not isinstance(entries, list):
        raise ContainerRuntimeError("OpenClaw config agents.list is invalid", code="container_config_invalid")

    host_workspace_text = str(host_workspace.resolve())
    host_skills_text = str(host_skills_root.resolve())

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            replacements = (
                (host_workspace_text, "/benchmark/workspace"),
                (host_skills_text, "/opt/benchmark/skills"),
                ("/scripts/run_skill.py", "/opt/benchmark/scripts/run_skill.py"),
            )
            for old, new in replacements:
                if value == old:
                    return new
                if value.startswith(old + "/"):
                    return new + value[len(old) :]
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    selected = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or "") == agent_id:
            item = rewrite(entry)
            item["workspace"] = "/benchmark/workspace"
            item["agentDir"] = "/benchmark/session/agent"
            selected.append(item)
    if not selected:
        raise ContainerRuntimeError(f"OpenClaw agent is missing from config: {agent_id}", code="container_config_invalid")
    payload["agents"] = {**(agents if isinstance(agents, dict) else {}), "list": selected}

    plugins = payload.get("plugins")
    if isinstance(plugins, dict):
        load = plugins.get("load")
        if isinstance(load, dict):
            paths = load.get("paths")
            if isinstance(paths, list):
                load["paths"] = [
                    "/opt/benchmark/benchmarking/runtime/openclaw_plugins/benchmark-workdir-guard"
                    if "benchmark-workdir-guard" in str(path)
                    else path
                    for path in paths
                ]
    if not skills_enabled:
        skills = payload.get("skills")
        if isinstance(skills, dict):
            load = skills.get("load")
            if isinstance(load, dict):
                load["extraDirs"] = []

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination
