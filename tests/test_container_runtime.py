import json
import os
import socket
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarking.runtime.agent_workspace import AttemptIdentity
from benchmarking.runtime.container_network import ContainerNetworkConfig
from benchmarking.runtime.container_runtime import (
    ContainerAttemptSpec,
    ContainerMount,
    ContainerRuntimeError,
    DockerContainerRuntime,
    materialize_container_config,
)


def identity() -> AttemptIdentity:
    return AttemptIdentity("run", "inv", "group", "single_llm", "agent", "record", 0, "session", "template")


@pytest.mark.parametrize("operation", ["inspect", "logs", "start", "kill", "rm", "ps"])
def test_docker_commands_always_have_bounded_outer_timeout(operation):
    def run(cmd, **kwargs):
        assert 0 < kwargs["timeout"] <= 30
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    runtime = DockerContainerRuntime(docker_executable="docker", run_subprocess=run)
    with pytest.raises(ContainerRuntimeError) as error:
        runtime._command([operation, "owned"])
    assert error.value.code == "docker_command_timeout"


def test_repeated_cancellation_skips_grace_wait():
    from benchmarking.runtime.cancellation import CancellationReason, CancellationToken
    from benchmarking.runtime.container_runtime import ContainerAttemptHandle
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    token = CancellationToken()
    token.cancel(CancellationReason(source="first"))
    token.cancel(CancellationReason(source="second"))
    DockerContainerRuntime(docker_executable="docker", run_subprocess=run).terminate(
        ContainerAttemptHandle("owned", "owned", identity(), "image"), cancellation_token=token)
    assert commands == [["docker", "kill", "--signal", "TERM", "owned"], ["docker", "kill", "owned"]]
    assert token.reason.source == "first"


def test_command_timeout_does_not_persist_environment_secrets():
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    runtime = DockerContainerRuntime(docker_executable="docker", run_subprocess=run)
    with pytest.raises(ContainerRuntimeError) as error:
        runtime._command(["create", "--env", "PROVIDER_TOKEN=secret-value", "image"])
    assert "secret-value" not in str(error.value)
    assert "secret-value" not in json.dumps(error.value.details)


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


def test_create_passes_explicit_dns_servers(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(command, 0, '[{"Image":"sha256:image"}]', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    DockerContainerRuntime(docker_executable="docker", run_subprocess=run).create(
        ContainerAttemptSpec(
            identity=identity(), image="benchmark:base", command=("run",),
            network_mode="bridge", dns_servers=("223.5.5.5", "1.1.1.1"),
            mounts=(ContainerMount(tmp_path, Path("/benchmark/workspace"), "rw", "workspace"),),
        )
    )
    command = commands[0]
    assert command[command.index("--dns") + 1 : command.index("--dns") + 4] == ["223.5.5.5", "--dns", "1.1.1.1"]


def test_container_names_preserve_full_identity_after_truncation():
    names = []

    def run(command, **kwargs):
        if command[1] == "create":
            names.append(command[command.index("--name") + 1])
            return subprocess.CompletedProcess(command, 0, "container-id\n", "")
        return subprocess.CompletedProcess(command, 0, '[{"Image":"sha256:image"}]', "")

    runtime = DockerContainerRuntime(docker_executable="docker", run_subprocess=run)
    original = replace(
        identity(),
        run_id="verifier-grounded-property-calculation-easy-qwen3-8-flash-20260911-210118",
        record_id="property_calculation_basic_003_diethyl_ether_aqueous_solvation_free_energy",
        group_id="single_llm_skills_on",
        session_id="benchmark-single_llm_skills_on-property-calculation-basic-003-session",
    )
    variants = [
        original,
        replace(original, group_id="single_llm_skills_off", session_id=original.session_id.replace("skills_on", "skills_off")),
        replace(original, invocation_id="another-invocation"),
        replace(original, attempt_index=1),
        replace(original, session_id=original.session_id + "-retry1"),
        replace(original, agent_id="another-agent"),
        replace(original, record_id=original.record_id + "-different"),
    ]
    for item in [*variants, original]:
        runtime.create(ContainerAttemptSpec(item, "image", ("run",)))
    assert len(set(names[:-1])) == len(variants)
    assert names[0] == names[-1]
    assert all(len(name) <= 190 for name in names)


@pytest.mark.parametrize("status,extra", [("ready", {"http_status": 401}), ("failed", {"code": "ENOTFOUND"})])
def test_network_probe_uses_frozen_environment_and_preserves_result(status, extra):
    network = ContainerNetworkConfig((("HTTPS_PROXY", "http://user:secret@host.docker.internal:7892"),))
    def run(command, **kwargs):
        assert command[1] == "run"
        assert command[command.index("--network") + 1] == "host"
        assert command[command.index("--entrypoint") + 1] == "node"
        assert "--rm" in command and "--env" in command and "--mount" not in command
        assert "secret" not in " ".join(command)
        assert kwargs["env"]["HTTPS_PROXY"] == dict(network.proxy_environment)["HTTPS_PROXY"]
        assert json.loads(kwargs["input"])["model"]["baseUrl"] == "https://provider.example"
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(command, 0, json.dumps({"status": status, **extra}), "")

    report = DockerContainerRuntime(docker_executable="docker", run_subprocess=run).check_provider_connection(image="image", network=network, model={"baseUrl": "https://provider.example"}, request={})
    assert report == {"status": status, **extra}


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_dns_probe_timeout_removes_owned_container(cleanup_failure):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, int(cleanup_failure), "", "daemon unavailable" if cleanup_failure else "")

    runtime = DockerContainerRuntime(docker_executable="docker", run_subprocess=run)
    with pytest.raises(ContainerRuntimeError) as error:
        runtime.check_provider_connection(image="image", network=ContainerNetworkConfig(), model={"baseUrl": "https://provider.example"}, request={})
    assert error.value.code == ("container_cleanup_failed" if cleanup_failure else "docker_command_timeout")
    name = commands[0][commands[0].index("--name") + 1]
    assert commands[-1] == ["docker", "rm", "-f", name]


def test_materialize_container_config_uses_attempt_local_agent_state(tmp_path: Path) -> None:
    source = tmp_path / "runtime.json"
    source.write_text(
        '{"agents":{"list":[{"id":"agent","workspace":"/host/work","agentDir":"/host/agent"}]},'
        '"tools":{"exec":{"pathPrepend":["/host/work/bin"]}}}',
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
    assert payload["tools"]["exec"]["pathPrepend"] == [
        "/benchmark/workspace/scratch/.runtime-bin",
        "/benchmark/workspace/bin",
    ]


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
    from benchmarking.runtime.agent_workspace import (
        SCHEMA_VERSION,
        SENTINEL_FILENAME,
        SENTINEL_KIND,
    )
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
