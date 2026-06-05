from __future__ import annotations

import re
from typing import Any


SKIP_TRACE_RE = re.compile(r"\bskill\s+trace\s*:\s*skipped\b", re.IGNORECASE)


def build_skill_use_audit(
    *,
    skills_enabled: bool,
    configured_skills: tuple[str, ...] | list[str],
    runner_meta: dict[str, Any],
    final_response_text: str,
    skill_health_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_summary = runner_meta.get("toolSummary") or {}
    calls = int(tool_summary.get("calls") or 0) if isinstance(tool_summary, dict) else 0
    raw_tools = tool_summary.get("tools") if isinstance(tool_summary, dict) else []
    tool_names = [str(item) for item in raw_tools] if isinstance(raw_tools, list) else []
    configured = [str(skill) for skill in configured_skills]
    declared_skip = bool(SKIP_TRACE_RE.search(str(final_response_text or "")))
    convergence = runner_meta.get("convergence") if isinstance(runner_meta, dict) else {}
    convergence = convergence if isinstance(convergence, dict) else {}
    convergence_tool_names = _tool_names_from_convergence(convergence)
    exec_tool_names = [name for name in convergence_tool_names if name == "exec"]
    exec_tool_call_count = len(exec_tool_names)
    exec_tool_failure_count = _int_meta(convergence.get("exec_tool_result_error_count"))
    skill_tools_available = bool(skills_enabled and configured)
    skill_tool_names = exec_tool_names if skill_tools_available else []
    skill_tool_call_count = exec_tool_call_count if skill_tools_available else 0
    skill_tool_failure_count = exec_tool_failure_count if skill_tools_available else 0
    openclaw_tool_call_count = _int_meta(convergence.get("tool_call_count"))
    if openclaw_tool_call_count == 0 and convergence_tool_names:
        openclaw_tool_call_count = len(convergence_tool_names)
    if openclaw_tool_call_count == 0:
        openclaw_tool_call_count = calls
    return {
        "skills_enabled": bool(skills_enabled),
        "available_skill_count": len(configured),
        "available_skills": configured,
        "openclaw_tool_call_count": openclaw_tool_call_count,
        "openclaw_tool_names": convergence_tool_names or tool_names,
        "tool_call_count": calls,
        "tool_names": tool_names,
        "tool_failure_count": int(tool_summary.get("failures") or 0) if isinstance(tool_summary, dict) else 0,
        "exec_tool_call_count": exec_tool_call_count,
        "exec_tool_names": exec_tool_names,
        "exec_tool_failure_count": exec_tool_failure_count,
        "skill_tool_call_count": skill_tool_call_count,
        "skill_tool_names": skill_tool_names,
        "skill_tool_failure_count": skill_tool_failure_count,
        "missing_skill_doc_read_count": _int_meta(convergence.get("missing_skill_doc_read_count")),
        "tool_result_error_count": _int_meta(convergence.get("tool_result_error_count")),
        "request_shape_error_count": _int_meta(convergence.get("request_shape_error_count")),
        "exec_tool_result_error_count": _int_meta(convergence.get("exec_tool_result_error_count")),
        "exec_request_shape_error_count": _int_meta(convergence.get("exec_request_shape_error_count")),
        "coverage_checklist_present": bool(convergence.get("coverage_checklist_present")),
        "skill_tool_executed": bool(skill_tool_call_count > 0),
        "model_declared_skip": declared_skip,
        "no_tool_call": bool(calls == 0),
        "no_skill_tool_call": bool(skill_tool_call_count == 0),
        "skill_health_summary": dict(skill_health_summary or {}),
    }


def _int_meta(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _tool_names_from_convergence(convergence: dict[str, Any]) -> list[str]:
    raw_tool_names = convergence.get("tool_names")
    if not isinstance(raw_tool_names, list):
        return []
    return [str(item) for item in raw_tool_names]
