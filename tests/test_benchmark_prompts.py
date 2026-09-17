from __future__ import annotations

import unittest

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.service.single.prompts import build_single_llm_prompt
from benchmarking.service.single.runner import validate_candidate_answer_contract


class BenchmarkPromptsTests(unittest.TestCase):
    XYZ_ANSWER_SCHEMA = {
        "format": "final_answer_block",
        "final_answer_prefix": "FINAL ANSWER:",
        "value_type": "xyz",
        "fence_language": "xyz",
    }

    def test_verifier_grounded_prompt_uses_official_prompt_without_schema_repetition(self) -> None:
        record = BenchmarkRecord(
            record_id="rdkit-logp",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/verifier_grounded.jsonl",
            eval_kind="verifier_grounded",
            prompt="Propose one valid single-component small-molecule SMILES.",
            reference_answer="Verifier-grounded task; score is computed by local verifier scripts.",
            payload={
                "verifier_grounded": {
                    "answer_schema": {
                        "format": "final_answer_line",
                        "final_answer_prefix": "FINAL ANSWER:",
                        "value_type": "smiles",
                    },
                },
            },
        )

        skills_on = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=True)
        skills_off = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=False)

        self.assertTrue(skills_on.endswith(record.prompt))
        self.assertIn("Chemistry skill catalog:", skills_on)
        self.assertEqual(record.prompt, skills_off)
        self.assertNotIn("deterministic local verifier scripts", skills_on)
        self.assertNotIn("single valid candidate", skills_on)

    def test_verifier_grounded_xyz_prompt_does_not_inject_fenced_block_schema(self) -> None:
        record = BenchmarkRecord(
            record_id="xtb-gap",
            dataset="verifier_grounded_xtb_xyz",
            source_file="/tmp/verifier_grounded_xtb_xyz.jsonl",
            eval_kind="verifier_grounded",
            prompt="Propose one neutral closed-shell small molecule as an XYZ geometry.",
            reference_answer="Verifier-grounded task; score is computed by local verifier scripts.",
            payload={
                "verifier_grounded": {
                    "answer_schema": {
                        "format": "final_answer_block",
                        "final_answer_prefix": "FINAL ANSWER:",
                        "value_type": "xyz",
                        "fence_language": "xyz",
                    },
                },
            },
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=False)

        self.assertEqual(record.prompt, prompt)
        self.assertNotIn("<XYZ content>", prompt)

    def test_verifier_grounded_bounded_prompt_only_prepends_common_time_budget(self) -> None:
        record = BenchmarkRecord(
            record_id="rdkit-qed",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/verifier_grounded.jsonl",
            eval_kind="verifier_grounded",
            prompt="Official task prompt.\nFINAL ANSWER: <SMILES>",
            reference_answer="No reference answer is exposed.",
            payload={},
        )

        skills_on = build_single_llm_prompt(
            record,
            websearch_enabled=False,
            skills_enabled=True,
            time_budget_seconds=900,
        )
        skills_off = build_single_llm_prompt(
            record,
            websearch_enabled=False,
            skills_enabled=False,
            time_budget_seconds=900,
        )

        expected = "Time budget: 900 seconds for the whole answer attempt.\n\n" + record.prompt
        self.assertTrue(skills_on.endswith(record.prompt))
        self.assertIn("Chemistry skill catalog:", skills_on)
        self.assertEqual(expected, skills_off)
        self.assertNotIn("deterministic local verifier scripts", skills_on)

    def test_verifier_grounded_candidate_contract_requires_final_answer_marker(self) -> None:
        record = BenchmarkRecord(
            record_id="rdkit-logp",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/verifier_grounded.jsonl",
            eval_kind="verifier_grounded",
            prompt="Q",
            reference_answer="Verifier-grounded task; score is computed by local verifier scripts.",
            payload={},
        )

        result = validate_candidate_answer_contract(
            record=record,
            short_answer_text="",
            full_response_text="The molecule is aspirin.",
            runner_meta={},
        )

        self.assertFalse(result.valid)
        self.assertEqual("candidate_answer_contract_invalid", result.code)
        self.assertIn("FINAL ANSWER", result.message)

    def test_verifier_grounded_xyz_block_candidate_contract_uses_answer_schema(self) -> None:
        record = BenchmarkRecord(
            record_id="xtb-gap",
            dataset="verifier_grounded_xtb_xyz",
            source_file="/tmp/verifier_grounded_xtb_xyz.jsonl",
            eval_kind="verifier_grounded",
            prompt="Q",
            reference_answer="Verifier-grounded task; score is computed by local verifier scripts.",
            payload={
                "verifier_grounded": {
                    "answer_schema": self.XYZ_ANSWER_SCHEMA,
                    "task_id": "xtb_gap_window_001",
                }
            },
        )
        full_response_text = "Visible verification.\nFINAL ANSWER:\n```xyz\n3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n```"

        result = validate_candidate_answer_contract(
            record=record,
            short_answer_text="",
            full_response_text=full_response_text,
            runner_meta={},
        )

        self.assertTrue(result.valid)
        self.assertEqual("", result.code)
        self.assertFalse(result.details["has_final_answer_marker"])
        self.assertTrue(result.details["has_complete_answer_for_eval"])
        self.assertEqual("final_answer_block", result.details["answer_schema_format"])
        self.assertEqual("xyz", result.details["answer_schema_value_type"])
        self.assertEqual("xyz", result.details["answer_schema_fence_language"])

    def test_complete_rescue_answer_survives_prior_idle_timeout(self) -> None:
        record = BenchmarkRecord(
            record_id="property_calc_free_energy_001",
            dataset="verifier_grounded_property_calculation",
            source_file="/tmp/verifier_grounded_property_calculation.jsonl",
            eval_kind="verifier_grounded",
            prompt="Q",
            reference_answer="No reference answer is exposed.",
            payload={
                "verifier_grounded": {
                    "answer_schema": {
                        "format": "final_answer_line",
                        "final_answer_prefix": "FINAL ANSWER:",
                        "value_type": "json",
                    }
                }
            },
        )
        full_response_text = 'FINAL ANSWER: {"answer":0.258031679,"unit":"kJ/mol"}'

        result = validate_candidate_answer_contract(
            record=record,
            short_answer_text='{"answer":0.258031679,"unit":"kJ/mol"}',
            full_response_text=full_response_text,
            runner_meta={
                "convergence": {
                    "latest_prompt_error": "LLM idle timeout (120s): no response from model",
                    "latest_prompt_error_is_timeout": True,
                    "finalization_rescue_succeeded": True,
                }
            },
        )

        self.assertTrue(result.valid)
        self.assertTrue(result.details["has_complete_answer_for_eval"])

    def test_single_llm_prompt_exposes_neutral_catalog_only_for_skills_on(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-1",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="verifier_grounded",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        skills_on = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)
        skills_off = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=False)

        self.assertIn("Chemistry skill catalog:", skills_on)
        self.assertIn("act-like-a-chemist", skills_on)
        self.assertTrue(skills_on.endswith(record.prompt))
        self.assertEqual(record.prompt, skills_off)
        for forbidden in (
            "Read `act-like-a-chemist` first",
            "Atomic Coverage Checklist",
            "Tool results only close",
            "Do not use OpenClaw skills",
        ):
            self.assertNotIn(forbidden, skills_on)
            self.assertNotIn(forbidden, skills_off)

    def test_single_llm_prompt_omits_websearch_guidance(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-1",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="verifier_grounded",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        web_on = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)
        web_off = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=True)

        self.assertNotIn("You may use web search", web_on)
        self.assertNotIn("Do not use web search", web_on)
        self.assertNotIn("external browsing", web_on)
        self.assertNotIn("You may use web search", web_off)
        self.assertNotIn("Do not use web search", web_off)
        self.assertNotIn("external browsing", web_off)

    def test_single_llm_prompt_adds_only_time_budget_not_coverage_sop(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-1",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="verifier_grounded",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True, time_budget_seconds=900)

        self.assertIn("Time budget: 900 seconds", prompt)
        self.assertIn("Chemistry skill catalog:", prompt)
        self.assertNotIn("Coverage Checklist", prompt)
        self.assertNotIn("Read `act-like-a-chemist` first", prompt)
        self.assertNotIn("Do not skip task-relevant derivation steps", prompt)
        self.assertNotIn("include enough visible checks for grading", prompt)

    def test_vgb_uses_official_prompt_without_repetition(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-olympiad",
            dataset="verifier_grounded_rdkit",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="verifier_grounded",
            prompt="Calculate the pH.\n\nEnd with FINAL ANSWER: <answer>.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=False)

        self.assertEqual(record.prompt, prompt)
        self.assertNotIn("heredoc", prompt.lower())
        self.assertNotIn("scratch/tmp", prompt)
        self.assertNotIn("inline multiline", prompt)
