import subprocess
from pathlib import Path

import pytest

from benchmarking.runtime.agent_workspace import AttemptIdentity
from benchmarking.runtime.container_runtime import (
    ContainerAttemptSpec,
    ContainerMount,
    ContainerRuntimeError,
    DockerContainerRuntime,
)


def identity() -> AttemptIdentity:
    return AttemptIdentity("run", "inv", "group", "single_llm", "agent", "record", 0, "session", "template")


def test_mount_validation_rejects_writable_input(tmp_path: Path) -> None:
    with pytest.raises(ContainerRuntimeError, match="read-only"):
        DockerContainerRuntime._validate_mounts((ContainerMount(tmp_path, Path("/benchmark/input"), "rw", "input"),))


def test_create_builds_hardened_docker_command(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, '[{"Config":{"Image":"sha256:image"}}]', "")
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
