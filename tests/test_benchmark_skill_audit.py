from __future__ import annotations

from benchmarking.core.reporting import GroupRecordResult, aggregate_results
from benchmarking.skills.audit import build_skill_use_audit


def _group_result(*, skills_enabled: bool, audit: dict[str, object]) -> GroupRecordResult:
    return GroupRecordResult(
        schema_version=2,
        group_id="g",
        group_label="g",
        runner="single_llm",
        websearch=False,
        record_id="r",
        subset="s",
        dataset="d",
        source_file="/tmp/d.jsonl",
        eval_kind="chembench_open_ended",
        prompt="Q",
        reference_answer="A",
        answer_text="A",
        evaluation={
            "score": 1.0,
            "max_score": 1.0,
            "normalized_score": 1.0,
            "passed": True,
            "details": {},
        },
        runner_meta={"skill_use_audit": audit},
        raw={},
        elapsed_seconds=1.0,
        run_lifecycle_status="completed",
        protocol_completion_status="missing",
        protocol_acceptance_status=None,
        answer_availability="native_final",
        answer_reliability="native",
        evaluable=True,
        scored=True,
        recovery_mode="none",
        degraded_execution=False,
        skills_enabled=skills_enabled,
    )


def test_skill_use_audit_detects_tool_calls() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("chem-calculator", "rdkit"),
        runner_meta={
            "toolSummary": {"calls": 5, "tools": ["read", "exec", "image", "web_search"], "failures": 0},
            "convergence": {"tool_names": ["read", "exec", "image", "web_search", "exec"]},
        },
        final_response_text="FINAL ANSWER: 7.59",
    )

    assert audit["skills_enabled"] is True
    assert audit["available_skill_count"] == 2
    assert audit["tool_call_count"] == 5
    assert audit["openclaw_tool_call_count"] == 5
    assert audit["openclaw_tool_names"] == ["read", "exec", "image", "web_search", "exec"]
    assert audit["exec_tool_call_count"] == 2
    assert audit["exec_tool_failure_count"] == 0
    assert audit["skill_tool_call_count"] == 2
    assert audit["skill_tool_names"] == ["exec", "exec"]
    assert audit["skill_tool_executed"] is True
    assert audit["model_declared_skip"] is False
    assert audit["no_tool_call"] is False
    assert audit["no_skill_tool_call"] is False


def test_skill_use_audit_does_not_count_non_exec_tools_as_skill_tools() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("chem-calculator", "rdkit"),
        runner_meta={
            "toolSummary": {"calls": 3, "tools": ["read", "image", "web_search"], "failures": 1},
            "convergence": {"tool_names": ["read", "image", "web_search"]},
        },
        final_response_text="FINAL ANSWER: B",
    )

    assert audit["tool_call_count"] == 3
    assert audit["openclaw_tool_call_count"] == 3
    assert audit["skill_tool_call_count"] == 0
    assert audit["tool_failure_count"] == 1
    assert audit["skill_tool_failure_count"] == 0
    assert audit["skill_tool_executed"] is False
    assert audit["no_tool_call"] is False
    assert audit["no_skill_tool_call"] is True


def test_skill_use_audit_keeps_skill_off_exec_as_exec_tool_not_skill() -> None:
    audit = build_skill_use_audit(
        skills_enabled=False,
        configured_skills=(),
        runner_meta={
            "toolSummary": {"calls": 2, "tools": ["read", "exec"], "failures": 0},
            "convergence": {
                "tool_call_count": 2,
                "tool_names": ["read", "exec"],
                "exec_tool_result_error_count": 1,
            },
        },
        final_response_text="FINAL ANSWER: B",
    )

    assert audit["openclaw_tool_call_count"] == 2
    assert audit["exec_tool_call_count"] == 1
    assert audit["exec_tool_failure_count"] == 1
    assert audit["skill_tool_call_count"] == 0
    assert audit["skill_tool_failure_count"] == 0
    assert audit["skill_tool_executed"] is False
    assert audit["no_skill_tool_call"] is True


def test_aggregate_results_keeps_legacy_skill_off_exec_out_of_skill_totals() -> None:
    summary = aggregate_results(
        [
            _group_result(
                skills_enabled=False,
                audit={
                    "skills_enabled": False,
                    "openclaw_tool_call_count": 1,
                    "skill_tool_call_count": 1,
                    "skill_tool_failure_count": 1,
                    "skill_tool_executed": True,
                    "no_skill_tool_call": False,
                },
            )
        ]
    )

    bucket = summary["groups"]["g"]
    assert bucket["exec_tool_call_total"] == 1
    assert bucket["exec_tool_failure_total"] == 1
    assert bucket["skill_tool_call_total"] == 0
    assert bucket["skill_tool_failure_total"] == 0
    assert bucket["skill_tool_executed_count"] == 0
    assert bucket["skill_no_tool_call_count"] == 0


def test_skill_use_audit_detects_declared_skip_without_counting_execution() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("rdkit",),
        runner_meta={"toolSummary": {"calls": 0, "tools": [], "failures": 0}},
        final_response_text="SKILL TRACE: skipped rdkit because this is qualitative.",
    )

    assert audit["skill_tool_executed"] is False
    assert audit["model_declared_skip"] is True
    assert audit["no_tool_call"] is True


def test_skill_use_audit_handles_missing_tool_summary() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("paper-retrieval",),
        runner_meta={},
        final_response_text="FINAL ANSWER: A",
    )

    assert audit["tool_call_count"] == 0
    assert audit["tool_names"] == []
    assert audit["skill_tool_call_count"] == 0
    assert audit["no_tool_call"] is True
    assert audit["no_skill_tool_call"] is True


def test_skill_use_audit_includes_health_summary() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("rdkit",),
        runner_meta={},
        final_response_text="FINAL ANSWER: A",
        skill_health_summary={"available_skill_count": 1, "unavailable_skill_count": 2},
    )

    assert audit["skill_health_summary"]["available_skill_count"] == 1
    assert audit["skill_health_summary"]["unavailable_skill_count"] == 2
