from __future__ import annotations

import unittest

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.workflow.prompts import build_chemqa_goal, build_single_llm_prompt, resolve_chemqa_answer_kind
from benchmarking.workflow.runners.single_llm import validate_candidate_answer_contract


class BenchmarkPromptsTests(unittest.TestCase):
    XYZ_ANSWER_SCHEMA = {
        "format": "final_answer_block",
        "final_answer_prefix": "FINAL ANSWER:",
        "value_type": "xyz",
        "fence_language": "xyz",
    }

    def test_frontierscience_olympiad_uses_numeric_answer_kind(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-1",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        self.assertEqual("numeric_short_answer", resolve_chemqa_answer_kind(record))

    def test_frontierscience_olympiad_formula_reference_uses_formula_answer_kind(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-formula",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Determine the corresponding \\( K_M \\) in terms of \\( [S] \\) and constants.",
            reference_answer="The corresponding `\\( K_M \\)` is KM=Ks1+Js[S]2",
            payload={"track": "olympiad"},
        )

        self.assertEqual("formula_short_answer", resolve_chemqa_answer_kind(record))
        self.assertIn(
            "ChemQA Artifact Flow answer kind: formula_short_answer.",
            build_chemqa_goal(record, websearch_enabled=True),
        )

    def test_frontierscience_olympiad_latex_prompt_with_numeric_reference_stays_numeric(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-numeric-latex",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Determine dissolved `\\( Sr^{2+} \\)` in micrograms for `\\( SrF_2 \\)`.",
            reference_answer="7.59",
            payload={"track": "olympiad"},
        )

        self.assertEqual("numeric_short_answer", resolve_chemqa_answer_kind(record))

    def test_hle_prompts_use_official_answer_confidence_format(self) -> None:
        record = BenchmarkRecord(
            record_id="hle-1",
            dataset="hle",
            source_file="/tmp/hle.jsonl",
            eval_kind="hle",
            prompt="Which option is correct?\nA. X\nB. Y",
            reference_answer="B",
            payload={"answer_type": "multiple-choice"},
        )

        single_prompt = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=True)
        chemqa_goal = build_chemqa_goal(record, websearch_enabled=True)

        self.assertEqual("multiple_choice", resolve_chemqa_answer_kind(record))
        self.assertIn("Explanation:", single_prompt)
        self.assertIn("Answer:", single_prompt)
        self.assertIn("Confidence:", single_prompt)
        self.assertIn("Explanation:", chemqa_goal)
        self.assertIn("Answer:", chemqa_goal)
        self.assertIn("Confidence:", chemqa_goal)
        self.assertIn("ChemQA Artifact Flow answer kind: multiple_choice.", chemqa_goal)

    def test_hle_prompt_specializes_multiple_choice_answer_field(self) -> None:
        record = BenchmarkRecord(
            record_id="hle-mc",
            dataset="hle",
            source_file="/tmp/hle.jsonl",
            eval_kind="hle",
            prompt="Which option is correct?\nA. X\nB. Y",
            reference_answer="B",
            payload={"answer_type": "multipleChoice"},
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=True)

        self.assertIn("For HLE multiple-choice tasks, put only the option letter", prompt)
        self.assertIn("Do not add `FINAL ANSWER:`", prompt)

    def test_hle_prompt_specializes_exact_match_answer_field(self) -> None:
        record = BenchmarkRecord(
            record_id="hle-exact",
            dataset="hle",
            source_file="/tmp/hle.jsonl",
            eval_kind="hle",
            prompt="Give the rate constant.",
            reference_answer="1.4E-14 hr^-1",
            payload={"answer_type": "exactMatch"},
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=True)

        self.assertIn("For HLE exact-match tasks, put only the final value, expression, or entity", prompt)
        self.assertIn("Do not add `FINAL ANSWER:`", prompt)

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

        prompt = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=True)

        self.assertEqual(record.prompt, prompt)
        self.assertNotIn("deterministic local verifier scripts", prompt)
        self.assertNotIn("single valid candidate", prompt)

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

        prompt = build_single_llm_prompt(record, websearch_enabled=False, skills_enabled=True)

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
        self.assertEqual(expected, skills_on)
        self.assertEqual(expected, skills_off)
        self.assertNotIn("act-like-a-chemist", skills_on)
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

    def test_single_llm_prompt_does_not_inject_skill_strategy_guidance(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-1",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        skills_on = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)
        skills_off = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=False)

        self.assertEqual(skills_on, skills_off)
        for forbidden in (
            "Skill capability tree",
            "act-like-a-chemist",
            "Atomic Coverage Checklist",
            "Tool results only close",
            "Do not use OpenClaw skills",
        ):
            self.assertNotIn(forbidden, skills_on)
            self.assertNotIn(forbidden, skills_off)

    def test_single_llm_prompt_omits_websearch_guidance(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-1",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
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
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True, time_budget_seconds=900)

        self.assertIn("Time budget: 900 seconds", prompt)
        self.assertNotIn("Coverage Checklist", prompt)
        self.assertNotIn("act-like-a-chemist", prompt)
        self.assertNotIn("Do not skip task-relevant derivation steps", prompt)
        self.assertNotIn("include enough visible checks for grading", prompt)

    def test_superchem_prompt_requires_visible_option_checks_without_skill_call_cap(self) -> None:
        record = BenchmarkRecord(
            record_id="superchem-1",
            dataset="superchem",
            source_file="/tmp/superchem.jsonl",
            eval_kind="superchem_multiple_choice_rpf",
            prompt="Choose the product.\nA. X\nB. Y",
            reference_answer="B",
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)

        self.assertIn("option checks", prompt)
        self.assertIn("FINAL ANSWER: <option letters>", prompt)
        self.assertIn("If the gathered evidence is sufficient to distinguish the options, answer immediately", prompt)
        self.assertIn("Provider skills may be used when they directly distinguish candidate options", prompt)
        self.assertNotIn("Show concise reasoning", prompt)
        self.assertNotIn("at most one", prompt.lower())
        self.assertNotIn("max one", prompt.lower())

    def test_superchem_prompt_targets_checkpoint_rpf(self) -> None:
        record = BenchmarkRecord(
            record_id="superchem-1",
            dataset="superchem",
            source_file="/tmp/superchem.jsonl",
            eval_kind="superchem_multiple_choice_rpf",
            prompt="Choose the product.\nA. X\nB. Y",
            reference_answer="B",
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)

        self.assertIn("checkpoint-like", prompt)
        self.assertIn("key structure, mechanism, stoichiometry, and elimination checks", prompt)
        self.assertIn("Use only uppercase option letters", prompt)
        self.assertIn("separate multiple correct letters with `|`", prompt)

    def test_chembench_prompt_distinguishes_numeric_and_exact_answers(self) -> None:
        numeric_record = BenchmarkRecord(
            record_id="chembench-numeric",
            dataset="chembench",
            source_file="/tmp/chembench.jsonl",
            eval_kind="chembench_open_ended",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={},
        )
        exact_record = BenchmarkRecord(
            record_id="chembench-exact",
            dataset="chembench",
            source_file="/tmp/chembench.jsonl",
            eval_kind="chembench_open_ended",
            prompt="What is the IUPAC name of [START_SMILES]CCO[END_SMILES]?",
            reference_answer="ethanol",
            payload={},
        )

        numeric_prompt = build_single_llm_prompt(numeric_record, websearch_enabled=True, skills_enabled=True)
        exact_prompt = build_single_llm_prompt(exact_record, websearch_enabled=True, skills_enabled=True)

        self.assertIn("formulas, substitutions, units, rounding", numeric_prompt)
        self.assertIn("precise final string, structure, name, or count", exact_prompt)
        self.assertIn("Avoid adding irrelevant formulas", exact_prompt)

    def test_single_llm_prompt_specializes_frontierscience_research(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-research",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_research",
            prompt="Context: protocol. Question: evaluate each changed condition.",
            reference_answer="Points: 1.0, Item: Covers each condition.",
            payload={"track": "research"},
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)

        self.assertIn("research-track", prompt)
        self.assertIn("itemized reasoning criteria", prompt)
        self.assertIn("every requested sub-question", prompt)
        self.assertIn("Do not collapse", prompt)
        self.assertIn("## FINAL RESEARCH ANSWER", prompt)
        self.assertIn("<rubric-complete final synthesis>", prompt)
        self.assertNotIn("FINAL ANSWER:", prompt)
        self.assertNotIn("concise answer summary", prompt)

    def test_frontierscience_olympiad_final_answer_is_exact_short_target(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-olympiad",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True)

        self.assertIn("The final line should contain only the requested value, expression, formula, structure name, or entity", prompt)
        self.assertIn("Do not provide multiple answer attempts", prompt)

    def test_chemqa_goal_specializes_frontierscience_research(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-research",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_research",
            prompt="Context: protocol. Question: evaluate each changed condition.",
            reference_answer="Points: 1.0, Item: Covers each condition.",
            payload={"track": "research"},
        )

        goal = build_chemqa_goal(record, websearch_enabled=True)

        self.assertIn("complete multi-part research answer", goal)
        self.assertIn("Do not compress the response to a concise final answer", goal)
        self.assertIn("## FINAL RESEARCH ANSWER", goal)
        self.assertNotIn("concise answer summary", goal)
        self.assertIn("ChemQA Artifact Flow answer kind: multi_part_research_answer.", goal)

    def test_chemqa_goal_omits_websearch_guidance(self) -> None:
        record = BenchmarkRecord(
            record_id="fs-1",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Calculate the pH.",
            reference_answer="4.7",
            payload={"track": "olympiad"},
        )

        web_on = build_chemqa_goal(record, websearch_enabled=True)
        web_off = build_chemqa_goal(record, websearch_enabled=False)

        self.assertNotIn("Web search may be used", web_on)
        self.assertNotIn("Do not use web search", web_on)
        self.assertNotIn("external browsing", web_on)
        self.assertNotIn("Web search may be used", web_off)
        self.assertNotIn("Do not use web search", web_off)
        self.assertNotIn("external browsing", web_off)
