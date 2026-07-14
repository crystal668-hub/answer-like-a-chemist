from __future__ import annotations

from unittest.mock import patch

import pytest

from benchmarking.scoring import verifier_grounded_runtime as runtime


def test_release_config_pins_version_hash_and_complete_inventory() -> None:
    config = runtime.load_release_config()

    assert config.version == "0.1.1"
    assert config.source_tag == "v0.1.1"
    assert len(config.wheel_sha256) == 64
    assert {name: track["task_count"] for name, track in config.tracks.items()} == {
        "property_calculation": 2,
        "rdkit": 11,
        "xtb": 18,
    }
    assert all(track["task_count"] == len(track["task_ids"]) for track in config.tracks.values())


def test_runtime_environment_does_not_inherit_agent_python_paths(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("PYTHONPATH", "/agent/source")
    monkeypatch.setenv("VIRTUAL_ENV", "/agent/venv")

    env = runtime._runtime_env()

    assert env["PATH"] == "/usr/bin"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env


def test_evaluate_answer_rejects_unpinned_release_before_subprocess() -> None:
    with patch.object(runtime, "_invoke_api") as invoke:
        with pytest.raises(runtime.VerifierGroundedRuntimeError, match="does not match"):
            runtime.evaluate_answer(
                track="rdkit",
                task_id="rdkit_qed_max_001",
                answer_text="FINAL ANSWER: CCO",
                release_identity={"package": "wrong", "version": "0", "wheel_sha256": "0"},
            )
    invoke.assert_not_called()


def test_evaluate_answer_calls_public_api_runtime_with_track_and_task() -> None:
    config = runtime.load_release_config()
    expected = {"task_id": "rdkit_qed_max_001", "status": "ok", "scores": {"score": 0.5}}
    with patch.object(runtime, "_invoke_api", return_value=expected) as invoke:
        result = runtime.evaluate_answer(
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
