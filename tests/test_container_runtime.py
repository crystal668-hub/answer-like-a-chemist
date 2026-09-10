import subprocess
import json
import os
import socket
from pathlib import Path

import pytest

from benchmarking.runtime.agent_workspace import AttemptIdentity
from benchmarking.runtime.container_runtime import (
    ContainerAttemptSpec,
    ContainerMount,
    ContainerRuntimeError,
    DockerContainerRuntime,
    materialize_container_config,
)


def identity() -> AttemptIdentity:
    return AttemptIdentity("run", "inv", "group", "single_llm", "agent", "record", 0, "session", "template")


def test_mount_validation_rejects_writable_input(tmp_path: Path) -> None:
    with pytest.raises(ContainerRuntimeError, match="read-only"):
        DockerContainerRuntime._validate_mounts((ContainerMount(tmp_path, Path("/benchmark/input"), "rw", "input"),))


@pytest.mark.parametrize("target,kind", [("/opt/benchmark/skills", "skills"), ("/opt/benchmark/scripts/run_skill.py", "skill_runner")])
def test_skill_mounts_are_read_only(tmp_path, target, kind):
    with pytest.raises(ContainerRuntimeError, match="read-only"):
        DockerContainerRuntime._validate_mounts((ContainerMount(tmp_path, Path(target), "rw", kind),))


def test_create_builds_hardened_docker_command(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, '[{"Image":"sha256:image","Config":{"Image":"sha256:image"}}]', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    handle = DockerContainerRuntime(docker_executable="docker", run_subprocess=run).create(
        ContainerAttemptSpec(identity=identity(), image="benchmark:base", command=("run",), mounts=(ContainerMount(tmp_path, Path("/benchmark/workspace"), "rw", "workspace"),), cpu_limit=1.0, pids_limit=64)
    )
    command = commands[0]
    assert handle.container_id == "container-id"
    assert "--user" in command and "1000:1000" in command
    assert "--cap-drop" in command and "ALL" in command
    assert "--pids-limit" in command and "64" in command
    assert "--network" in command and "host" in command


def test_materialize_container_config_uses_attempt_local_agent_state(tmp_path: Path) -> None:
    source = tmp_path / "runtime.json"
    source.write_text(
        '{"agents":{"list":[{"id":"agent","workspace":"/host/work","agentDir":"/host/agent"}]}}',
        encoding="utf-8",
    )
    destination = tmp_path / "container.json"
    materialize_container_config(
        source,
        destination,
        agent_id="agent",
        host_workspace=Path("/host/work"),
        host_skills_root=tmp_path / "skills",
        skills_enabled=False,
    )
    payload = __import__("json").loads(destination.read_text(encoding="utf-8"))
    entry = payload["agents"]["list"][0]
    assert entry["workspace"] == "/benchmark/workspace"
    assert entry["agentDir"] == "/benchmark/session/agents/agent/agent"


@pytest.mark.parametrize("response", ['"sha256:bad"', 'null', '[]', 'invalid'])
def test_invalid_image_identity_fails(response):
    runtime = DockerContainerRuntime(docker_executable="docker", run_subprocess=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, response, ""))
    with pytest.raises(ContainerRuntimeError):
        runtime.resolve_image_digest("example:latest")


def test_missing_daemon_and_image_fail():
    runtime = DockerContainerRuntime(docker_executable="docker", run_subprocess=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "unavailable"))
    with pytest.raises(ContainerRuntimeError, match="unavailable"):
        runtime.check_ready()
    with pytest.raises(ContainerRuntimeError, match="unavailable"):
        runtime.resolve_image_digest("missing")


def test_postcreate_inspection_failure_removes_created_container():
    commands = []
    def run(cmd, **kw):
        commands.append(cmd)
        if cmd[1] == "inspect":
            return subprocess.CompletedProcess(cmd, 1, "", "inspection failed")
        return subprocess.CompletedProcess(cmd, 0, "container-id", "")
    runtime = DockerContainerRuntime(docker_executable="docker", run_subprocess=run)
    with pytest.raises(ContainerRuntimeError):
        runtime.create(ContainerAttemptSpec(identity(), "image", ("run",)))
    assert commands[-1] == ["docker", "rm", "-f", "container-id"]


def test_secret_refs_and_policy_paths_are_projected_without_secret_values(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({
        "agents": {"list": [{"id": "agent", "workspace": "/host/work"}]},
        "models": {"providers": {"test": {"apiKey": {"source": "env", "id": "TEST_API_KEY"}}}},
        "plugins": {"entries": {"guard": {"config": {"path": "/host/work/scratch"}}}},
    }))
    target = tmp_path / "target.json"
    materialize_container_config(source, target, agent_id="agent", host_workspace=Path("/host/work"), host_skills_root=tmp_path, skills_enabled=True)
    payload = json.loads(target.read_text())
    assert payload["models"]["providers"]["test"]["apiKey"] == "${TEST_API_KEY}"
    assert payload["plugins"]["entries"]["guard"]["config"]["path"] == "/benchmark/workspace/scratch"


@pytest.mark.parametrize("variant,removed", [("stale", True), ("alive", False), ("identity", False), ("missing", False), ("host", False)])
def test_orphan_recovery_requires_complete_stale_ownership(tmp_path, monkeypatch, variant, removed):
    from benchmarking.runtime.agent_workspace import SENTINEL_KIND, SENTINEL_FILENAME, SCHEMA_VERSION
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = {"kind": SENTINEL_KIND, "schema_version": SCHEMA_VERSION, **identity().sentinel_fields(), "workspace_path": str(workspace)}
    (workspace / SENTINEL_FILENAME).write_text(json.dumps(sentinel))
    labels = {f"benchmark.{key}": str(value) for key, value in identity().sentinel_fields().items()}
    labels.update({"benchmark.owner_host": socket.gethostname(), "benchmark.owner_pid": "12345"})
    if variant == "identity":
        labels["benchmark.attempt_index"] = "9"
    if variant == "missing":
        labels.pop("benchmark.session_id")
    if variant == "host":
        labels["benchmark.owner_host"] = "other-host"
    def kill(pid, signal):
        if variant != "alive":
            raise ProcessLookupError()
    monkeypatch.setattr(os, "kill", kill)
    commands = []
    def run(cmd, **kw):
        commands.append(cmd)
        response = "owned\n" if cmd[1] == "ps" else json.dumps([{"Config": {"Labels": labels}, "Mounts": [{"Source": str(workspace), "Destination": "/benchmark/workspace"}]}])
        return subprocess.CompletedProcess(cmd, 0, response, "")
    reports = DockerContainerRuntime(docker_executable="docker", run_subprocess=run).recover_orphans(runtime_root=tmp_path)
    assert reports[0]["removed"] is removed
    assert any(cmd[1] == "rm" for cmd in commands) is removed
