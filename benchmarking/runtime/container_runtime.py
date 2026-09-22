"""Docker lifecycle helpers for isolated benchmark attempts.

The adapter intentionally shells out to the Docker CLI.  This keeps the
orchestrator dependency-free while still making every container operation
injectable and testable.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from benchmarking.runtime.agent_workspace import (
    AttemptIdentity,
    AttemptWorkspaceManager,
    workspace_slug,
)
from benchmarking.runtime.bundles import RuntimePathProjection
from benchmarking.runtime.cancellation import CancellationToken
from benchmarking.runtime.container_network import ContainerNetworkConfig
from benchmarking.runtime.container_resources import DockerStatsSampler
from benchmarking.runtime.observability import (
    active_runtime_metrics,
    increment,
    measure,
)


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
    dns_servers: tuple[str, ...] = ()
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


class _DockerWaitClient:
    """One long-lived docker wait CLI process owned by a lifecycle operation."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.started = time.monotonic()
        self._observed = False

    def wait(self, timeout: float) -> bool:
        try:
            self.process.wait(timeout=max(0.01, timeout))
            return True
        except subprocess.TimeoutExpired:
            return False

    def result(self) -> str:
        stdout, stderr = self.process.communicate()
        if self.process.returncode != 0:
            detail = (stderr or stdout or "").strip()
            raise ContainerRuntimeError(
                detail[:2000] or f"docker wait exited {self.process.returncode}",
                code="docker_wait_failed",
                details={"returncode": self.process.returncode},
            )
        return stdout

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
        finally:
            if not self._observed:
                metrics = active_runtime_metrics()
                if metrics is not None:
                    metrics.duration("docker_command", time.monotonic() - self.started)
                    metrics.duration("docker_wait_client", time.monotonic() - self.started)
                self._observed = True


def _identity_labels(identity: AttemptIdentity) -> dict[str, str]:
    return {f"benchmark.{key}": str(value) for key, value in identity.sentinel_fields().items()}


class DockerContainerRuntime:
    def __init__(self, *, docker_executable: str | None = None, run_subprocess: CommandRunner = subprocess.run,
                 popen_subprocess: Any = subprocess.Popen, use_wait_client: bool | None = None) -> None:
        self.docker = docker_executable or shutil.which("docker")
        self._run = run_subprocess
        self._popen = popen_subprocess
        # Injected command runners are used by contract tests and cannot safely
        # be mixed with a real Popen wait client.
        self._use_wait_client = use_wait_client if use_wait_client is not None else run_subprocess is subprocess.run

    def _start_wait_client(self, container_id: str) -> _DockerWaitClient:
        increment("docker_command_count")
        increment("docker_command_count.wait")
        increment("docker_wait_client_count")
        try:
            process = self._popen(
                [self._require_docker(), "wait", container_id],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            increment("docker_command_failure_count")
            raise ContainerRuntimeError(
                f"unable to start docker wait client: {exc}",
                code="docker_wait_start_failed",
                details={"container_id": container_id},
            ) from exc
        return _DockerWaitClient(process)

    def _require_docker(self) -> str:
        if not self.docker:
            raise ContainerRuntimeError("docker executable not found", code="docker_unavailable")
        return self.docker

    def _command(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        kwargs.setdefault("timeout", 30 if args[0] in {"create", "start", "logs", "kill", "rm", "stop"} else 15)
        increment("docker_command_count")
        increment(f"docker_command_count.{args[0]}")
        try:
            with measure("docker_command"):
                result = self._run([self._require_docker(), *args], text=True, capture_output=True, check=False, **kwargs)
        except subprocess.TimeoutExpired as exc:
            increment("docker_command_timeout_count")
            raise ContainerRuntimeError(
                f"docker {args[0]} exceeded its {kwargs['timeout']}-second command deadline",
                code="docker_command_timeout", details={"stage": args[0], "timeout_seconds": kwargs["timeout"]},
            ) from exc
        except OSError as exc:
            increment("docker_command_failure_count")
            raise ContainerRuntimeError(str(exc), code="docker_command_failed") from exc
        if result.returncode != 0:
            increment("docker_command_failure_count")
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

    def check_provider_connection(
        self, *, image: str, network: ContainerNetworkConfig, model: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        name = "benchmark-network-" + uuid.uuid4().hex
        script = (Path(__file__).parents[1] / "resources/provider-connectivity-probe.mjs").read_text()
        args = [
            "run", "--rm", "-i", "--name", name, "--network", network.network_mode,
            "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m", "--entrypoint", "node",
        ]
        for dns_server in network.dns_servers:
            args += ["--dns", dns_server]
        for key, _ in network.proxy_environment:
            args += ["--env", key]
        args += [image, "--input-type=module", "-e", script]
        try:
            result = self._command(args, timeout=30, env=network.apply(os.environ), input=json.dumps({"model": dict(model), "request": dict(request)}))
        except BaseException:
            # A killed Docker client does not stop its container.
            try:
                self._command(["rm", "-f", name])
            except ContainerRuntimeError as cleanup_error:
                if "No such container" not in str(cleanup_error):
                    raise ContainerRuntimeError(
                        f"Network probe cleanup failed: {name}", code="container_cleanup_failed",
                        details={"container_name": name},
                    ) from cleanup_error
            raise
        try:
            report = json.loads(result.stdout)
            if report["status"] not in {"ready", "failed"}:
                raise ValueError("invalid connectivity report")
        except (ValueError, TypeError, KeyError) as exc:
            raise ContainerRuntimeError("Docker network probe returned invalid JSON", code="docker_invalid_response") from exc
        return report

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
            if mount.kind in {"input", "config", "skills", "skill_runner"} and mount.mode != "ro":
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
        labels = {**{str(k): str(v) for k, v in spec.labels.items()}, **_identity_labels(spec.identity),
                  "benchmark.owner_pid": str(os.getpid()), "benchmark.owner_host": socket.gethostname()}
        raw_name = "-".join((spec.identity.run_id, spec.identity.record_id, str(spec.identity.attempt_index), spec.identity.session_id))
        identity_json = json.dumps(spec.identity.sentinel_fields(), sort_keys=True, separators=(",", ":"))
        identity_digest = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()[:16]
        name = "benchmark-" + re.sub(r"[^a-zA-Z0-9_.-]+", "-", raw_name).strip("-.")[:163] + "-" + identity_digest
        args = ["create", "--name", name, "--network", spec.network_mode, "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m"]
        if spec.cpu_limit is not None:
            args += ["--cpus", str(spec.cpu_limit)]
        if spec.memory_limit_bytes is not None:
            args += ["--memory", str(spec.memory_limit_bytes)]
        if spec.pids_limit is not None:
            args += ["--pids-limit", str(spec.pids_limit)]
        for dns_server in spec.dns_servers:
            args += ["--dns", dns_server]
        for key, value in {**labels}.items():
            args += ["--label", f"{key}={value}"]
        for key, value in spec.environment.items():
            args += ["--env", f"{key}={value}"]
        for mount in spec.mounts:
            args += ["--mount", f"type=bind,src={mount.source.resolve()},dst={mount.target},readonly={str(mount.mode == 'ro').lower()}"]
        args += [spec.image, *spec.command]
        try:
            result = self._command(args)
        except ContainerRuntimeError as exc:
            if exc.code == "docker_command_timeout":
                try:
                    inspected = self.inspect(name)
                    actual = (inspected.get("Config") or {}).get("Labels") or {}
                    if any(actual.get(key) != value for key, value in labels.items()):
                        raise ContainerRuntimeError("timed-out create ownership is unconfirmed")
                    self._command(["rm", "-f", name])
                except ContainerRuntimeError as cleanup_error:
                    raise ContainerRuntimeError(str(exc), code="container_cleanup_failed", details={
                        "container_name": name, "identity": spec.identity.sentinel_fields(),
                        "cleanup_error": str(cleanup_error), "execution_state": "unconfirmed"}) from exc
            raise
        container_id = result.stdout.strip().splitlines()[-1]
        if not container_id:
            raise ContainerRuntimeError("docker create returned no container id", code="container_create_failed")
        try:
            inspect = self.inspect(container_id)
            image_digest = str(inspect.get("Image") or self.resolve_image_digest(spec.image))
        except Exception as exc:
            try:
                self._command(["rm", "-f", container_id], timeout=30)
            except ContainerRuntimeError as cleanup_error:
                raise ContainerRuntimeError(str(exc), code="container_cleanup_failed", details={
                    "container_id": container_id, "identity": spec.identity.sentinel_fields(),
                    "cleanup_error": str(cleanup_error), "execution_state": "unconfirmed"}) from exc
            raise
        return ContainerAttemptHandle(container_id, name, spec.identity, image_digest, labels=labels)

    def start(self, handle: ContainerAttemptHandle) -> None:
        self._command(["start", handle.container_id])

    def start_resource_sampler(
        self,
        handle: ContainerAttemptHandle,
        *,
        output_path: Path,
        window_seconds: float = 5.0,
        heartbeat_path: Path | None = None,
    ) -> DockerStatsSampler:
        increment("docker_command_count")
        increment("docker_command_count.stats_stream")
        try:
            return DockerStatsSampler(
                command=[
                    self._require_docker(),
                    "stats",
                    "--format",
                    "{{json .}}",
                    handle.container_id,
                ],
                output_path=output_path,
                popen=self._popen,
                window_seconds=window_seconds,
                heartbeat_path=heartbeat_path,
            )
        except OSError as exc:
            increment("container_resource_sampler_failure_count")
            raise ContainerRuntimeError(
                f"unable to start docker stats sampler: {exc}",
                code="container_stats_start_failed",
                details={"container_id": handle.container_id},
            ) from exc

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
        result = self._command(["image", "inspect", "--format", "{{json .Id}}", image], timeout=15)
        try:
            digest = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ContainerRuntimeError("docker image inspect returned invalid JSON", code="docker_invalid_response") from exc
        if isinstance(digest, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            return digest
        raise ContainerRuntimeError(f"docker image has invalid immutable ID: {image}", code="container_image_unpinned")

    def collect(self, handle: ContainerAttemptHandle, *, timeout_seconds: float | None = None, cancellation_token: CancellationToken | None = None) -> ContainerAttemptResult:
        timed_out = False
        cancelled = False
        termination = {}
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        if not self._use_wait_client:
            return self._collect_legacy(handle, timeout_seconds=timeout_seconds, cancellation_token=cancellation_token)
        wait_client = self._start_wait_client(handle.container_id)
        try:
            while True:
                cancelled = cancellation_token is not None and cancellation_token.is_cancelled
                timed_out = deadline is not None and time.monotonic() >= deadline
                if cancelled or timed_out:
                    # Share the wait client while the supervisor finalizes evidence.
                    termination = self.terminate(
                        handle, cancellation_token=cancellation_token, _wait_client=wait_client
                    )
                    break
                interval = min(0.25, max(0.01, deadline - time.monotonic())) if deadline is not None else 0.25
                if wait_client.wait(interval):
                    wait_client.result()
                    break
        finally:
            wait_client.close()
        logs = self._command(["logs", handle.container_id])
        inspect = self.inspect(handle.container_id)
        config = inspect.get("Config")
        if isinstance(config, dict) and isinstance(config.get("Env"), list):
            config["Env"] = [f"{item.split('=', 1)[0]}=<redacted>" for item in config["Env"]]
        state = inspect.get("State") if isinstance(inspect.get("State"), dict) else {}
        try:
            stats = self.stats(handle.container_id)
        except ContainerRuntimeError:
            stats = {}
        return ContainerAttemptResult(handle, state.get("ExitCode"), logs.stdout, logs.stderr, timed_out, cancelled, bool(state.get("OOMKilled")), inspect=inspect, stats=stats, cleanup={"termination": termination})

    def _collect_legacy(self, handle: ContainerAttemptHandle, *, timeout_seconds: float | None,
                        cancellation_token: CancellationToken | None) -> ContainerAttemptResult:
        timed_out = False
        cancelled = False
        termination = {}
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        while True:
            cancelled = cancellation_token is not None and cancellation_token.is_cancelled
            timed_out = deadline is not None and time.monotonic() >= deadline
            if cancelled or timed_out:
                termination = self.terminate(handle, cancellation_token=cancellation_token)
                break
            interval = min(1.0, max(0.01, deadline - time.monotonic())) if deadline is not None else 1.0
            try:
                self._command(["wait", handle.container_id], timeout=interval)
                break
            except ContainerRuntimeError as exc:
                if exc.code != "docker_command_timeout":
                    raise
        logs = self._command(["logs", handle.container_id])
        inspect = self.inspect(handle.container_id)
        config = inspect.get("Config")
        if isinstance(config, dict) and isinstance(config.get("Env"), list):
            config["Env"] = [f"{item.split('=', 1)[0]}=<redacted>" for item in config["Env"]]
        state = inspect.get("State") if isinstance(inspect.get("State"), dict) else {}
        try:
            stats = self.stats(handle.container_id)
        except ContainerRuntimeError:
            stats = {}
        return ContainerAttemptResult(handle, state.get("ExitCode"), logs.stdout, logs.stderr, timed_out, cancelled,
                                      bool(state.get("OOMKilled")), inspect=inspect, stats=stats,
                                      cleanup={"termination": termination})

    def stop(self, handle: ContainerAttemptHandle, *, grace_seconds: float) -> None:
        self._command(["stop", "--time", str(max(0, int(grace_seconds))), handle.container_id], timeout=max(0, grace_seconds) + 30)

    def terminate(self, handle: ContainerAttemptHandle, *, cancellation_token: CancellationToken | None = None,
                  grace_seconds: float = 420, _wait_client: _DockerWaitClient | None = None) -> dict[str, Any]:
        self._command(["kill", "--signal", "TERM", handle.container_id])
        if not self._use_wait_client and _wait_client is None:
            deadline = time.monotonic() + grace_seconds
            while time.monotonic() < deadline:
                if cancellation_token is not None and cancellation_token.request_count > 1:
                    break
                try:
                    self._command(["wait", handle.container_id], timeout=min(1, max(0.01, deadline - time.monotonic())))
                    return {"term_sent": True, "forced": False, "grace_seconds": grace_seconds}
                except ContainerRuntimeError as exc:
                    if exc.code != "docker_command_timeout":
                        raise
            self.kill(handle)
            return {"term_sent": True, "forced": True, "grace_seconds": grace_seconds,
                    "repeat_cancellation": cancellation_token is not None and cancellation_token.request_count > 1}
        deadline = time.monotonic() + grace_seconds
        owns_wait_client = _wait_client is None
        wait_client = _wait_client
        try:
            while time.monotonic() < deadline:
                if cancellation_token is not None and cancellation_token.request_count > 1:
                    break
                if wait_client is None:
                    wait_client = self._start_wait_client(handle.container_id)
                if wait_client.wait(min(0.25, max(0.01, deadline - time.monotonic()))):
                    wait_client.result()
                    return {"term_sent": True, "forced": False, "grace_seconds": grace_seconds}
            self.kill(handle)
            if wait_client is not None and wait_client.wait(30):
                wait_client.result()
            return {"term_sent": True, "forced": True, "grace_seconds": grace_seconds,
                    "repeat_cancellation": cancellation_token is not None and cancellation_token.request_count > 1}
        finally:
            if owns_wait_client and wait_client is not None:
                wait_client.close()

    def kill(self, handle: ContainerAttemptHandle) -> None:
        self._command(["kill", handle.container_id])

    def remove(self, handle: ContainerAttemptHandle, *, force: bool = False) -> CleanupReport:
        try:
            args = ["rm"] + (["-f"] if force else []) + [handle.container_id]
            self._command(args, timeout=30)
            return CleanupReport(True, handle.container_id)
        except ContainerRuntimeError as exc:
            return CleanupReport(False, handle.container_id, str(exc))

    def recover_orphans(self, *, runtime_root: Path) -> list[dict[str, Any]]:
        result = self._command(["ps", "-aq", "--no-trunc", "--filter", "label=benchmark.run_id"])
        reports: list[dict[str, Any]] = []
        for container_id in result.stdout.splitlines():
            if not container_id.strip():
                continue
            report = {"container_id": container_id.strip(), "removed": False}
            lock_handle = None
            try:
                inspected = self.inspect(container_id.strip())
                labels = inspected.get("Config", {}).get("Labels") or {}
                report["run_id"] = labels.get("benchmark.run_id", "")
                if labels.get("benchmark.owner_host") != socket.gethostname():
                    raise ValueError("owner host is missing or differs")
                pid = int(labels.get("benchmark.owner_pid", "0"))
                if pid <= 0:
                    raise ValueError("owner PID is missing")
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise ValueError("owner process is still alive")
                mounts = [item for item in inspected.get("Mounts", []) if item.get("Destination") == "/benchmark/workspace"]
                if len(mounts) != 1:
                    raise ValueError("workspace mount is missing or ambiguous")
                workspace = Path(mounts[0]["Source"])
                if workspace.is_symlink() or not workspace.resolve().is_relative_to(runtime_root.resolve()):
                    raise ValueError("workspace is outside managed runtime")
                sentinel = AttemptWorkspaceManager._read_sentinel_payload(workspace)
                identity = AttemptWorkspaceManager._identity_from_sentinel(sentinel)
                if identity.runner_kind != "single_llm" or any(
                    str(labels.get(f"benchmark.{key}", "")) != str(value)
                    for key, value in identity.sentinel_fields().items()
                ):
                    raise ValueError("container identity does not match workspace sentinel")
                if Path(sentinel["workspace_path"]).resolve() != workspace.resolve():
                    raise ValueError("workspace sentinel path mismatch")
                lock_path = workspace.parents[2] / "locks" / f"{workspace_slug(identity.group_id)}--{workspace_slug(identity.agent_id)}.lock"
                if lock_path.is_symlink():
                    raise ValueError("workspace recovery lock is a symlink")
                if lock_path.exists():
                    lock_handle = lock_path.open("rb")
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                handle = ContainerAttemptHandle(container_id.strip(), str(inspected.get("Name", "")), identity, str(inspected.get("Image", "")))
                if (inspected.get("State") or {}).get("Running"):
                    self.terminate(handle)
                from benchmarking.runtime.attempt_finalization import write_evidence
                try:
                    logs = self._command(["logs", container_id.strip()])
                    write_evidence(workspace / "scratch/notes/orphan-evidence.json", {
                        "identity": identity.sentinel_fields(), "stdout": logs.stdout, "stderr": logs.stderr})
                except (OSError, ContainerRuntimeError) as exc:
                    report["evidence_error"] = str(exc)
                self._command(["rm", "-f", container_id.strip()], timeout=30)
                report["removed"] = True
            except (ContainerRuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
                report["reason"] = str(exc)
            finally:
                if lock_handle is not None:
                    lock_handle.close()
            reports.append(report)
        return reports


def materialize_container_config(
    source: Path,
    destination: Path,
    *,
    agent_id: str,
    host_workspace: Path,
    host_skills_root: Path,
    skills_enabled: bool,
    path_projection: RuntimePathProjection | None = None,
    workspace_policy: Mapping[str, Any] | None = None,
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
    entries = agents.get("entries") if isinstance(agents, dict) else None
    if entries is None and isinstance(agents, dict):
        legacy = agents.get("list")
        if isinstance(legacy, list):
            entries = {str(item.get("id")): item for item in legacy if isinstance(item, dict) and item.get("id")}
    if isinstance(entries, list):
        entries = {str(item.get("id")): item for item in entries if isinstance(item, dict) and item.get("id")}
    if not isinstance(entries, dict):
        raise ContainerRuntimeError("OpenClaw config agents.entries is invalid", code="container_config_invalid")

    host_workspace_text = str(host_workspace.resolve())
    host_skills_text = str(host_skills_root.resolve())

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            replacements = tuple([
                *sorted(((host, container) for container, host in
                         (path_projection.audit_mappings() if path_projection else {}).items()),
                        key=lambda item: len(item[0]), reverse=True),
                (host_workspace_text, "/benchmark/workspace"),
                (host_skills_text, "/opt/benchmark/skills"),
                ("/scripts/run_skill.py", "/opt/benchmark/scripts/run_skill.py"),
            ])
            for old, new in replacements:
                if value == old:
                    return new
                if value.startswith(old + "/"):
                    return new + value[len(old) :]
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            if value.get("source") == "env" and isinstance(value.get("id"), str):
                return "${" + value["id"] + "}"
            return {key: rewrite(item) for key, item in value.items()}
        return value

    if workspace_policy is not None:
        payload.setdefault("plugins", {}).setdefault("entries", {}).setdefault(
            "benchmark-workdir-guard", {}).setdefault("config", {}).setdefault("agentPolicies", {})[agent_id] = dict(workspace_policy)
    payload = rewrite(payload)
    agents_payload = payload.setdefault("agents", {})
    defaults = agents_payload.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        raise ContainerRuntimeError("OpenClaw config agents.defaults is invalid", code="container_config_invalid")
    defaults["skipBootstrap"] = True
    payload.pop("secrets", None)
    selected = []
    for entry in entries.values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or "") == agent_id:
            item = rewrite(entry)
            item["workspace"] = "/benchmark/workspace"
            item["agentDir"] = f"/benchmark/session/agents/{agent_id}/agent"
            selected.append(item)
    if not selected:
        raise ContainerRuntimeError(f"OpenClaw agent is missing from config: {agent_id}", code="container_config_invalid")
    payload["agents"] = {**payload["agents"], "entries": {agent_id: selected[0]}}
    if len(selected) > 1:
        raise ContainerRuntimeError(f"OpenClaw agent selection is ambiguous: {agent_id}", code="container_config_invalid")
    if len(entries) > 1:
        payload["agents"]["ownership"] = "explicit"

    tools = payload.setdefault("tools", {})
    if not isinstance(tools, dict):
        raise ContainerRuntimeError("OpenClaw config tools is invalid", code="container_config_invalid")
    exec_config = tools.setdefault("exec", {})
    if not isinstance(exec_config, dict):
        raise ContainerRuntimeError("OpenClaw config tools.exec is invalid", code="container_config_invalid")
    existing_path_prepend = exec_config.get("pathPrepend", [])
    if not isinstance(existing_path_prepend, list) or not all(
        isinstance(item, str) for item in existing_path_prepend
    ):
        raise ContainerRuntimeError(
            "OpenClaw config tools.exec.pathPrepend is invalid",
            code="container_config_invalid",
        )
    attempt_tool_bin = "/benchmark/workspace/scratch/.runtime-bin"
    exec_config["pathPrepend"] = [
        attempt_tool_bin,
        *(item for item in existing_path_prepend if item != attempt_tool_bin),
    ]

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
