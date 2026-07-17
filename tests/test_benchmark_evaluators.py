import unittest

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.scoring.evaluators import (
    EvaluationError,
    evaluate_chembench_open_ended,
    evaluate_frontierscience_olympiad,
    evaluate_frontierscience_research,
    evaluate_generic_semantic,
    evaluate_hle,
    evaluate_verifier_grounded,
    extract_candidate_short_answer,
    parse_frontierscience_research_rubric,
    parse_superchem_option_answer,
    run_verifier_grounded_evaluation,
    safe_json_extract,
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

    def test_chembench_open_ended_numeric_match_uses_judge_full_answer_text(self) -> None:
        judge = JudgeStub({"correct": True, "score": 1.0, "rationale": "matches"})
        record = BenchmarkRecord(
            record_id="demo",
            dataset="chembench",
            source_file="/tmp/demo.jsonl",
            eval_kind="chembench_open_ended",
            prompt="What is 2+2?",
            reference_answer="4",
            payload={"target": "4", "preferred_score": "mae"},
        )

        result = evaluate_chembench_open_ended(
            record,
            short_answer_text="wrong-short-answer",
            full_response_text="Reasoning with necessary context\nFINAL ANSWER: 4",
            answer_text="Reasoning with necessary context\nFINAL ANSWER: 4",
            judge=judge,
        )

        self.assertTrue(result.passed)
        self.assertEqual(1.0, result.score)
        self.assertEqual(1.0, result.normalized_score)
        self.assertEqual("judge", result.details["method"])
        self.assertEqual("Reasoning with necessary context\nFINAL ANSWER: 4", result.details["candidate_answer_text"])
        self.assertEqual(1, len(judge.prompts))
        self.assertIn("Reasoning with necessary context", judge.prompts[0])
        self.assertNotIn("wrong-short-answer", judge.prompts[0])

    def test_frontierscience_olympiad_always_uses_judge(self) -> None:
        judge = JudgeStub({"correct": True, "score": 1.0, "rationale": "matches"})
        record = BenchmarkRecord(
            record_id="fs-demo",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="What is 6 x 7?",
            reference_answer="42",
            payload={"track": "olympiad"},
        )

        result = evaluate_frontierscience_olympiad(
            record,
            short_answer_text="42",
            full_response_text="FINAL ANSWER: 42",
            judge=judge,
        )

        self.assertTrue(result.passed)
        self.assertEqual("judge", result.details["method"])
        self.assertEqual(1, len(judge.prompts))

    def test_frontierscience_olympiad_iupac_reference_uses_judge_not_numeric_heuristic(self) -> None:
        judge = JudgeStub({"correct": True, "score": 1.0, "rationale": "matches"})
        record = BenchmarkRecord(
            record_id="fs-chem-olympiad-23cb57a5",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Provide molecule X.",
            reference_answer=(
                "Molecule X is <INCHI>InChI=1S/C7H6F3N/c8-7(9,10)5-2-1-3-6(11)4-5/h1-4H,11H2"
                "</INCHI>, <SMILES>C1=CC(=CC(=C1)N)C(F)(F)F</SMILES>, "
                "<IUPAC>3-(trifluoromethyl)aniline</IUPAC>."
            ),
            payload={"track": "olympiad"},
        )

        result = evaluate_frontierscience_olympiad(
            record,
            short_answer_text="3-(trifluoromethyl)aniline",
            full_response_text="FINAL ANSWER: 3-(trifluoromethyl)aniline",
            judge=judge,
        )

        self.assertTrue(result.passed)
        self.assertEqual("judge", result.details["method"])
        self.assertEqual(1, len(judge.prompts))
        self.assertIn("3-(trifluoromethyl)aniline", result.details["candidate_answer_text"])

    def test_frontierscience_research_partial_credit_does_not_pass(self) -> None:
        judge = JudgeStub(
            {
                "items": [
                    {"index": 1, "awarded": 1.0, "max_points": 1.0, "met": True, "rationale": "covered"},
                    {"index": 2, "awarded": 0.0, "max_points": 1.0, "met": False, "rationale": "missing"},
                ],
                "total_awarded": 1.0,
                "max_points": 2.0,
                "summary": "partial credit only",
            }
        )
        record = BenchmarkRecord(
            record_id="fs-research-demo",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_research",
            prompt="Design an experiment and justify it.",
            reference_answer=(
                "Points: 1, Item: Identify the relevant catalyst.\n"
                "Points: 1, Item: Justify the proposed mechanism."
            ),
            payload={"track": "research"},
        )

        result = evaluate_frontierscience_research(
            record,
            short_answer_text="partial",
            full_response_text="The answer identifies the catalyst only.",
            answer_text="The answer identifies the catalyst only.",
            judge=judge,
        )

        self.assertEqual(1.0, result.score)
        self.assertEqual(2.0, result.max_score)
        self.assertEqual(0.5, result.normalized_score)
        self.assertFalse(result.passed)
        self.assertEqual("rubric_points", result.primary_metric)

    def test_frontierscience_research_full_credit_passes(self) -> None:
        judge = JudgeStub(
            {
                "items": [
                    {"index": 1, "awarded": 1.0, "max_points": 1.0, "met": True, "rationale": "covered"},
                    {"index": 2, "awarded": 1.0, "max_points": 1.0, "met": True, "rationale": "covered"},
                ],
                "total_awarded": 2.0,
                "max_points": 2.0,
                "summary": "full credit",
            }
        )
        record = BenchmarkRecord(
            record_id="fs-research-full-credit",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_research",
            prompt="Design an experiment and justify it.",
            reference_answer=(
                "Points: 1, Item: Identify the relevant catalyst.\n"
                "Points: 1, Item: Justify the proposed mechanism."
            ),
            payload={"track": "research"},
        )

        result = evaluate_frontierscience_research(
            record,
            short_answer_text="complete",
            full_response_text="The answer covers both rubric items.",
            answer_text="The answer covers both rubric items.",
            judge=judge,
        )

        self.assertEqual(2.0, result.score)
        self.assertEqual(1.0, result.normalized_score)
        self.assertTrue(result.passed)

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

    def test_hle_uses_official_judge_shape_and_preserves_confidence(self) -> None:
        judge = JudgeStub(
            {
                "extracted_final_answer": "PCC",
                "reasoning": "The extracted answer matches.",
                "correct": "yes",
                "confidence": 87,
            }
        )
        record = BenchmarkRecord(
            record_id="hle-demo",
            dataset="hle",
            source_file="/tmp/hle.jsonl",
            eval_kind="hle",
            prompt="Which reagent oxidizes a primary alcohol to an aldehyde?",
            reference_answer="PCC",
            payload={"answer_type": "short-answer", "category": "Chemistry"},
        )

        result = evaluate_hle(
            record,
            short_answer_text="wrong-short-answer",
            full_response_text="Explanation: brief\nAnswer: PCC\nConfidence: 87%",
            answer_text="Explanation: brief\nAnswer: PCC\nConfidence: 87%",
            judge=judge,
        )

        self.assertTrue(result.passed)
        self.assertEqual(1.0, result.score)
        self.assertEqual("hle_judge_accuracy", result.primary_metric)
        self.assertEqual(87, result.details["confidence"])
        self.assertIn("extracted_final_answer", judge.prompts[0])
        self.assertIn("[correct_answer]: PCC", judge.prompts[0])
        self.assertIn("Explanation: brief", judge.prompts[0])
        self.assertNotIn("wrong-short-answer", judge.prompts[0])

    def test_parse_helpers_cover_research_rubric_and_superchem_options(self) -> None:
        rubric = "Points: 1.5, Item: Identify the limiting reagent.\nExplain the stoichiometric basis."
        items = parse_frontierscience_research_rubric(rubric)
        self.assertEqual([{"points": 1.5, "description": "Identify the limiting reagent.\nExplain the stoichiometric basis."}], items)

        self.assertEqual("A|D", parse_superchem_option_answer("Option A and D are correct.", valid_options=("A", "B", "C", "D")))

    def test_safe_json_extract_repairs_unescaped_latex_backslashes(self) -> None:
        reply = (
            '{"correct":false,"score":0.0,'
            '"rationale":"The candidate states \\(K_M = K_s + [S]^2/J_s\\), which differs.",'
            '"expected_answer":"KM=Ks1+Js[S]2",'
            '"candidate_answer":"K_M = K_s + [S]^2/J_s"}'
        )

        parsed = safe_json_extract(reply)

        self.assertEqual(False, parsed["correct"])
        self.assertIn(r"\(K_M", parsed["rationale"])


if __name__ == "__main__":
    unittest.main()
