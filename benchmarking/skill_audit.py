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
) -> dict[str, Any]:
    tool_summary = runner_meta.get("toolSummary") or {}
    calls = int(tool_summary.get("calls") or 0) if isinstance(tool_summary, dict) else 0
    raw_tools = tool_summary.get("tools") if isinstance(tool_summary, dict) else []
    tool_names = [str(item) for item in raw_tools] if isinstance(raw_tools, list) else []
    configured = [str(skill) for skill in configured_skills]
    declared_skip = bool(SKIP_TRACE_RE.search(str(final_response_text or "")))
    return {
        "skills_enabled": bool(skills_enabled),
        "available_skill_count": len(configured),
        "available_skills": configured,
        "tool_call_count": calls,
        "tool_names": tool_names,
        "tool_failure_count": int(tool_summary.get("failures") or 0) if isinstance(tool_summary, dict) else 0,
        "skill_tool_executed": bool(calls > 0),
        "model_declared_skip": declared_skip,
        "no_tool_call": bool(calls == 0),
    }
