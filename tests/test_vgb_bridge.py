from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import time
from unittest.mock import patch

import pytest

from benchmarking.runtime import vgb_bridge as bridge


def test_release_config_pins_version_hash_and_complete_inventory() -> None:
    config = bridge.load_release_config()

    assert config.version == "0.9.2"
    assert config.source_tag == "v0.9.2"
    assert config.source_commit == "355bf7a04ac20104c05169874829a7969c920ca1"
    assert config.wheel_sha256 == "3ff814edff484dc1befdaf2b226d51cd308c1e17f387a06073276c63deb620b2"
    assert config.wheel_size == 184592
    assert {name: track["task_count"] for name, track in config.tracks.items()} == {
        "property_calculation_advanced": 20,
        "property_calculation_basic": 51,
        "rdkit": 14,
        "xtb": 20,
    }
    assert all(track["task_count"] == len(track["task_ids"]) for track in config.tracks.values())


def test_runtime_environment_does_not_inherit_agent_python_paths(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PYTHONPATH", "/agent/source")
    monkeypatch.setenv("VIRTUAL_ENV", "/agent/venv")

    env = bridge._runtime_env()

    assert env["PATH"] == "/usr/bin"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env


def test_evaluate_answer_rejects_unpinned_release_before_subprocess() -> None:
    with (
        patch.object(bridge, "_invoke_api") as invoke,
        pytest.raises(bridge.VerifierGroundedRuntimeError, match="does not match"),
    ):
        bridge.evaluate_answer(
            track="rdkit",
            task_id="rdkit_qed_max_001",
            answer_text="FINAL ANSWER: CCO",
            release_identity={"package": "wrong", "version": "0", "wheel_sha256": "0"},
        )
    invoke.assert_not_called()


def test_evaluate_answer_calls_public_api_runtime_with_track_and_task() -> None:
    config = bridge.load_release_config()
    expected = {"task_id": "rdkit_qed_max_001", "status": "scored", "scores": {"score": 0.5}}
    with patch.object(bridge, "_invoke_api", return_value=expected) as invoke:
        result = bridge.evaluate_answer(
            track="rdkit",
            task_id="rdkit_qed_max_001",
            answer_text="FINAL ANSWER: CCO",
            release_identity=config.identity,
        )

    assert result == expected
    payload = invoke.call_args.args[1]
    assert payload == {
        "action": "evaluate_one",
        "track": "rdkit",
        "task_id": "rdkit_qed_max_001",
        "answer_text": "FINAL ANSWER: CCO",
    }
    assert "source_repo" not in payload
    assert "verifier_specs" not in payload


def test_evaluate_answer_forwards_invocation_validation_cache() -> None:
    config = bridge.load_release_config()
    cache = bridge.InvocationValidationCache()
    expected = {"task_id": "rdkit_qed_max_001", "status": "scored", "scores": {"score": 0.5}}
    with patch.object(bridge, "_invoke_api", return_value=expected) as invoke:
        bridge.evaluate_answer(
            track="rdkit", task_id="rdkit_qed_max_001", answer_text="FINAL ANSWER: CCO",
            release_identity=config.identity, release_config=config, validation_cache=cache,
        )
    assert invoke.call_args.kwargs["validation_cache"] is cache


def test_evaluate_answer_uses_invocation_release_after_default_changes() -> None:
    invocation_config = bridge.load_release_config()
    changed_default = bridge.ReleaseConfig(
        **{
            **invocation_config.__dict__,
            "version": "future",
        }
    )
    expected = {"task_id": "rdkit_qed_max_001", "status": "scored", "scores": {"score": 0.5}}
    with (
        patch.object(bridge, "load_release_config", return_value=changed_default),
        patch.object(bridge, "_invoke_api", return_value=expected) as invoke,
    ):
        result = bridge.evaluate_answer(
            track="rdkit",
            task_id="rdkit_qed_max_001",
            answer_text="FINAL ANSWER: CCO",
            release_identity=invocation_config.identity,
            release_config=invocation_config,
        )

    assert result == expected
    assert invoke.call_args.args[0] is invocation_config


def test_load_public_reference_answers_calls_public_api_runtime() -> None:
    config = bridge.load_release_config()
    task_ids = config.tracks["property_calculation_advanced"]["task_ids"]
    expected = [{"task_id": task_id, "answer": 0.0} for task_id in task_ids]
    with patch.object(
        bridge, "_invoke_api", return_value={"reference_answers": expected}
    ) as invoke:
        result = bridge.load_public_reference_answers("property_calculation_advanced")

    assert result == expected
    assert invoke.call_args.args[1] == {
        "action": "reference_answers",
        "track": "property_calculation_advanced",
        "task_ids": task_ids,
    }


def test_load_public_reference_answers_rejects_incomplete_pinned_inventory() -> None:
    with patch.object(
        bridge,
        "_invoke_api",
        return_value={
            "reference_answers": [
                {
                    "task_id": "property_calculation_advanced_001_free_energy",
                    "answer": 0.258031679,
                    "unit": "kJ/mol",
                }
            ]
        },
    ), pytest.raises(bridge.VerifierGroundedRuntimeError, match="inventory"):
        bridge.load_public_reference_answers("property_calculation_advanced")


def test_invocation_validation_cache_reuses_success_and_invalidates_fingerprint(monkeypatch) -> None:
    config = bridge.load_release_config()
    cache = bridge.InvocationValidationCache()
    calls = []
    fingerprint = [("first",)]
    monkeypatch.setattr(bridge, "_validation_fingerprint", lambda _config: fingerprint[0])
    monkeypatch.setattr(bridge, "_validate_runtime_files_uncached", lambda _config: calls.append(1) or {"ok": True})

    result = cache.validate(config)
    result["mutated"] = True
    assert cache.validate(config) == {"ok": True}
    fingerprint[0] = ("second",)
    assert cache.validate(config) == {"ok": True}
    assert len(calls) == 2
    assert cache.to_meta() == {"hit_count": 1, "miss_count": 2, "failure_count": 0}


def test_invocation_validation_cache_does_not_cache_failures(monkeypatch) -> None:
    config = bridge.load_release_config()
    cache = bridge.InvocationValidationCache()
    calls = []

    def fail(_config):
        calls.append(1)
        raise bridge.VerifierGroundedRuntimeError("invalid runtime")

    monkeypatch.setattr(bridge, "_validate_runtime_files_uncached", fail)
    with pytest.raises(bridge.VerifierGroundedRuntimeError):
        cache.validate(config)
    with pytest.raises(bridge.VerifierGroundedRuntimeError):
        cache.validate(config)
    assert len(calls) == 2


def _materialize_runtime_files(config: bridge.ReleaseConfig, content: bytes) -> None:
    config.wheel_path.parent.mkdir(parents=True, exist_ok=True)
    config.wheel_path.write_bytes(content)
    config.runtime_python.parent.mkdir(parents=True, exist_ok=True)
    config.runtime_python.write_text("fixture runtime", encoding="utf-8")
    config.runtime_manifest.write_text(json.dumps({
        **config.identity,
        "source_commit": config.source_commit,
        "source_tag": config.source_tag,
        "wheel_path": str(config.wheel_path),
    }), encoding="utf-8")


def test_validation_cache_real_file_change_failure_and_recovery(monkeypatch, tmp_path) -> None:
    content = b"real fingerprint fixture"
    monkeypatch.setattr(bridge.runtime_paths, "data_root", tmp_path / "data")
    monkeypatch.setattr(bridge.runtime_paths, "project_state_root", tmp_path / "state")
    config = bridge.ReleaseConfig(
        package="fixture", version="1", source_commit="commit", source_tag="tag",
        wheel_filename="fixture.whl", wheel_sha256=hashlib.sha256(content).hexdigest(),
        wheel_size=len(content), tracks={},
    )
    _materialize_runtime_files(config, content)
    cache = bridge.InvocationValidationCache()
    assert cache.validate(config)["source_tag"] == "tag"
    assert cache.validate(config)["source_tag"] == "tag"

    config.runtime_manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(bridge.VerifierGroundedRuntimeError, match="does not match"):
        cache.validate(config)
    _materialize_runtime_files(config, content)
    assert cache.validate(config)["source_tag"] == "tag"
    assert cache.to_meta() == {"hit_count": 1, "miss_count": 3, "failure_count": 1}


def test_validation_cache_serializes_concurrent_misses(monkeypatch) -> None:
    config = bridge.load_release_config()
    cache = bridge.InvocationValidationCache()
    calls = []

    def validate(_config):
        calls.append(1)
        time.sleep(0.01)
        return {"ok": True}

    monkeypatch.setattr(bridge, "_validate_runtime_files_uncached", validate)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: cache.validate(config), range(16)))
    assert results == [{"ok": True}] * 16
    assert len(calls) == 1
    assert cache.to_meta() == {"hit_count": 15, "miss_count": 1, "failure_count": 0}


def test_validation_fingerprint_includes_release_configuration() -> None:
    config = bridge.load_release_config()
    changed = replace(config, source_tag="v0.9.2-reconfigured")
    assert bridge._validation_fingerprint(config) != bridge._validation_fingerprint(changed)
