from __future__ import annotations

from benchmarking.core.records import BenchmarkRecord
from benchmarking.skills.tree import render_top_level_skill_tree


def build_single_llm_prompt(
    record: BenchmarkRecord,
    *,
    websearch_enabled: bool,
    skills_enabled: bool = True,
    configured_skills: set[str] | None = None,
    time_budget_seconds: int | None = None,
) -> str:
    if record.eval_kind != "verifier_grounded":
        raise ValueError("Single-LLM prompts require eval_kind=verifier_grounded")
    instructions: list[str] = []
    if isinstance(time_budget_seconds, int) and time_budget_seconds > 0:
        instructions.append(f"Time budget: {time_budget_seconds} seconds for the whole answer attempt.")
    if skills_enabled:
        instructions.append(render_top_level_skill_tree(configured_skills=configured_skills))

    prefix = "\n".join(instructions)
    question = record.prompt
    return (prefix + "\n\n" if prefix else "") + question.strip()
