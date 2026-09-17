import unittest

from benchmarking.core.answer_processing import (
    AgentResponseParseError,
    extract_candidate_short_answer,
    parse_agent_json_response,
)
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.runtime.vgb_bridge import load_release_config
from benchmarking.scoring.errors import EvaluationError
from benchmarking.scoring.evaluators.generic import evaluate_generic_semantic
from benchmarking.scoring.evaluators.verifier_grounded import (
    evaluate_verifier_grounded,
    run_verifier_grounded_evaluation,
    validate_verifier_grounded_release,
)


class JudgeStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def evaluate_json(self, prompt: str) -> dict[str, object]:
        self.prompts.append(prompt)
        return dict(self.payload)


class BenchmarkEvaluatorTests(unittest.TestCase):
    def test_extract_candidate_short_answer_strips_markdown_final_answer_marker(self) -> None:
        self.assertEqual("B", extract_candidate_short_answer("Visible reasoning.\n**FINAL ANSWER:** B"))
        self.assertEqual("B", extract_candidate_short_answer("Visible reasoning.\n**FINAL ANSWER: B**"))

    def test_generic_semantic_uses_judge_full_answer_text(self) -> None:
        judge = JudgeStub({"correct": True, "score": 1.0, "rationale": "full answer contains the match"})
        record = BenchmarkRecord(
            record_id="generic-demo",
            dataset="custom",
            source_file="/tmp/custom.jsonl",
            eval_kind="generic_semantic",
            prompt="Name the molecule.",
            reference_answer="benzene",
            payload={},
        )

        result = evaluate_generic_semantic(
            record,
            short_answer_text="wrong-short-answer",
            full_response_text="The relevant final answer is benzene.",
            answer_text="The relevant final answer is benzene.",
            judge=judge,
        )

        self.assertTrue(result.passed)
        self.assertEqual("judge", result.details["method"])
        self.assertIn("The relevant final answer is benzene.", judge.prompts[0])
        self.assertNotIn("wrong-short-answer", judge.prompts[0])

    def test_verifier_grounded_returns_continuous_score_without_pass_threshold(self) -> None:
        record = BenchmarkRecord(
            record_id="rdkit-logp",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/verifier_grounded.jsonl",
            eval_kind="verifier_grounded",
            prompt="Propose one valid single-component small-molecule SMILES.",
            reference_answer="Verifier-grounded task; score is computed by local verifier scripts.",
            payload={
                "verifier_grounded": {
                    "release": {
                        "package": "verifier-grounded-benchmark",
                        "version": "0.2.0",
                        "wheel_sha256": "pinned",
                    },
                    "track": "rdkit",
                    "task_id": "rdkit_logp_window_003",
                }
            },
        )

        def verifier_runner(*, record, answer_text):
            self.assertEqual("Reasoning.\nFINAL ANSWER: c1ccccc1", answer_text)
            return {
                "task_id": "rdkit_logp_window_003",
                "status": "scored",
                "canonical_smiles": "c1ccccc1",
                "properties": {"logp": 1.6866},
                "scores": {
                    "score": 0.73,
                    "constraint_scores": [{"property": "logp", "score": 0.73}],
                },
                "failure_type": None,
                "message": None,
                "versions": {"rdkit": "2026.03.2"},
                "raw_answer": "Reasoning.\nFINAL ANSWER: c1ccccc1",
                "extracted_answer": "c1ccccc1",
            }

        result = evaluate_verifier_grounded(
            record,
            short_answer_text="c1ccccc1",
            full_response_text="Reasoning.\nFINAL ANSWER: c1ccccc1",
            answer_text="Reasoning.\nFINAL ANSWER: c1ccccc1",
            judge=object(),
            verifier_runner=verifier_runner,
        )

        self.assertEqual(0.73, result.score)
        self.assertEqual(0.73, result.normalized_score)
        self.assertIsNone(result.passed)
        self.assertEqual("verifier_score", result.primary_metric)
        self.assertEqual("isolated_wheel_api", result.details["method"])
        self.assertEqual("c1ccccc1", result.details["canonical_smiles"])
        self.assertEqual({"logp": 1.6866}, result.details["properties"])

    def test_verifier_grounded_parse_error_is_scored_zero_but_not_threshold_passed(self) -> None:
        record = BenchmarkRecord(
            record_id="rdkit-logp",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/verifier_grounded.jsonl",
            eval_kind="verifier_grounded",
            prompt="Propose one valid single-component small-molecule SMILES.",
            reference_answer="Verifier-grounded task; score is computed by local verifier scripts.",
            payload={
                "verifier_grounded": {
                    "release": {
                        "package": "verifier-grounded-benchmark",
                        "version": "0.2.0",
                        "wheel_sha256": "pinned",
                    },
                    "track": "rdkit",
                    "task_id": "rdkit_logp_window_003",
                }
            },
        )

        def verifier_runner(*, record, answer_text):
            return {
                "task_id": "rdkit_logp_window_003",
                "status": "scored",
                "failure_type": "parse_error",
                "message": "missing final answer line",
                "canonical_smiles": None,
                "properties": {},
                "scores": {"score": 0.0, "constraint_scores": []},
                "versions": {},
            }

        result = evaluate_verifier_grounded(
            record,
            short_answer_text="",
            full_response_text="No final marker.",
            answer_text="No final marker.",
            judge=object(),
            verifier_runner=verifier_runner,
        )

        self.assertEqual(0.0, result.score)
        self.assertIsNone(result.passed)
        self.assertEqual("parse_error", result.details["failure_type"])
        self.assertEqual("missing final answer line", result.details["message"])

    def test_verifier_grounded_infrastructure_error_is_not_converted_to_zero(self) -> None:
        record = BenchmarkRecord(
            record_id="rdkit_logp_window_003",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/verifier_grounded.jsonl",
            eval_kind="verifier_grounded",
            prompt="Propose one valid single-component small-molecule SMILES.",
            reference_answer="No reference answer is exposed.",
            payload={
                "verifier_grounded": {
                    "release": {
                        "package": "verifier-grounded-benchmark",
                        "version": "0.2.0",
                        "wheel_sha256": "pinned",
                    },
                    "track": "rdkit",
                    "task_id": "rdkit_logp_window_003",
                }
            },
        )

        def verifier_runner(*, record, answer_text):
            return {
                "task_id": "rdkit_logp_window_003",
                "status": "error",
                "failure_scope": "infrastructure",
                "failure_type": "verifier_timeout",
                "message": "verifier timed out",
                "properties": {},
                "scores": {"score": None, "constraint_scores": []},
                "versions": {},
            }

        with self.assertRaisesRegex(EvaluationError, "verifier_timeout"):
            evaluate_verifier_grounded(
                record,
                short_answer_text="c1ccccc1",
                full_response_text="FINAL ANSWER: c1ccccc1",
                answer_text="FINAL ANSWER: c1ccccc1",
                judge=object(),
                verifier_runner=verifier_runner,
            )

    def test_verifier_grounded_rejects_record_task_mismatch_before_runtime(self) -> None:
        record = BenchmarkRecord(
            record_id="rdkit_qed_max_001",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/verifier_grounded.jsonl",
            eval_kind="verifier_grounded",
            prompt="Q",
            reference_answer="No reference answer is exposed.",
            payload={
                "verifier_grounded": {
                    "release": {
                        "package": "verifier-grounded-benchmark",
                        "version": "0.2.0",
                        "wheel_sha256": "pinned",
                    },
                    "track": "rdkit",
                    "task_id": "rdkit_sa_min_002",
                }
            },
        )

        with self.assertRaisesRegex(EvaluationError, "does not match record_id"):
            run_verifier_grounded_evaluation(record=record, answer_text="FINAL ANSWER: CCO")

    def test_verifier_grounded_rejects_release_mismatch_at_invocation_start(self) -> None:
        record = BenchmarkRecord(
            record_id="rdkit_qed_max_001",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/verifier_grounded.jsonl",
            eval_kind="verifier_grounded",
            prompt="Q",
            reference_answer="No reference answer is exposed.",
            payload={
                "verifier_grounded": {
                    "release": {
                        "package": "verifier-grounded-benchmark",
                        "version": "stale",
                        "wheel_sha256": "stale",
                    },
                    "track": "rdkit",
                    "task_id": "rdkit_qed_max_001",
                }
            },
        )

        with self.assertRaisesRegex(EvaluationError, "invocation verifier release"):
            validate_verifier_grounded_release(
                record,
                release_config=load_release_config(),
            )

    def test_parse_agent_json_response_repairs_unescaped_latex_backslashes(self) -> None:
        reply = (
            '{"correct":false,"score":0.0,'
            '"rationale":"The candidate states \\(K_M = K_s + [S]^2/J_s\\), which differs.",'
            '"expected_answer":"KM=Ks1+Js[S]2",'
            '"candidate_answer":"K_M = K_s + [S]^2/J_s"}'
        )

        parsed = parse_agent_json_response(reply)

        self.assertEqual(False, parsed["correct"])
        self.assertIn(r"\(K_M", parsed["rationale"])

    def test_parse_agent_json_response_rejects_empty_and_non_object_payloads(self) -> None:
        with self.assertRaisesRegex(AgentResponseParseError, "empty agent response"):
            parse_agent_json_response("  ")
        with self.assertRaisesRegex(AgentResponseParseError, "must be an object"):
            parse_agent_json_response('["not", "an", "object"]')


if __name__ == "__main__":
    unittest.main()
