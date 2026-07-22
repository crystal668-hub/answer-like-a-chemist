from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from benchmarking.core.datasets import BenchmarkRecord, GradingSpec, load_records
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.registry import EVALUATORS, evaluate_record, register_evaluator


class BenchmarkDatasetsTests(unittest.TestCase):
    def test_load_records_builds_chembench_grading_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chembench" / "data" / "sample.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "id": "chem-1",
                        "prompt": "Q",
                        "target": "42",
                        "eval_kind": "chembench_open_ended",
                        "relative_tolerance": 0.1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            record = load_records([path])[0]

            self.assertEqual("chembench_open_ended", record.grading.kind)
            self.assertEqual("42", record.grading.reference_answer)
            self.assertEqual(0.1, record.grading.config["relative_tolerance"])

    def test_load_records_builds_hle_chemistry_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "hle" / "data" / "hle_chemistry_pool.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "id": "hle-chemistry-demo",
                        "question": "Which reagent oxidizes a primary alcohol to an aldehyde?",
                        "answer": "PCC",
                        "eval_kind": "hle",
                        "answer_type": "short-answer",
                        "category": "Chemistry",
                        "raw_subject": "Organic chemistry",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            record = load_records([path])[0]

            self.assertEqual("hle", record.dataset)
            self.assertEqual("hle", record.grading.kind)
            self.assertEqual("hle_chemistry", record.grading.subset)
            self.assertEqual("short-answer", record.grading.config["answer_type"])
            self.assertEqual("Chemistry", record.grading.config["category"])

    def test_load_records_builds_verifier_grounded_grading_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "verifier_grounded_rdkit" / "data" / "sample.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            verifier_config = {
                "answer_schema": {
                    "format": "final_answer_line",
                    "final_answer_prefix": "FINAL ANSWER:",
                    "value_type": "smiles",
                },
                "release": {
                    "package": "verifier-grounded-benchmark",
                    "version": "0.1.1",
                    "wheel_sha256": "pinned",
                },
                "track": "rdkit",
                "task_id": "rdkit_logp_window_003",
            }
            path.write_text(
                json.dumps(
                    {
                        "id": "rdkit_logp_window_003",
                        "prompt": "Propose one valid SMILES.",
                        "answer": "Verifier-grounded task; score is computed by local verifier scripts.",
                        "eval_kind": "verifier_grounded",
                        "verifier_grounded": verifier_config,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            record = load_records([path])[0]

            self.assertEqual("verifier_grounded_rdkit", record.dataset)
            self.assertEqual("verifier_grounded", record.grading.kind)
            self.assertEqual("verifier_grounded_rdkit", record.grading.subset)
            self.assertEqual(verifier_config, record.grading.config["verifier_grounded"])

    def test_load_records_builds_xtb_xyz_verifier_grounded_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "verifier_grounded_xtb_xyz" / "data" / "sample.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            verifier_config = {
                "answer_schema": {
                    "format": "final_answer_block",
                    "final_answer_prefix": "FINAL ANSWER:",
                    "value_type": "xyz",
                    "fence_language": "xyz",
                },
                "release": {
                    "package": "verifier-grounded-benchmark",
                    "version": "0.1.1",
                    "wheel_sha256": "pinned",
                },
                "track": "xtb",
                "task_id": "xtb_gap_window_001",
                "timeout_seconds": 540.0,
            }
            path.write_text(
                json.dumps(
                    {
                        "id": "xtb_gap_window_001",
                        "prompt": "Propose one neutral closed-shell small molecule as an XYZ geometry.",
                        "answer": "Verifier-grounded task; score is computed by local verifier scripts.",
                        "eval_kind": "verifier_grounded",
                        "verifier_grounded": verifier_config,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            record = load_records([path])[0]

            self.assertEqual("verifier_grounded_xtb_xyz", record.dataset)
            self.assertEqual("verifier_grounded", record.grading.kind)
            self.assertEqual("verifier_grounded_xtb_xyz", record.grading.subset)
            self.assertEqual(verifier_config, record.grading.config["verifier_grounded"])

    def test_load_records_missing_prompt_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chembench" / "data" / "sample.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "id": "chem-1",
                        "target": "42",
                        "eval_kind": "chembench_open_ended",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Missing prompt/problem field"):
                load_records([path])

    def test_load_records_missing_answer_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chembench" / "data" / "sample.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "id": "chem-1",
                        "prompt": "Q",
                        "eval_kind": "chembench_open_ended",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Missing answer/target field"):
                load_records([path])

    def test_evaluate_record_uses_registry_dispatch(self) -> None:
        calls: list[tuple[str, str, str, object]] = []

        def evaluator(
            record: BenchmarkRecord,
            *,
            short_answer_text: str,
            full_response_text: str,
            answer_text: str = "",
            judge: object,
        ) -> dict[str, object]:
            calls.append((record.record_id, short_answer_text, answer_text, judge))
            return {"ok": True, "full_response_text": full_response_text, "answer_text": answer_text}

        saved = dict(EVALUATORS)
        try:
            register_evaluator("unit_test_eval_kind", evaluator)
            record = BenchmarkRecord(
                record_id="chem-1",
                dataset="chembench",
                source_file="/tmp/sample.jsonl",
                prompt="Q",
                grading=GradingSpec(
                    kind="unit_test_eval_kind",
                    reference_answer="42",
                    subset="chembench",
                    config={},
                ),
                raw_payload={"id": "chem-1"},
            )

            result = evaluate_record(
                record,
                short_answer_text="42",
                full_response_text="FINAL ANSWER: 42",
                answer_text="FULL ANSWER: 42",
                judge=object(),
            )

            self.assertEqual({"ok": True, "full_response_text": "FINAL ANSWER: 42", "answer_text": "FULL ANSWER: 42"}, result)
            self.assertEqual(1, len(calls))
            self.assertEqual("chem-1", calls[0][0])
            self.assertEqual("42", calls[0][1])
            self.assertEqual("FULL ANSWER: 42", calls[0][2])
            self.assertIn("unit_test_eval_kind", EVALUATORS)
        finally:
            EVALUATORS.clear()
            EVALUATORS.update(saved)

    def test_benchmark_record_keeps_compatibility_properties(self) -> None:
        payload = {"id": "chem-1", "target": "42"}
        record = BenchmarkRecord(
            record_id="chem-1",
            dataset="chembench",
            source_file="/tmp/sample.jsonl",
            prompt="Q",
            grading=GradingSpec(
                kind="chembench_open_ended",
                reference_answer="42",
                subset="chembench",
                config={"relative_tolerance": 0.1},
            ),
            raw_payload=payload,
        )

        self.assertEqual("chembench_open_ended", record.eval_kind)
        self.assertEqual("42", record.reference_answer)
        self.assertEqual(payload, record.payload)

    def test_benchmark_record_asdict_preserves_legacy_shape(self) -> None:
        payload = {"id": "chem-1", "target": "42", "options": {"A": "x"}}
        record = BenchmarkRecord(
            record_id="chem-1",
            dataset="chembench",
            source_file="/tmp/sample.jsonl",
            prompt="Q",
            eval_kind="chembench_open_ended",
            reference_answer="42",
            payload=payload,
        )

        serialized = asdict(record)

        self.assertEqual("chembench_open_ended", serialized["eval_kind"])
        self.assertEqual("42", serialized["reference_answer"])
        self.assertEqual(payload, serialized["payload"])
        self.assertEqual(
            {"record_id", "dataset", "source_file", "eval_kind", "prompt", "reference_answer", "payload"},
            set(serialized),
        )

    def test_benchmark_record_payload_and_grading_config_are_deep_copied(self) -> None:
        payload = {"id": "chem-1", "target": "42", "options": {"A": "x"}}
        record = BenchmarkRecord(
            record_id="chem-1",
            dataset="chembench",
            source_file="/tmp/sample.jsonl",
            prompt="Q",
            eval_kind="chembench_open_ended",
            reference_answer="42",
            payload=payload,
        )

        record.payload["options"]["A"] = "mutated payload"
        self.assertEqual("x", record.grading.config["options"]["A"])

        record.grading.config["options"]["A"] = "mutated config"
        self.assertEqual("mutated payload", record.payload["options"]["A"])
        self.assertEqual("x", payload["options"]["A"])

    def test_evaluate_record_unknown_kind_without_generic_fallback_raises_registry_error(self) -> None:
        record = BenchmarkRecord(
            record_id="chem-1",
            dataset="chembench",
            source_file="/tmp/sample.jsonl",
            prompt="Q",
            grading=GradingSpec(
                kind="missing_eval_kind",
                reference_answer="42",
                subset="chembench",
                config={},
            ),
            raw_payload={"id": "chem-1"},
        )
        saved = dict(EVALUATORS)
        try:
            EVALUATORS.clear()
            with self.assertRaises(EvaluationRegistryError):
                evaluate_record(
                    record,
                    short_answer_text="42",
                    full_response_text="FINAL ANSWER: 42",
                    judge=object(),
                )
        finally:
            EVALUATORS.clear()
            EVALUATORS.update(saved)


if __name__ == "__main__":
    unittest.main()
