import json
import sys
from unittest.mock import Mock

import pytest

from benchmarking.runtime import provider_preflight
from benchmarking.runtime.container_runtime import ContainerRuntimeError


@pytest.fixture
def setup_preflight(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "models": {"providers": {"qwen": {"baseUrl": "${QWEN_BASE_URL}"}}},
        "agents": {"defaults": {"models": {"qwen/flash": {"alias": "flash"}}}},
    }))
    env = {"QWEN_BASE_URL": "https://user:secret@provider.example/v1?key=secret"}
    monkeypatch.setattr(provider_preflight, "build_openclaw_subprocess_env", lambda: env)
    monkeypatch.setattr(provider_preflight, "dotenv_values", lambda path: {"QWEN_BASE_URL": "https://fallback.example"})
    runtime = Mock()
    runtime.check_dns.return_value = {"status": "ready", "hostname": "provider.example"}
    return config, env, runtime


@pytest.mark.parametrize("model", ["qwen/flash", "flash"])
def test_provider_dns_selects_endpoint_and_keeps_credentials_out_of_probe(setup_preflight, model):
    config, _, runtime = setup_preflight
    report = provider_preflight.check_provider_dns(runtime=runtime, image="pinned-image", config_path=config, model=model)
    runtime.check_dns.assert_called_once_with(image="pinned-image", hostname="provider.example")
    assert "secret" not in json.dumps(report)
    assert report["provider"] == "qwen"


def test_provider_dns_preserves_resolver_error(setup_preflight):
    config, _, runtime = setup_preflight
    runtime.check_dns.return_value = {"status": "failed", "hostname": "provider.example", "code": "ENOTFOUND"}
    report = provider_preflight.check_provider_dns(runtime=runtime, image="image", config_path=config, model="qwen/flash")
    assert report["code"] == "ENOTFOUND" and report["status"] == "failed"


def test_provider_dns_skips_remote_proxy_resolution(setup_preflight):
    config, env, runtime = setup_preflight
    env.update(NODE_USE_ENV_PROXY="1", HTTPS_PROXY="http://proxy.example:8080")
    report = provider_preflight.check_provider_dns(runtime=runtime, image="image", config_path=config, model="qwen/flash")
    assert report["reason"] == "proxy_resolution"
    runtime.check_dns.assert_not_called()


def test_provider_dns_skips_builtin_endpoint(setup_preflight):
    config, _, runtime = setup_preflight
    report = provider_preflight.check_provider_dns(runtime=runtime, image="image", config_path=config, model="builtin/model")
    assert report["reason"] == "no_explicit_provider_endpoint"
    runtime.check_dns.assert_not_called()


def test_provider_dns_rejects_unresolved_endpoint(setup_preflight):
    config, env, runtime = setup_preflight
    env["QWEN_BASE_URL"] = ""
    with pytest.raises(ContainerRuntimeError) as error:
        provider_preflight.check_provider_dns(runtime=runtime, image="image", config_path=config, model="qwen/flash")
    assert error.value.code == "provider_endpoint_invalid"
    runtime.check_dns.assert_not_called()


def test_cli_dns_failure_stops_before_workspace_recovery_and_scheduling(setup_preflight, monkeypatch, tmp_path):
    from benchmarking.core.datasets import BenchmarkRecord
    from benchmarking.workflow import cli
    from benchmarking.workflow.errors import BenchmarkError

    config, _, runtime = setup_preflight
    output = tmp_path / "run"
    record = BenchmarkRecord(
        record_id="record", dataset="test", source_file="test.jsonl", prompt="question",
        eval_kind="generic_semantic", reference_answer="answer", payload={},
    )
    monkeypatch.setattr(sys, "argv", [
        "benchmark", "--openclaw-config", str(config), "--exact-output-dir", str(output),
        "--single-agent-model", "qwen/flash",
    ])
    monkeypatch.setattr(cli.dataset_selection, "select_dataset_files", lambda args: [tmp_path / "test.jsonl"])
    monkeypatch.setattr(cli.dataset_selection, "load_records", lambda files: [record])
    manager = Mock(runtime_root=tmp_path / "workspaces")
    monkeypatch.setattr(cli, "AttemptWorkspaceManager", Mock(return_value=manager))
    runtime.check_ready.return_value = {}
    runtime.resolve_image_digest.return_value = "sha256:image"
    runtime.recover_orphans.return_value = []
    runtime.check_dns.return_value = {"status": "failed", "hostname": "provider.example", "code": "ENOTFOUND"}
    monkeypatch.setattr(cli, "DockerContainerRuntime", lambda: runtime)

    with pytest.raises(BenchmarkError, match="Provider DNS lookup failed inside Docker"):
        cli.main()
    manager.recover_all_incomplete.assert_not_called()
    startup = json.loads((output / "docker-startup.json").read_text())
    assert startup["status"] == "failed"
    assert startup["error_code"] == "provider_dns_failed"
    assert startup["provider_dns"]["code"] == "ENOTFOUND"
    assert json.loads((output / "runtime-manifest.json").read_text())["terminal_status"] == "failed"
    assert not (output / "per-record").exists()
