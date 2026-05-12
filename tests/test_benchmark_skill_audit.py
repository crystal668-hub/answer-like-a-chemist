from __future__ import annotations

from benchmarking.skills.audit import build_skill_use_audit


def test_skill_use_audit_detects_tool_calls() -> None:
    audit = build_skill_use_audit(
        skills_enabled=True,
        configured_skills=("chem-calculator", "rdkit"),
        runner_meta={"toolSummary": {"calls": 2, "tools": ["write", "exec"], "failures": 0}},
        final_response_text="FINAL ANSWER: 7.59",
    )

    assert audit["skills_enabled"] is True
    assert audit["available_skill_count"] == 2
    assert audit["tool_call_count"] == 2
    assert audit["skill_tool_executed"] is True
    assert audit["model_declared_skip"] is False
    assert audit["no_tool_call"] is False


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
    assert audit["no_tool_call"] is True


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
