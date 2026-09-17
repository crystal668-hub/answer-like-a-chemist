from pathlib import Path

from benchmarking.core.records import BenchmarkRecord
from benchmarking.runtime.bundles import ensure_runtime_bundle


def test_vgb_records_do_not_materialize_input_bundles(tmp_path: Path) -> None:
    record = BenchmarkRecord(
        record_id="rdkit_qed_max_001",
        track="rdkit",
        source_file="fixture",
        eval_kind="verifier_grounded",
        prompt="Question?",
        reference_answer="placeholder",
    )

    assert ensure_runtime_bundle(record, bundle_root=tmp_path) is None
