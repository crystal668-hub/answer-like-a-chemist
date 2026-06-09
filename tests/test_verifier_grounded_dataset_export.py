from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
    assert len(rows) == 7
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
