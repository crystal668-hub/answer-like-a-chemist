from __future__ import annotations

import unittest

from benchmarking.datasets import BenchmarkRecord
from benchmarking.prompts import build_chemqa_goal, build_single_llm_prompt, resolve_chemqa_answer_kind


class BenchmarkPromptsTests(unittest.TestCase):
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

    def test_single_llm_prompt_respects_skills_enabled_flag(self) -> None:
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

        self.assertIn("Skill capability tree", skills_on)
        self.assertIn("First choose a capability domain", skills_on)
        self.assertIn("paper-pipeline", skills_on)
        self.assertIn("calculation-math", skills_on)
        self.assertNotIn("Experimental chemistry skill routing rules", skills_on)
        self.assertNotIn("first matching primary route", skills_on)
        self.assertNotIn("SKILL TRACE: skipped", skills_on)
        self.assertNotIn("Do not use OpenClaw skills", skills_on)
        self.assertNotIn("Skill capability tree", skills_off)
        self.assertIn("Do not use OpenClaw skills", skills_off)

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

    def test_single_llm_prompt_uses_30_percent_stop_rule_and_visible_checks(self) -> None:
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

        self.assertIn("When roughly 30% or less of the budget remains", prompt)
        self.assertNotIn("20% or less", prompt)
        self.assertNotIn("Be careful, concise", prompt)
        self.assertIn("Do not skip task-relevant derivation steps", prompt)
        self.assertIn("include enough visible checks for grading", prompt)

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
