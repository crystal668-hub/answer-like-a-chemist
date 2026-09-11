from __future__ import annotations
from benchmarking.service.single.orchestration import runner_options as single_runner_options

import tempfile
import unittest
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarking.core.answer_processing import normalize_answer_tracks
from benchmarking.core.contracts import AnswerPayload, RunnerResult, RunStatus
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.core.experiments import ExperimentSpec
from benchmarking.core.reporting import build_error_group_record_result
from benchmarking.dashboard.progress import ProgressWriter
from benchmarking.scoring.results import EvaluationResult
from benchmarking.workflow.orchestration import run_group


@dataclass(frozen=True)
class Group:
    id: str = "single_llm_skills_off"
    label: str = "Single"
    runner: str = "single_llm"
    websearch: bool = True
    skills_enabled: bool = False


class OrchestrationTests(unittest.TestCase):
    def test_concurrent_records_have_distinct_config_and_workspace_identity(self):
        barrier = threading.Barrier(2)
        snapshots = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = SimpleNamespace(active_workspace_path=lambda *, group_id, agent_id: root / group_id / agent_id)
            config = root / "source.json"
            original_workspace = manager.active_workspace_path(group_id=Group().id, agent_id="agent")
            config.write_text(json.dumps({"agents": {"list": [{"id": "agent", "workspace": str(original_workspace)}]}, "policy": {"agent": {"path": str(original_workspace / "scratch")}}}))
            def build(**kwargs):
                data = json.loads(kwargs["config_path"].read_text())
                agent = kwargs["agent_id"]
                expected = manager.active_workspace_path(group_id=Group().id, agent_id=agent)
                self.assertEqual(str(expected), data["agents"]["list"][0]["workspace"])
                self.assertEqual(str(expected / "scratch"), data["policy"][agent]["path"])
                snapshots.append((agent, kwargs["config_path"]))
                class Runner:
                    def run(self, record, group):
                        barrier.wait(timeout=2)
                        return RunnerResult(status=RunStatus.COMPLETED, answer=AnswerPayload(short_answer_text="A", full_response_text="A"), raw={}, runner_meta={})
                return Runner()
            def run(index):
                return run_group(
        group=Group(),
        records=[BenchmarkRecord(record_id=str(index), dataset="demo", source_file="demo", prompt="Q", reference_answer="A", eval_kind="demo")],
        output_root=root,
        judge=None,
        build_runner_fn=build,
        evaluate_answer_fn=lambda *a, **k: EvaluationResult(eval_kind="demo", score=1, max_score=1, normalized_score=1, passed=True, primary_metric="score", primary_metric_direction="higher_is_better", details={}),
        build_error_group_record_result_fn=lambda **kw: self.fail(str(kw)),
        classify_subset_fn=lambda r: "demo",
        save_json_fn=lambda p,d: p.write_text(json.dumps(d)),
        slugify_fn=str,
        manage_group_lifecycle=False,
        runner_options_factory=lambda: single_runner_options(
            group=Group(),
            output_root=root,
            single_timeout=10,
            config_path=config,
            single_agent="agent",
            experiment_specs={Group().id: SimpleNamespace(skill_allowlist=())},
            single_agent_thinking="off",
            workspace_manager=manager,
            manage_group_lifecycle=False,
        ),
    )
            (root / "per-record" / Group().id).mkdir(parents=True)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(run, index) for index in range(2)]
                results = [future.result(timeout=5) for future in futures]
            self.assertEqual(2, len({item[0] for item in snapshots}))
            self.assertEqual(2, len({item[1] for item in snapshots}))
            self.assertEqual("agent", json.loads(config.read_text())["agents"]["list"][0]["id"])
            self.assertTrue(all(result[0].scored for result in results))

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
        judge=object(),
        build_runner_fn=build_runner_fn,
        evaluate_answer_fn=evaluate_answer_fn,
        build_error_group_record_result_fn=build_error_entry,
        classify_subset_fn=lambda _record: "chembench",
        save_json_fn=lambda path, payload: (saved.append(path), path.parent.mkdir(parents=True, exist_ok=True), path.write_text(str(payload), encoding="utf-8")),
        slugify_fn=lambda value, **_kwargs: str(value),
        runner_options_factory=lambda: single_runner_options(
            group=group,
            output_root=Path(tmpdir),
            single_timeout=10,
            config_path=Path(tmpdir) / "cfg.json",
            single_agent="agent-1",
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
            single_agent_thinking="minimal",
        ),
    )

        self.assertEqual(1, len(results))
        entry = results[0]
        self.assertTrue(entry.evaluation["passed"])
        self.assertEqual("FINAL ANSWER: A", entry.answer_text)
        self.assertEqual("demo-run", entry.runner_meta["run_id"])
        self.assertIn("configured_skills", calls["build_runner_kwargs"])
        self.assertEqual("minimal", calls["build_runner_kwargs"]["benchmark_agent_thinking"])
        self.assertEqual("r1.json", saved[0].name)

    def test_run_group_writes_progress_events(self) -> None:
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

        class StubRunner:
            def run(self, actual_record: BenchmarkRecord, actual_group: Group) -> RunnerResult:
                return RunnerResult(
                    status=RunStatus.COMPLETED,
                    answer=AnswerPayload(short_answer_text="A", full_response_text="FINAL ANSWER: A"),
                    raw={"ok": True},
                    runner_meta={},
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
            output_root = Path(tmpdir)
            progress_writer = ProgressWriter(output_root, total_records=1, groups=[group.id])
            run_group(
        group=group,
        records=[record],
        output_root=output_root,
        judge=object(),
        build_runner_fn=lambda **_kwargs: StubRunner(),
        evaluate_answer_fn=lambda *_args, **_kwargs: EvaluationResult(
                    eval_kind="chembench_open_ended",
                    score=1.0,
                    max_score=1.0,
                    normalized_score=1.0,
                    passed=True,
                    primary_metric="unit",
                    primary_metric_direction="higher_is_better",
                    details={},
                ),
        build_error_group_record_result_fn=build_error_entry,
        classify_subset_fn=lambda _record: "chembench",
        save_json_fn=lambda path, payload: (
                    path.parent.mkdir(parents=True, exist_ok=True),
                    path.write_text(str(payload), encoding="utf-8"),
                ),
        slugify_fn=lambda value, **_kwargs: str(value),
        progress_writer=progress_writer,
        runner_options_factory=lambda: single_runner_options(
            group=group,
            output_root=output_root,
            single_timeout=10,
            config_path=output_root / "cfg.json",
            single_agent="agent-1",
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
            single_agent_thinking="minimal",
        ),
    )
            events = (output_root / "progress" / "events.jsonl").read_text(encoding="utf-8")

        self.assertIn('"type": "group_started"', events)
        self.assertIn('"type": "record_started"', events)
        self.assertIn('"type": "record_completed"', events)

    def test_run_group_marks_progress_failed_when_runner_init_fails(self) -> None:
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
            output_root = Path(tmpdir)
            progress_writer = ProgressWriter(output_root, total_records=1, groups=[group.id])
            run_group(
        group=group,
        records=[record],
        output_root=output_root,
        judge=object(),
        build_runner_fn=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        evaluate_answer_fn=lambda *_args, **_kwargs: None,
        build_error_group_record_result_fn=build_error_entry,
        classify_subset_fn=lambda _record: "chembench",
        save_json_fn=lambda path, payload: (
                    path.parent.mkdir(parents=True, exist_ok=True),
                    path.write_text(str(payload), encoding="utf-8"),
                ),
        slugify_fn=lambda value, **_kwargs: str(value),
        progress_writer=progress_writer,
        runner_options_factory=lambda: single_runner_options(
            group=group,
            output_root=output_root,
            single_timeout=10,
            config_path=output_root / "cfg.json",
            single_agent="agent-1",
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
            single_agent_thinking="minimal",
        ),
    )
            state = (output_root / "progress" / "state.json").read_text(encoding="utf-8")

        self.assertIn('"status": "failed"', state)
        self.assertIn('"completed": 1', state)
        self.assertIn('"completed_count": 1', state)

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
        judge=object(),
        build_runner_fn=build_runner_fn,
        evaluate_answer_fn=evaluate_answer_fn,
        build_error_group_record_result_fn=build_error_entry,
        classify_subset_fn=lambda _record: "superchem",
        save_json_fn=lambda path, payload: (
                    path.parent.mkdir(parents=True, exist_ok=True),
                    path.write_text(str(payload), encoding="utf-8"),
                ),
        slugify_fn=lambda value, **_kwargs: str(value),
        runner_options_factory=lambda: single_runner_options(
            group=group,
            output_root=Path(tmpdir),
            single_timeout=10,
            config_path=Path(tmpdir) / "cfg.json",
            single_agent="agent-1",
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
            single_agent_thinking="minimal",
        ),
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
