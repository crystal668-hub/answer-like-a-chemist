from __future__ import annotations

from unittest.mock import patch

import pytest

from benchmarking.scoring import verifier_grounded_runtime as runtime


def test_release_config_pins_version_hash_and_complete_inventory() -> None:
    config = runtime.load_release_config()

    assert config.version == "0.3.0"
    assert config.source_tag == "v0.3.0"
    assert config.source_commit == "89ed5b9d83547bea98f6eeac4a03a131e33e8b90"
    assert config.wheel_sha256 == "b93c18b818e8d19993e817de6439ccea910b36a8f386c551078b7c6b10420381"
    assert config.wheel_size == 143722
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
    expected = {"task_id": "rdkit_qed_max_001", "status": "scored", "scores": {"score": 0.5}}
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


def test_load_public_sample_answers_calls_public_api_runtime() -> None:
    expected = [
        {
            "task_id": "property_calc_free_energy_001",
            "answer": 0.258031679,
            "unit": "kJ/mol",
        },
        {
            "task_id": "property_calc_crystal_phase_002",
            "answers": [
                {"property": "potential_energy_difference", "value": 0.079, "unit": "eV"},
                {"property": "ambient_pressure_phase", "value": "alpha"},
                {"property": "high_pressure_phase", "value": "beta"},
            ],
        },
    ]
    with patch.object(runtime, "_invoke_api", return_value={"sample_answers": expected}) as invoke:
        result = runtime.load_public_sample_answers("property_calculation")

    assert result == expected
    assert invoke.call_args.args[1] == {
        "action": "sample_answers",
        "track": "property_calculation",
    }


def test_load_public_sample_answers_rejects_incomplete_pinned_inventory() -> None:
    with patch.object(
        runtime,
        "_invoke_api",
        return_value={
            "sample_answers": [
                {
                    "task_id": "property_calc_free_energy_001",
                    "answer": 0.258031679,
                    "unit": "kJ/mol",
                }
            ]
        },
    ):
        with pytest.raises(runtime.VerifierGroundedRuntimeError, match="inventory"):
            runtime.load_public_sample_answers("property_calculation")
