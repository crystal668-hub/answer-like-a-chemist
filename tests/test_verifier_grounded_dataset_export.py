from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import runtime_paths
from benchmarking.core.datasets import load_records
from scripts.export_verifier_grounded_rdkit_dataset import export_rdkit_dataset, export_xtb_xyz_dataset


def test_export_rdkit_dataset_materializes_openclaw_jsonl(tmp_path: Path) -> None:
    source_root = Path("/Users/xutao/verifier-grounded-benchmark")
    output_path = tmp_path / "verifier_grounded_rdkit.jsonl"

    export_rdkit_dataset(source_root=source_root, output_path=output_path)

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 10
    first = rows[0]
    assert first["id"] == "rdkit_qed_max_001"
    assert first["eval_kind"] == "verifier_grounded"
    assert first["answer"] == "Verifier-grounded task; score is computed by local verifier scripts."
    assert "FINAL ANSWER:" in first["prompt"]
    assert first["verifier_grounded"]["source_repo"] == str(source_root)
    assert first["verifier_grounded"]["task_set"] == "rdkit_baseline"
    assert first["verifier_grounded"]["task"]["task_id"] == "rdkit_qed_max_001"
    assert first["verifier_grounded"]["task"]["answer_schema"]["value_type"] == "smiles"
    assert any(spec["verifier_id"] == "rdkit_qed_v1" for spec in first["verifier_grounded"]["verifier_specs"])


def test_export_rdkit_dataset_cli_runs_as_script_path(tmp_path: Path) -> None:
    output_path = tmp_path / "verifier_grounded_rdkit.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/export_verifier_grounded_rdkit_dataset.py",
            "--source-root",
            "/Users/xutao/verifier-grounded-benchmark",
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.is_file()


def test_export_xtb_xyz_dataset_materializes_openclaw_jsonl(tmp_path: Path) -> None:
    source_root = Path("/Users/xutao/verifier-grounded-benchmark")
    output_path = tmp_path / "verifier_grounded_xtb_xyz.jsonl"

    export_xtb_xyz_dataset(source_root=source_root, output_path=output_path)

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 13
    first = rows[0]
    assert first["id"] == "xtb_gap_window_001"
    assert first["eval_kind"] == "verifier_grounded"
    assert first["answer"] == "Verifier-grounded task; score is computed by local verifier scripts."
    assert "```xyz" in first["prompt"]
    assert first["verifier_grounded"]["source_repo"] == str(source_root)
    assert first["verifier_grounded"]["task_set"] == "xtb_xyz"
    assert first["verifier_grounded"]["task"]["task_id"] == "xtb_gap_window_001"
    assert first["verifier_grounded"]["task"]["answer_schema"]["value_type"] == "xyz"
    verifier_ids = {spec["verifier_id"] for spec in first["verifier_grounded"]["verifier_specs"]}
    assert {"xtb_gap_gfn2_v1", "xtb_relaxation_energy_gfn2_v1"} <= verifier_ids
    max_spec_timeout = max(float(spec["timeout_seconds"]) for spec in first["verifier_grounded"]["verifier_specs"])
    assert first["verifier_grounded"]["timeout_seconds"] > max_spec_timeout

    by_id = {row["id"]: row for row in rows}
    assert {
        "xtb_lumo_min_008",
        "xtb_polarizability_dipole_opt_009",
        "xtb_solvation_selectivity_alpb_010",
        "xtb_electrophilicity_max_011",
        "xtb_fukui_carbon_site_012",
        "xtb_hessian_thermo_stability_013",
    } <= set(by_id)
    hessian_specs = by_id["xtb_hessian_thermo_stability_013"]["verifier_grounded"]["verifier_specs"]
    assert [spec["verifier_id"] for spec in hessian_specs] == [
        "xtb_hessian_thermo_gfn2_v1",
        "xtb_hessian_thermo_gfn2_v1",
        "xtb_relaxation_energy_gfn2_v1",
    ]
    assert by_id["xtb_hessian_thermo_stability_013"]["verifier_grounded"]["timeout_seconds"] == 1560.0


def test_export_xtb_xyz_dataset_cli_runs_as_script_path(tmp_path: Path) -> None:
    output_path = tmp_path / "verifier_grounded_xtb_xyz.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/export_verifier_grounded_rdkit_dataset.py",
            "--task-set",
            "xtb_xyz",
            "--dataset-name",
            "verifier_grounded_xtb_xyz",
            "--source-root",
            "/Users/xutao/verifier-grounded-benchmark",
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["verifier_grounded"]["task_set"] == "xtb_xyz"


def test_default_xtb_xyz_formal_benchmark_dataset_is_loadable() -> None:
    dataset_path = (
        runtime_paths.benchmarks_root
        / "verifier_grounded_xtb_xyz"
        / "data"
        / "verifier_grounded_xtb_xyz.jsonl"
    )

    records = load_records([dataset_path])

    assert len(records) == 13
    assert records[0].dataset == "verifier_grounded_xtb_xyz"
    assert records[0].grading.kind == "verifier_grounded"
    assert records[0].grading.subset == "verifier_grounded_xtb_xyz"
    assert records[-1].record_id == "xtb_hessian_thermo_stability_013"
