from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchmarking.contracts import AnswerPayload, RunnerResult, RunStatus
from benchmarking.datasets import BenchmarkRecord
from benchmarking.evaluators import EvaluationResult, normalize_answer_tracks
from benchmarking.experiments import ExperimentSpec
from benchmarking.orchestration import run_group
from benchmarking.reporting import build_error_group_record_result


@dataclass(frozen=True)
class Group:
    id: str = "single_llm_skills_off"
    label: str = "Single"
    runner: str = "single_llm"
    websearch: bool = True
    skills_enabled: bool = False


class OrchestrationTests(unittest.TestCase):
    def test_run_group_scores_successful_runner_result(self) -> None:
        record = BenchmarkRecord(
            record_id="r1",
            dataset="chembench",
            source_file="/tmp/demo.jsonl",
            eval_kind="chembench_open_ended",
            prompt="Q",
            reference_answer="A",
            payload={},
        )
        group = Group()
        calls: dict[str, Any] = {}

        class StubRunner:
            def run(self, actual_record: BenchmarkRecord, actual_group: Group) -> RunnerResult:
                calls["runner"] = (actual_record, actual_group)
                return RunnerResult(
                    status=RunStatus.COMPLETED,
                    answer=AnswerPayload(short_answer_text="A", full_response_text="FINAL ANSWER: A"),
                    raw={"ok": True},
                    runner_meta={"run_id": "demo-run"},
                )

        def build_runner_fn(**kwargs: Any) -> StubRunner:
            calls["build_runner_kwargs"] = kwargs
            return StubRunner()

        def evaluate_answer_fn(
            actual_record: BenchmarkRecord,
            *,
            short_answer_text: str,
            full_response_text: str,
            answer_text: str,
            judge: object,
        ) -> EvaluationResult:
            calls["evaluate"] = (actual_record, short_answer_text, full_response_text, answer_text, judge)
            return EvaluationResult(
                eval_kind="chembench_open_ended",
                score=1.0,
                max_score=1.0,
                normalized_score=1.0,
                passed=True,
                primary_metric="unit",
                primary_metric_direction="higher_is_better",
                details={},
            )

        def build_error_entry(**kwargs: Any):
            return build_error_group_record_result(
                **kwargs,
                classify_subset_fn=lambda _record: "chembench",
                normalize_answer_tracks_fn=normalize_answer_tracks,
                build_execution_error_evaluation_fn=lambda actual_record, *, error_message: EvaluationResult(
                    eval_kind=actual_record.eval_kind,
                    score=0.0,
                    max_score=1.0,
                    normalized_score=0.0,
                    passed=False,
                    primary_metric="execution_error",
                    primary_metric_direction="lower_is_better",
                    details={"error": error_message},
                ),
                deep_copy_jsonish_fn=lambda value: value,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            saved: list[Path] = []
            results = run_group(
                group=group,
                records=[record],
                output_root=Path(tmpdir),
                single_timeout=10,
                chemqa_timeout=10,
                judge=object(),
                config_path=Path(tmpdir) / "cfg.json",
                single_agent="agent-1",
                chemqa_root=Path(tmpdir),
                chemqa_model_profile="unused",
                review_rounds=None,
                rebuttal_rounds=None,
                chemqa_slot_sets={},
                experiment_specs={
                    group.id: ExperimentSpec(
                        id=group.id,
                        label=group.label,
                        runner_kind=group.runner,
                        websearch_enabled=group.websearch,
                        skills_enabled=group.skills_enabled,
                        single_agent_id="agent-1",
                    )
                },
                build_runner_fn=build_runner_fn,
                evaluate_answer_fn=evaluate_answer_fn,
                build_error_group_record_result_fn=build_error_entry,
                classify_subset_fn=lambda _record: "chembench",
                save_json_fn=lambda path, payload: (saved.append(path), path.parent.mkdir(parents=True, exist_ok=True), path.write_text(str(payload), encoding="utf-8")),
                slugify_fn=lambda value, **_kwargs: str(value),
            )

        self.assertEqual(1, len(results))
        entry = results[0]
        self.assertTrue(entry.evaluation["passed"])
        self.assertEqual("FINAL ANSWER: A", entry.answer_text)
        self.assertEqual("demo-run", entry.runner_meta["run_id"])
        self.assertIn("configured_skills", calls["build_runner_kwargs"])
        self.assertEqual("r1.json", saved[0].name)

    def test_run_group_preserves_runner_diagnostics_when_evaluator_raises(self) -> None:
        record = BenchmarkRecord(
            record_id="r1",
            dataset="superchem",
            source_file="/tmp/demo.jsonl",
            eval_kind="superchem_multiple_choice_rpf",
            prompt="Q",
            reference_answer="A",
            payload={},
        )
        group = Group()

        class StubRunner:
            def run(self, actual_record: BenchmarkRecord, actual_group: Group) -> RunnerResult:
                return RunnerResult(
                    status=RunStatus.COMPLETED,
                    answer=AnswerPayload(short_answer_text="A", full_response_text="Reasoning\nFINAL ANSWER: A"),
                    raw={"result": {"payloads": [{"text": "Reasoning\nFINAL ANSWER: A"}]}},
                    runner_meta={"run_id": "demo-run", "candidate_answer_contract": {"valid": True}},
                )

        def build_runner_fn(**kwargs: Any) -> StubRunner:
            return StubRunner()

        def evaluate_answer_fn(
            actual_record: BenchmarkRecord,
            *,
            short_answer_text: str,
            full_response_text: str,
            answer_text: str,
            judge: object,
        ) -> EvaluationResult:
            raise RuntimeError("judge exploded")

        def build_error_entry(**kwargs: Any):
            return build_error_group_record_result(
                **kwargs,
                classify_subset_fn=lambda _record: "superchem",
                normalize_answer_tracks_fn=normalize_answer_tracks,
                build_execution_error_evaluation_fn=lambda actual_record, *, error_message: EvaluationResult(
                    eval_kind=actual_record.eval_kind,
                    score=0.0,
                    max_score=1.0,
                    normalized_score=0.0,
                    passed=False,
                    primary_metric="execution_error",
                    primary_metric_direction="lower_is_better",
                    details={"error": error_message},
                ),
                deep_copy_jsonish_fn=lambda value: value,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            results = run_group(
                group=group,
                records=[record],
                output_root=Path(tmpdir),
                single_timeout=10,
                chemqa_timeout=10,
                judge=object(),
                config_path=Path(tmpdir) / "cfg.json",
                single_agent="agent-1",
                chemqa_root=Path(tmpdir),
                chemqa_model_profile="unused",
                review_rounds=None,
                rebuttal_rounds=None,
                chemqa_slot_sets={},
                experiment_specs={
                    group.id: ExperimentSpec(
                        id=group.id,
                        label=group.label,
                        runner_kind=group.runner,
                        websearch_enabled=group.websearch,
                        skills_enabled=group.skills_enabled,
                        single_agent_id="agent-1",
                    )
                },
                build_runner_fn=build_runner_fn,
                evaluate_answer_fn=evaluate_answer_fn,
                build_error_group_record_result_fn=build_error_entry,
                classify_subset_fn=lambda _record: "superchem",
                save_json_fn=lambda path, payload: (
                    path.parent.mkdir(parents=True, exist_ok=True),
                    path.write_text(str(payload), encoding="utf-8"),
                ),
                slugify_fn=lambda value, **_kwargs: str(value),
            )

        self.assertEqual(1, len(results))
        entry = results[0]
        self.assertIn("judge/evaluator failed", entry.error or "")
        self.assertEqual("demo-run", entry.runner_meta["run_id"])
        self.assertIn("judge exploded", entry.runner_meta["evaluation_error"]["message"])
        self.assertIn("traceback", entry.runner_meta)
        self.assertEqual({"result": {"payloads": [{"text": "Reasoning\nFINAL ANSWER: A"}]}}, entry.raw)
        self.assertEqual("A", entry.short_answer_text)
        self.assertEqual("Reasoning\nFINAL ANSWER: A", entry.full_response_text)


if __name__ == "__main__":
    unittest.main()
