import unittest

from benchmarking.datasets import BenchmarkRecord
from benchmarking.evaluators import (
    evaluate_chembench_open_ended,
    evaluate_frontierscience_olympiad,
    evaluate_generic_semantic,
    evaluate_hle,
    parse_frontierscience_research_rubric,
    parse_superchem_option_answer,
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
