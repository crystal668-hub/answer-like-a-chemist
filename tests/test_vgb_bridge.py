from __future__ import annotations

from unittest.mock import patch

import pytest

from benchmarking.runtime import vgb_bridge as bridge


def test_release_config_pins_version_hash_and_complete_inventory() -> None:
    config = bridge.load_release_config()

    assert config.version == "0.5.0"
    assert config.source_tag == "v0.5.0"
    assert config.source_commit == "0a9523176efb84e9d37163caf99bf7509789e823"
    assert config.wheel_sha256 == "fb99d63fef0fd45a43ed8a074be93c7963c947ec2e371fe27b84a52fc5bdb98e"
    assert config.wheel_size == 175381
    assert {name: track["task_count"] for name, track in config.tracks.items()} == {
        "property_calculation": 14,
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
    with patch.object(bridge, "_invoke_api") as invoke:
        with pytest.raises(bridge.VerifierGroundedRuntimeError, match="does not match"):
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


def test_load_public_sample_answers_calls_public_api_runtime() -> None:
    expected = [
        {"task_id": "property_calc_free_energy_001", "answer": 0.258031679, "unit": "kJ/mol"},
        {"task_id": "property_calc_crystal_phase_002", "answers": [{"property": "potential_energy_difference", "value": 0.079, "unit": "eV"}, {"property": "ambient_pressure_phase", "value": "alpha"}, {"property": "high_pressure_phase", "value": "beta"}]},
        {"task_id": "property_calc_003_hbond_count", "answer": 12, "unit": "count"},
        {"task_id": "property_calc_004_ir_top3_frequencies", "answers": [{"property": "frequency_1", "value": 1685.5562, "unit": "cm^-1"}, {"property": "frequency_2", "value": 1208.1036, "unit": "cm^-1"}, {"property": "frequency_3", "value": 1674.0688, "unit": "cm^-1"}]},
        {"task_id": "property_calc_005_crystal_density", "answer": 1.44728, "unit": "g/cm^3"},
        {"task_id": "property_calc_006_cocrystal_ratio", "answer": "1:1"},
        {"task_id": "property_calc_007_polymorph_free_energy_crossover", "answers": [{"property": "lower_free_energy_at_0k", "value": "FormC"}, {"property": "crossover_temperature", "value": 343.15, "unit": "K"}]},
        {"task_id": "property_calc_008_interaction_binding_energy", "answers": [{"property": "interaction_energy", "value": -69.04, "unit": "kcal/mol"}, {"property": "binding_energy", "value": -58.15, "unit": "kcal/mol"}]},
        {"task_id": "property_calc_009_homo_lumo_gap", "answer": 7.26, "unit": "eV"},
        {"task_id": "property_calc_010_hbond_distances", "answers": [{"property": "oh_bond_distance", "value": 1.029, "unit": "angstrom"}, {"property": "h_o_contact_distance", "value": 1.485, "unit": "angstrom"}]},
        {"task_id": "property_calc_011_accessible_pore_volume_ratio", "answer": 1.713, "unit": "ratio"},
        {"task_id": "property_calc_012_carboxyl_hydrogen_distance", "answer": 2.521, "unit": "angstrom"},
        {"task_id": "property_calc_013_halogen_bond_energy", "answer": -17.11, "unit": "kcal/mol"},
        {"task_id": "property_calc_014_bay069_pka", "answer": 5.7, "unit": "pKa"},
    ]
    with patch.object(bridge, "_invoke_api", return_value={"sample_answers": expected}) as invoke:
        result = bridge.load_public_sample_answers("property_calculation")

    assert result == expected
    assert invoke.call_args.args[1] == {
        "action": "sample_answers",
        "track": "property_calculation",
    }


def test_load_public_sample_answers_rejects_incomplete_pinned_inventory() -> None:
    with patch.object(
        bridge,
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
    ), pytest.raises(bridge.VerifierGroundedRuntimeError, match="inventory"):
        bridge.load_public_sample_answers("property_calculation")
