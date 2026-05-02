from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "chemqa-review"
    / "scripts"
    / "chemqa_artifact_flow.py"
)
SPEC = importlib.util.spec_from_file_location("chemqa_artifact_flow", MODULE_PATH)
chemqa_artifact_flow = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = chemqa_artifact_flow
SPEC.loader.exec_module(chemqa_artifact_flow)


class ChemQAArtifactFlowProjectionTests(unittest.TestCase):
    def test_numeric_short_answer_repairs_setup_sentence_from_summary_final_value(self) -> None:
        candidate = {
            "owner": "proposer-1",
            "direct_answer": "After mixing 100 mL of 0.250 M NaF with 100 mL of 0.0250 M Sr(NO3)2, the total volume is 200 mL.",
            "summary": (
                "After mixing 100 mL of 0.250 M NaF with 100 mL of 0.0250 M Sr(NO3)2, "
                "the total volume is 200 mL. Initial concentrations: [F-] = 0.125 M, "
                "[Sr2+] = 0.0125 M. Using Ksp = [Sr2+][F-]^2, residual [Sr2+] = "
                "4.33e-7 M. Mass of dissolved Sr2+ = (4.33e-7 mol/L * 0.200 L) "
                "* 87.6 g/mol = 7.59 μg."
            ),
            "reasoning_summary": "Correct Ksp calculation gives 7.59 μg.",
        }

        result = chemqa_artifact_flow.validate_candidate_artifact(
            candidate,
            answer_kind="numeric_short_answer",
            run_id="sr-demo",
        )

        self.assertTrue(result.valid, result.errors)
        payload = result.artifact["payload"]
        self.assertEqual("7.59 μg", payload["evaluator_answer"])
        self.assertEqual("7.59 μg", payload["display_answer"])
        self.assertIn("7.59 μg", payload["full_answer"])
        self.assertEqual("numeric_final_answer_extraction", payload["projection_metadata"]["repair"])

    def test_finalization_writes_repaired_numeric_evaluator_answer(self) -> None:
        protocol = {
            "terminal_state": "completed",
            "acceptance_status": "accepted",
            "candidate_submission": {
                "owner": "proposer-1",
                "direct_answer": "After mixing 100 mL of 0.250 M NaF with 100 mL of 0.0250 M Sr(NO3)2, the total volume is 200 mL.",
                "summary": "Mass of dissolved Sr2+ = (4.33e-7 mol/L * 0.200 L) * 87.6 g/mol = 7.59 μg.",
                "reasoning_summary": "Correct Ksp calculation gives 7.59 μg.",
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = chemqa_artifact_flow.finalization_from_protocol(
                protocol=protocol,
                output_dir=Path(tmpdir),
                run_id="sr-final-demo",
                answer_kind="numeric_short_answer",
            )

            self.assertEqual("completed", result.terminal_state)
            final = result.qa_result["final_answer"]
            self.assertEqual("7.59 μg", final["direct_answer"])
            self.assertEqual("7.59 μg", final["answer"])
            self.assertEqual("7.59 μg", final["value"])
            self.assertEqual("numeric_final_answer_extraction", final["projection_metadata"]["repair"])

    def test_protocol_answer_revision_updates_final_evaluator_answer(self) -> None:
        protocol = {
            "terminal_state": "completed",
            "acceptance_status": "accepted",
            "candidate_submission": {
                "owner": "proposer-1",
                "direct_answer": "The mass gain of the Cu strip (1.52 g) from Cu + 2Ag+ -> Cu2+ + 2Ag displacement",
                "summary": "Initial incorrect calculation.",
            },
            "proposer_trajectory": {
                "rebuttals": [
                    {
                        "rebuttal_round": 1,
                        "payload": {
                            "artifact_kind": "rebuttal",
                            "phase": "rebuttal",
                            "owner": "proposer-1",
                            "mode": "answer_revision",
                            "response_summary": "Corrected the algebra error.",
                            "updated_answer": {
                                "evaluator_answer": "NaCl: 69.9%, KCl: 30.1%",
                                "display_answer": "NaCl 69.9%, KCl 30.1% by mass",
                                "full_answer": "The mixture contains 17.48 g NaCl (69.9%) and 7.52 g KCl (30.1%) by mass.",
                            },
                        },
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = chemqa_artifact_flow.finalization_from_protocol(
                protocol=protocol,
                output_dir=Path(tmpdir),
                run_id="nacl-kcl-demo",
                answer_kind="multi_part_research_answer",
            )

            final = result.qa_result["final_answer"]
            self.assertEqual("NaCl: 69.9%, KCl: 30.1%", final["direct_answer"])
            self.assertEqual("NaCl 69.9%, KCl 30.1% by mass", final["display_answer"])
            self.assertIn("17.48 g NaCl", final["full_answer"])

    def test_protocol_numeric_answer_revision_repairs_setup_sentence_from_updated_full_answer(self) -> None:
        protocol = {
            "terminal_state": "completed",
            "acceptance_status": "accepted",
            "candidate_submission": {
                "owner": "proposer-1",
                "direct_answer": "42",
                "summary": "Initial answer.",
            },
            "proposer_trajectory": {
                "rebuttals": [
                    {
                        "rebuttal_round": 1,
                        "payload": {
                            "artifact_kind": "rebuttal",
                            "phase": "rebuttal",
                            "owner": "proposer-1",
                            "mode": "answer_revision",
                            "response_summary": "Corrected the projection.",
                            "updated_answer": {
                                "evaluator_answer": "After mixing 100 mL of 0.250 M NaF with 100 mL of 0.0250 M Sr(NO3)2, the total volume is 200 mL.",
                                "display_answer": "Setup calculation",
                                "full_answer": "Mass of dissolved Sr2+ = (4.33e-7 mol/L * 0.200 L) * 87.6 g/mol = 7.59 μg.",
                            },
                        },
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = chemqa_artifact_flow.finalization_from_protocol(
                protocol=protocol,
                output_dir=Path(tmpdir),
                run_id="numeric-revision-demo",
                answer_kind="numeric_short_answer",
            )

            final = result.qa_result["final_answer"]
            self.assertEqual("7.59 μg", final["direct_answer"])
            self.assertEqual("numeric_final_answer_extraction", final["projection_metadata"]["repair"])

    def test_response_only_rebuttal_does_not_update_answer_even_if_summary_claims_fix(self) -> None:
        protocol = {
            "terminal_state": "completed",
            "acceptance_status": "accepted",
            "candidate_submission": {
                "owner": "proposer-1",
                "direct_answer": "42",
                "summary": "Original answer.",
            },
            "proposer_trajectory": {
                "rebuttals": [
                    {
                        "rebuttal_round": 1,
                        "payload": {
                            "artifact_kind": "rebuttal",
                            "phase": "rebuttal",
                            "owner": "proposer-1",
                            "mode": "response_only",
                            "response_summary": "Fixed direct_answer to 7.59 μg.",
                            "updated_answer": None,
                            "updated_direct_answer": None,
                        },
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            result = chemqa_artifact_flow.finalization_from_protocol(
                protocol=protocol,
                output_dir=Path(tmpdir),
                run_id="response-only-demo",
                answer_kind="numeric_short_answer",
            )

            self.assertEqual("42", result.qa_result["final_answer"]["direct_answer"])


if __name__ == "__main__":
    unittest.main()
