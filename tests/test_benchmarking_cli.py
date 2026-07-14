from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from benchmarking.core.reporting import GroupRecordResult as ReportingGroupRecordResult
from benchmarking.workflow import cli as benchmarking_cli


def test_benchmark_test_is_thin_compatibility_facade() -> None:
    module_path = Path(__file__).resolve().parents[1] / "benchmark_test.py"
    spec = importlib.util.spec_from_file_location("benchmark_test_facade_probe", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.main is benchmarking_cli.main
    assert module.GroupRecordResult is ReportingGroupRecordResult
    assert module.GroupRecordResult.__module__ == "benchmarking.core.reporting"
    assert module.BenchmarkRecord is benchmarking_cli.BenchmarkRecord


def test_benchmarking_cli_owns_benchmark_entrypoint_behavior() -> None:
    assert callable(benchmarking_cli.main)
    assert callable(benchmarking_cli.parse_args)
    assert benchmarking_cli.EXPERIMENT_GROUPS["single_llm_skills_on"].runner == "single_llm"
    assert benchmarking_cli.EXPERIMENT_GROUPS["chemqa_skills_on"].runner == "chemqa"
    assert all(group.websearch is False for group in benchmarking_cli.EXPERIMENT_GROUPS.values())
    assert all(spec.websearch_enabled is False for spec in benchmarking_cli.EXPERIMENT_SPECS.values())


def test_parse_args_accepts_no_timeout_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmarking_cli.sys,
        "argv",
        ["benchmark_test.py", "--no-timeout"],
    )

    args = benchmarking_cli.parse_args()

    assert args.no_timeout is True


def test_parse_args_accepts_no_analysis_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmarking_cli.sys,
        "argv",
        ["benchmark_test.py", "--no-analysis"],
    )

    args = benchmarking_cli.parse_args()

    assert args.no_analysis is True


def test_default_web_search_preflight_skips_all_experiment_groups(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    class FakeConfigPool:
        def config_for_group(self, group):
            return tmp_path / f"{group.id}.json"

    def fake_preflight(**kwargs):
        calls.append(kwargs["agent_id"])
        return {"available": True}

    monkeypatch.setattr(benchmarking_cli, "run_web_search_preflight", fake_preflight)

    report = benchmarking_cli.run_benchmark_web_search_preflight(
        group_ids=list(benchmarking_cli.EXPERIMENT_GROUPS),
        config_pool=FakeConfigPool(),
        args=type("Args", (), {"single_agent_id_override": None})(),
    )

    assert calls == []
    assert report["available"] is True
    assert report["reports"] == {}


def test_resume_filters_existing_per_record_before_runner_creation(tmp_path) -> None:
    records = [
        benchmarking_cli.BenchmarkRecord(
            record_id=record_id,
            dataset="demo",
            source_file="/tmp/demo.jsonl",
            prompt="Question?",
            reference_answer="Answer",
            eval_kind="generic_semantic",
        )
        for record_id in ("existing", "pending")
    ]
    existing = tmp_path / "per-record" / "single_llm_skills_on" / "existing.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("{}\n", encoding="utf-8")

    pending = benchmarking_cli.pending_records_for_group(
        records,
        output_root=tmp_path,
        group_id="single_llm_skills_on",
        merge_existing_per_record=True,
    )

    assert [record.record_id for record in pending] == ["pending"]


def test_web_search_preflight_failure_materializes_group_failure(monkeypatch, tmp_path) -> None:
    record = benchmarking_cli.BenchmarkRecord(
        record_id="record-1",
        dataset="demo",
        source_file="/tmp/demo.jsonl",
        prompt="Question?",
        reference_answer="Answer",
        eval_kind="generic_semantic",
        grading=benchmarking_cli.GradingSpec(
            kind="generic_semantic",
            reference_answer="Answer",
            subset="demo_subset",
        ),
    )
    run_group_calls: list[str] = []
    monkeypatch.setitem(
        benchmarking_cli.EXPERIMENT_GROUPS,
        "single_llm_skills_on",
        benchmarking_cli.ExperimentGroup(
            id="single_llm_skills_on",
            label=benchmarking_cli.EXPERIMENT_GROUPS["single_llm_skills_on"].label,
            runner="single_llm",
            websearch=True,
            skills_enabled=True,
        ),
    )
    monkeypatch.setitem(
        benchmarking_cli.EXPERIMENT_SPECS,
        "single_llm_skills_on",
        benchmarking_cli.ExperimentSpec(
            id="single_llm_skills_on",
            label=benchmarking_cli.EXPERIMENT_GROUPS["single_llm_skills_on"].label,
            runner_kind="single_llm",
            websearch_enabled=True,
            skills_enabled=True,
            single_agent_id=benchmarking_cli.BASELINE_AGENT_IDS["single_llm_skills_on"],
            skill_allowlist=tuple(benchmarking_cli.BENCHMARK_SKILLS_ALLOWLIST),
        ),
    )

    class FakeConfigPool:
        def __init__(self, *args, **kwargs) -> None:
            self.context = type("Context", (), {"experiment_specs": benchmarking_cli.EXPERIMENT_SPECS})()

        def config_for_group(self, group):
            path = tmp_path / "runtime-config" / f"{group.id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

        def judge_config_path(self):
            path = tmp_path / "runtime-config" / "judge.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

    monkeypatch.setattr(
        benchmarking_cli,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "single_timeout": 30,
                "no_timeout": False,
                "chemqa_timeout": 30,
                "max_unchanged_status_polls": 1,
                "max_recovery_attempts": 1,
                "groups": "single_llm_skills_on",
                "benchmark_root": str(tmp_path),
                "files": None,
                "datasets": None,
                "list_datasets": False,
                "subsets": None,
                "random_count_per_subset": None,
                "random_seed": 0,
                "offset": 0,
                "limit": None,
                "print_selected_records": False,
                "exact_output_dir": str(tmp_path / "out"),
                "output_dir": str(tmp_path / "out-parent"),
                "openclaw_config": str(tmp_path / "openclaw.json"),
                "single_agent_model": "openai/gpt-5.4",
                "single_agent_thinking": "high",
                "judge_model": "openai/gpt-5.4",
                "judge_agent_thinking": "high",
                "single_agent_id_override": None,
                "judge_agent": "benchmark-judge",
                "judge_timeout": 30,
                "max_concurrent_groups": 1,
                "inter_wave_delay_seconds": 0,
                "chemqa_root": str(tmp_path / "chemqa"),
                "chemqa_model_profile": "profile",
                "review_rounds": None,
                "rebuttal_rounds": None,
                "merge_existing_per_record": False,
            },
        )(),
    )
    monkeypatch.setattr(benchmarking_cli, "select_dataset_files", lambda args: [tmp_path / "demo.jsonl"])
    monkeypatch.setattr(benchmarking_cli, "load_records", lambda paths: [record])
    monkeypatch.setattr(benchmarking_cli, "check_all_skill_health", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        benchmarking_cli,
        "summarize_skill_health",
        lambda reports: {"available_skill_count": 0, "unavailable_skill_count": 0, "available_skills": [], "unavailable_skills": []},
    )
    monkeypatch.setattr(benchmarking_cli, "ConfigPool", FakeConfigPool)
    monkeypatch.setattr(
        benchmarking_cli,
        "run_benchmark_web_search_preflight",
        lambda **kwargs: {
            "enabled": True,
            "provider": "duckduckgo",
            "available": False,
            "reports": {"single_llm_skills_on": {"available": False, "error": "fetch failed"}},
        },
    )
    monkeypatch.setattr(benchmarking_cli, "run_pending_cleanroom_cleanup", lambda: [])
    monkeypatch.setattr(
        benchmarking_cli,
        "launch_automated_evaluation",
        lambda output_root: {"status": "launched", "output_root": str(output_root)},
    )

    def fake_run_group(**kwargs):
        run_group_calls.append(kwargs["group"].id)
        return []

    monkeypatch.setattr(benchmarking_cli, "run_group", fake_run_group)

    assert benchmarking_cli.main() == 0

    assert run_group_calls == []
    result_path = tmp_path / "out" / "per-record" / "single_llm_skills_on" / "record-1.json"
    payload = benchmarking_cli.json.loads(result_path.read_text(encoding="utf-8"))
    assert "web_search preflight failed" in payload["error"]
    progress = benchmarking_cli.json.loads((tmp_path / "out" / "progress" / "state.json").read_text(encoding="utf-8"))
    assert progress["completed"] == 1
    assert progress["groups"]["single_llm_skills_on"]["status"] == "failed"
    assert progress["groups"]["single_llm_skills_on"]["completed_records"] == ["record-1"]


def test_main_launches_automated_evaluation_after_results_are_written(monkeypatch, tmp_path) -> None:
    record = benchmarking_cli.BenchmarkRecord(
        record_id="record-1",
        dataset="demo",
        source_file="/tmp/demo.jsonl",
        prompt="Question?",
        reference_answer="Answer",
        eval_kind="generic_semantic",
        grading=benchmarking_cli.GradingSpec(
            kind="generic_semantic",
            reference_answer="Answer",
            subset="demo_subset",
        ),
    )
    launched: list[Path] = []

    class FakeConfigPool:
        def __init__(self, *args, **kwargs) -> None:
            self.context = type("Context", (), {"experiment_specs": benchmarking_cli.EXPERIMENT_SPECS})()

        def config_for_group(self, group):
            path = tmp_path / "out" / "runtime-config" / f"{group.id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

        def judge_config_path(self):
            path = tmp_path / "out" / "runtime-config" / "judge.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

    def fake_run_group(**kwargs):
        return [
            benchmarking_cli.GroupRecordResult(
                schema_version=2,
                group_id="single_llm_skills_off",
                group_label="single off",
                runner="single_llm",
                websearch=False,
                record_id="record-1",
                subset="demo_subset",
                dataset="demo",
                source_file="/tmp/demo.jsonl",
                eval_kind="generic_semantic",
                prompt="Question?",
                reference_answer="Answer",
                answer_text="FINAL ANSWER: Answer",
                evaluation={
                    "eval_kind": "generic_semantic",
                    "score": 1.0,
                    "max_score": 1.0,
                    "normalized_score": 1.0,
                    "passed": True,
                    "primary_metric": "semantic_match",
                    "primary_metric_direction": "higher_is_better",
                    "details": {},
                },
                runner_meta={},
                raw={},
                elapsed_seconds=0.1,
                run_lifecycle_status="completed",
                protocol_completion_status="completed",
                protocol_acceptance_status=None,
                answer_availability="native_final",
                answer_reliability="native",
                evaluable=True,
                scored=True,
                recovery_mode="none",
                degraded_execution=False,
                skills_enabled=False,
                execution_error_kind=None,
                error=None,
                short_answer_text="Answer",
                full_response_text="FINAL ANSWER: Answer",
            )
        ]

    def fake_launch(output_root):
        output_path = Path(output_root)
        assert (output_path / "results.json").is_file()
        assert (output_path / "runtime-manifest.json").is_file()
        assert not (output_path / "summary_by_group.csv").exists()
        assert not (output_path / "summary_by_group_and_subset.csv").exists()
        launched.append(output_path)
        return {
            "status": "launched",
            "status_path": str(output_path / "analysis" / "status.json"),
            "report_path": str(output_path / "analysis" / "report.json"),
        }

    output_path = tmp_path / "out"
    output_path.mkdir()
    (output_path / "summary_by_group.csv").write_text("stale\n", encoding="utf-8")
    (output_path / "summary_by_group_and_subset.csv").write_text("stale\n", encoding="utf-8")

    monkeypatch.setattr(
        benchmarking_cli,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "single_timeout": 30,
                "no_timeout": False,
                "chemqa_timeout": 30,
                "max_unchanged_status_polls": 1,
                "max_recovery_attempts": 1,
                "groups": "single_llm_skills_off",
                "benchmark_root": str(tmp_path),
                "files": None,
                "datasets": None,
                "list_datasets": False,
                "subsets": None,
                "random_count_per_subset": None,
                "random_seed": 0,
                "offset": 0,
                "limit": None,
                "print_selected_records": False,
                "exact_output_dir": str(tmp_path / "out"),
                "output_dir": str(tmp_path / "out-parent"),
                "openclaw_config": str(tmp_path / "openclaw.json"),
                "single_agent_model": "openai/gpt-5.4",
                "single_agent_thinking": "medium",
                "judge_model": "openai/gpt-5.4",
                "judge_agent_thinking": "minimal",
                "single_agent_id_override": None,
                "judge_agent": "benchmark-judge",
                "judge_timeout": 30,
                "max_concurrent_groups": 1,
                "inter_wave_delay_seconds": 0,
                "chemqa_root": str(tmp_path / "chemqa"),
                "chemqa_model_profile": "profile",
                "review_rounds": None,
                "rebuttal_rounds": None,
                "merge_existing_per_record": False,
            },
        )(),
    )
    monkeypatch.setattr(benchmarking_cli, "select_dataset_files", lambda args: [tmp_path / "demo.jsonl"])
    monkeypatch.setattr(benchmarking_cli, "load_records", lambda paths: [record])
    monkeypatch.setattr(benchmarking_cli, "check_all_skill_health", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        benchmarking_cli,
        "summarize_skill_health",
        lambda reports: {"available_skill_count": 0, "unavailable_skill_count": 0, "available_skills": [], "unavailable_skills": []},
    )
    monkeypatch.setattr(benchmarking_cli, "ConfigPool", FakeConfigPool)
    monkeypatch.setattr(benchmarking_cli, "JudgeClient", lambda **kwargs: object())
    monkeypatch.setattr(
        benchmarking_cli,
        "run_benchmark_web_search_preflight",
        lambda **kwargs: {
            "enabled": True,
            "provider": "duckduckgo",
            "available": True,
            "reports": {"single_llm_skills_off": {"available": True}},
        },
    )
    monkeypatch.setattr(benchmarking_cli, "run_pending_cleanroom_cleanup", lambda: [])
    monkeypatch.setattr(benchmarking_cli, "run_group", fake_run_group)
    monkeypatch.setattr(benchmarking_cli, "launch_automated_evaluation", fake_launch)

    assert benchmarking_cli.main() == 0

    assert launched == [tmp_path / "out"]
    manifest = benchmarking_cli.json.loads((tmp_path / "out" / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["automated_evaluation"]["status"] == "launched"
    assert manifest["groups"]["single_llm_skills_off"]["single_agent_thinking"] == "medium"
    assert manifest["groups"]["single_llm_skills_off"]["group"]["websearch"] is False
    assert manifest["judge"]["thinking"] == "minimal"
    assert manifest["timeout_mode"] == "bounded"
    results = benchmarking_cli.json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert results["timeout_mode"] == "bounded"
    assert results["run_groups"][0]["websearch"] is False


def test_main_skips_automated_evaluation_when_no_analysis_is_set(monkeypatch, tmp_path) -> None:
    record = benchmarking_cli.BenchmarkRecord(
        record_id="record-1",
        dataset="demo",
        source_file="/tmp/demo.jsonl",
        prompt="Question?",
        reference_answer="Answer",
        eval_kind="generic_semantic",
        grading=benchmarking_cli.GradingSpec(
            kind="generic_semantic",
            reference_answer="Answer",
            subset="demo_subset",
        ),
    )
    launched: list[Path] = []

    class FakeConfigPool:
        def __init__(self, *args, **kwargs) -> None:
            self.context = type("Context", (), {"experiment_specs": benchmarking_cli.EXPERIMENT_SPECS})()

        def config_for_group(self, group):
            path = tmp_path / "out" / "runtime-config" / f"{group.id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

        def judge_config_path(self):
            path = tmp_path / "out" / "runtime-config" / "judge.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

    def fake_run_group(**kwargs):
        return [
            benchmarking_cli.GroupRecordResult(
                schema_version=2,
                group_id="single_llm_skills_off",
                group_label="single off",
                runner="single_llm",
                websearch=False,
                record_id="record-1",
                subset="demo_subset",
                dataset="demo",
                source_file="/tmp/demo.jsonl",
                eval_kind="generic_semantic",
                prompt="Question?",
                reference_answer="Answer",
                answer_text="FINAL ANSWER: Answer",
                evaluation={
                    "eval_kind": "generic_semantic",
                    "score": 1.0,
                    "max_score": 1.0,
                    "normalized_score": 1.0,
                    "passed": True,
                    "primary_metric": "semantic_match",
                    "primary_metric_direction": "higher_is_better",
                    "details": {},
                },
                runner_meta={},
                raw={},
                elapsed_seconds=0.1,
                run_lifecycle_status="completed",
                protocol_completion_status="completed",
                protocol_acceptance_status=None,
                answer_availability="native_final",
                answer_reliability="native",
                evaluable=True,
                scored=True,
                recovery_mode="none",
                degraded_execution=False,
                skills_enabled=False,
                execution_error_kind=None,
                error=None,
                short_answer_text="Answer",
                full_response_text="FINAL ANSWER: Answer",
            )
        ]

    monkeypatch.setattr(
        benchmarking_cli,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "single_timeout": 30,
                "no_timeout": False,
                "no_analysis": True,
                "chemqa_timeout": 30,
                "max_unchanged_status_polls": 1,
                "max_recovery_attempts": 1,
                "groups": "single_llm_skills_off",
                "benchmark_root": str(tmp_path),
                "files": None,
                "datasets": None,
                "list_datasets": False,
                "subsets": None,
                "random_count_per_subset": None,
                "random_seed": 0,
                "offset": 0,
                "limit": None,
                "print_selected_records": False,
                "exact_output_dir": str(tmp_path / "out"),
                "output_dir": str(tmp_path / "out-parent"),
                "openclaw_config": str(tmp_path / "openclaw.json"),
                "single_agent_model": "openai/gpt-5.4",
                "single_agent_thinking": "medium",
                "judge_model": "openai/gpt-5.4",
                "judge_agent_thinking": "minimal",
                "single_agent_id_override": None,
                "judge_agent": "benchmark-judge",
                "judge_timeout": 30,
                "max_concurrent_groups": 1,
                "inter_wave_delay_seconds": 0,
                "chemqa_root": str(tmp_path / "chemqa"),
                "chemqa_model_profile": "profile",
                "review_rounds": None,
                "rebuttal_rounds": None,
                "merge_existing_per_record": False,
            },
        )(),
    )
    monkeypatch.setattr(benchmarking_cli, "select_dataset_files", lambda args: [tmp_path / "demo.jsonl"])
    monkeypatch.setattr(benchmarking_cli, "load_records", lambda paths: [record])
    monkeypatch.setattr(benchmarking_cli, "check_all_skill_health", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        benchmarking_cli,
        "summarize_skill_health",
        lambda reports: {"available_skill_count": 0, "unavailable_skill_count": 0, "available_skills": [], "unavailable_skills": []},
    )
    monkeypatch.setattr(benchmarking_cli, "ConfigPool", FakeConfigPool)
    monkeypatch.setattr(benchmarking_cli, "JudgeClient", lambda **kwargs: object())
    monkeypatch.setattr(
        benchmarking_cli,
        "run_benchmark_web_search_preflight",
        lambda **kwargs: {
            "enabled": True,
            "provider": "duckduckgo",
            "available": True,
            "reports": {"single_llm_skills_off": {"available": True}},
        },
    )
    monkeypatch.setattr(benchmarking_cli, "run_pending_cleanroom_cleanup", lambda: [])
    monkeypatch.setattr(benchmarking_cli, "run_group", fake_run_group)
    monkeypatch.setattr(benchmarking_cli, "launch_automated_evaluation", lambda output_root: launched.append(Path(output_root)))

    assert benchmarking_cli.main() == 0

    assert launched == []
    manifest = benchmarking_cli.json.loads((tmp_path / "out" / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["automated_evaluation"]["status"] == "skipped"
    assert manifest["automated_evaluation"]["reason"] == "disabled_by_cli"
    assert manifest["automated_evaluation"]["status_path"] == str(tmp_path / "out" / "analysis" / "status.json")
    results = benchmarking_cli.json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert results["records"] == 1
    assert results["results"][0]["record_id"] == "record-1"


def test_main_ignores_automated_evaluation_launch_failure(monkeypatch, tmp_path) -> None:
    record = benchmarking_cli.BenchmarkRecord(
        record_id="record-1",
        dataset="demo",
        source_file="/tmp/demo.jsonl",
        prompt="Question?",
        reference_answer="Answer",
        eval_kind="generic_semantic",
        grading=benchmarking_cli.GradingSpec(kind="generic_semantic", reference_answer="Answer", subset="demo_subset"),
    )

    class FakeConfigPool:
        def __init__(self, *args, **kwargs) -> None:
            self.context = type("Context", (), {"experiment_specs": benchmarking_cli.EXPERIMENT_SPECS})()

        def config_for_group(self, group):
            path = tmp_path / "out" / "runtime-config" / f"{group.id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

        def judge_config_path(self):
            path = tmp_path / "out" / "runtime-config" / "judge.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            return path

    def fake_run_group(**kwargs):
        return []

    monkeypatch.setattr(
        benchmarking_cli,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "single_timeout": 30,
                "no_timeout": False,
                "chemqa_timeout": 30,
                "max_unchanged_status_polls": 1,
                "max_recovery_attempts": 1,
                "groups": "single_llm_skills_off",
                "benchmark_root": str(tmp_path),
                "files": None,
                "datasets": None,
                "list_datasets": False,
                "subsets": None,
                "random_count_per_subset": None,
                "random_seed": 0,
                "offset": 0,
                "limit": None,
                "print_selected_records": False,
                "exact_output_dir": str(tmp_path / "out"),
                "output_dir": str(tmp_path / "out-parent"),
                "openclaw_config": str(tmp_path / "openclaw.json"),
                "single_agent_model": "openai/gpt-5.4",
                "single_agent_thinking": "high",
                "judge_model": "openai/gpt-5.4",
                "judge_agent_thinking": "high",
                "single_agent_id_override": None,
                "judge_agent": "benchmark-judge",
                "judge_timeout": 30,
                "max_concurrent_groups": 1,
                "inter_wave_delay_seconds": 0,
                "chemqa_root": str(tmp_path / "chemqa"),
                "chemqa_model_profile": "profile",
                "review_rounds": None,
                "rebuttal_rounds": None,
                "merge_existing_per_record": False,
            },
        )(),
    )
    monkeypatch.setattr(benchmarking_cli, "select_dataset_files", lambda args: [tmp_path / "demo.jsonl"])
    monkeypatch.setattr(benchmarking_cli, "load_records", lambda paths: [record])
    monkeypatch.setattr(benchmarking_cli, "check_all_skill_health", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        benchmarking_cli,
        "summarize_skill_health",
        lambda reports: {"available_skill_count": 0, "unavailable_skill_count": 0, "available_skills": [], "unavailable_skills": []},
    )
    monkeypatch.setattr(benchmarking_cli, "ConfigPool", FakeConfigPool)
    monkeypatch.setattr(benchmarking_cli, "JudgeClient", lambda **kwargs: object())
    monkeypatch.setattr(
        benchmarking_cli,
        "run_benchmark_web_search_preflight",
        lambda **kwargs: {
            "enabled": True,
            "provider": "duckduckgo",
            "available": True,
            "reports": {"single_llm_skills_off": {"available": True}},
        },
    )
    monkeypatch.setattr(benchmarking_cli, "run_pending_cleanroom_cleanup", lambda: [])
    monkeypatch.setattr(benchmarking_cli, "run_group", fake_run_group)
    monkeypatch.setattr(benchmarking_cli, "launch_automated_evaluation", lambda output_root: (_ for _ in ()).throw(RuntimeError("boom")))

    assert benchmarking_cli.main() == 0

    manifest = benchmarking_cli.json.loads((tmp_path / "out" / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["automated_evaluation"]["status"] == "launch_failed"
    assert "boom" in manifest["automated_evaluation"]["error"]
