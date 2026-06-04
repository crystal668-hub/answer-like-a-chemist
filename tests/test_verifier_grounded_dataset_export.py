from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.export_verifier_grounded_rdkit_dataset import export_rdkit_dataset


def test_export_rdkit_dataset_materializes_openclaw_jsonl(tmp_path: Path) -> None:
    source_root = Path("/Users/xutao/verifier-grounded-benchmark")
    output_path = tmp_path / "verifier_grounded_rdkit.jsonl"

    export_rdkit_dataset(source_root=source_root, output_path=output_path)

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12
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
