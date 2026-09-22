import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from benchmarking.runtime import provider_preflight
from benchmarking.runtime.container_network import ContainerNetworkConfig
from benchmarking.runtime.container_runtime import ContainerRuntimeError


@pytest.fixture
def setup_preflight(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "models": {"providers": {"qwen": {"baseUrl": "${QWEN_BASE_URL}", "apiKey": "secret"}}},
        "agents": {"defaults": {"models": {"qwen/flash": {"alias": "flash"}}}},
    }))
    env = {"QWEN_BASE_URL": "https://provider.example/v1"}
    monkeypatch.setattr(provider_preflight, "runtime_environment", lambda: env)
    runtime = Mock()
    runtime.check_provider_connection.return_value = {"status": "ready", "http_status": 401}
    return config, env, runtime


@pytest.mark.parametrize("model", ["qwen/flash", "flash"])
def test_provider_connection_uses_same_network_without_authentication(setup_preflight, model):
    config, _, runtime = setup_preflight
    network = ContainerNetworkConfig((("HTTPS_PROXY", "http://host.docker.internal:7892"),))
    report = provider_preflight.check_provider_connection(runtime=runtime, image="pinned", config_path=config, model=model, network=network)
    call = runtime.check_provider_connection.call_args.kwargs
    assert call["network"] is network
    assert call["model"] == {"baseUrl": "https://provider.example/v1", "provider": "qwen", "id": "flash", "api": "openai-responses"}
    assert call["request"] == {}
    assert "secret" not in json.dumps(report)
    assert report["status"] == "ready"
    assert report["http_status"] == 401


@pytest.mark.parametrize("code", ["ENOTFOUND", "ECONNREFUSED", "ETIMEOUT", "HTTP_UNAVAILABLE"])
def test_provider_connection_preserves_failures(setup_preflight, code):
    config, _, runtime = setup_preflight
    runtime.check_provider_connection.return_value = {"status": "failed", "code": code}
    report = provider_preflight.check_provider_connection(runtime=runtime, image="image", config_path=config, model="qwen/flash", network=ContainerNetworkConfig())
    assert report["code"] == code and report["status"] == "failed"


def test_provider_connection_skips_builtin_endpoint(setup_preflight):
    config, _, runtime = setup_preflight
    report = provider_preflight.check_provider_connection(runtime=runtime, image="image", config_path=config, model="builtin/model", network=ContainerNetworkConfig())
    assert report["reason"] == "no_explicit_provider_endpoint"
    runtime.check_provider_connection.assert_not_called()


def test_provider_connection_rejects_unresolved_endpoint(setup_preflight):
    config, env, runtime = setup_preflight
    env["QWEN_BASE_URL"] = ""
    with pytest.raises(ContainerRuntimeError) as error:
        provider_preflight.check_provider_connection(runtime=runtime, image="image", config_path=config, model="qwen/flash", network=ContainerNetworkConfig())
    assert error.value.code == "provider_endpoint_invalid"


def test_cli_connection_failure_stops_before_workspace_recovery_and_scheduling(setup_preflight, monkeypatch, tmp_path):
    from benchmarking.core.records import load_records
    from benchmarking.service.single import execution
    from benchmarking.workflow import cli
    from benchmarking.workflow.errors import BenchmarkError

    config, _, runtime = setup_preflight
    output = tmp_path / "run"
    record = load_records([
        Path(__file__).resolve().parents[1]
        / "benchmarking/resources/verifier_grounded/tracks/open_generation_rdkit.jsonl"
    ])[0]
    monkeypatch.setattr(sys, "argv", ["benchmark", "--openclaw-config", str(config), "--exact-output-dir", str(output), "--single-agent-model", "qwen/flash"])
    monkeypatch.setattr(execution, "select_track_files", lambda args: [tmp_path / "test.jsonl"])
    monkeypatch.setattr(execution, "select_records", lambda files, args: [record])
    manager = Mock(runtime_root=tmp_path / "workspaces")
    monkeypatch.setattr(cli, "AttemptWorkspaceManager", Mock(return_value=manager))
    network = ContainerNetworkConfig((("HTTPS_PROXY", "http://host.docker.internal:7892"),))
    monkeypatch.setattr(cli, "resolve_container_network", lambda: network)
    runtime.check_ready.return_value = {}
    runtime.resolve_image_digest.return_value = "sha256:image"
    runtime.recover_orphans.return_value = []
    runtime.check_provider_connection.return_value = {"status": "failed", "code": "ECONNREFUSED"}
    monkeypatch.setattr(cli, "DockerContainerRuntime", lambda: runtime)
    with pytest.raises(BenchmarkError, match="Provider connection failed inside Docker"):
        cli.main()
    manager.recover_all_incomplete.assert_not_called()
    assert runtime.check_provider_connection.call_args.kwargs["network"] is network
    startup = json.loads((output / "docker-startup.json").read_text())
    assert startup["status"] == "failed"
    assert startup["error_code"] == "provider_connectivity_failed"
    assert startup["network"] == network.to_meta()
    assert startup["provider_connectivity"]["code"] == "ECONNREFUSED"
    assert json.loads((output / "runtime-manifest.json").read_text())["terminal_status"] == "failed"
    assert not (output / "per-record").exists()
