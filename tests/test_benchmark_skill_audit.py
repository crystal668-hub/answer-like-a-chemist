from __future__ import annotations

from benchmarking.skills.audit import build_skill_use_audit


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
