from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from benchmarking.core.experiments import ExperimentSpec
from benchmarking.skills.tree import benchmark_skill_allowlist
from benchmarking.workflow.errors import BenchmarkError


DEFAULT_SINGLE_AGENT = "benchmark-single-skills-off"
DEFAULT_SINGLE_AGENT_MODEL = "qwen3.5-plus"
DEFAULT_JUDGE_AGENT = "benchmark-judge"
DEFAULT_JUDGE_MODEL = "openai/gpt-5.5"
DEFAULT_CHEMQA_PRESET = "chemqa-review@1"
DEFAULT_CHEMQA_MODEL_PROFILE = "chemqa-review-su8-coord-qwen-ds-kimi-glm-minimax"
THINKING_LEVEL_CHOICES = ("off", "minimal", "low", "medium", "high", "xhigh")
DEFAULT_SINGLE_AGENT_THINKING = "high"
DEFAULT_JUDGE_AGENT_THINKING = "high"
BENCHMARK_SKILLS_ALLOWLIST = list(benchmark_skill_allowlist())
CHEMQA_SLOT_SETS = {
    "chemqa_skills_on": "A",
}
BASELINE_AGENT_IDS = {
    "single_llm_skills_on": "benchmark-single-skills-on",
    "single_llm_skills_off": "benchmark-single-skills-off",
}
JUDGE_AGENT_ID = "benchmark-judge"


@dataclass(frozen=True)
class ExperimentGroup:
    id: str
    label: str
    runner: str
    websearch: bool
    skills_enabled: bool = True


EXPERIMENT_GROUPS: dict[str, ExperimentGroup] = {
    "single_llm_skills_on": ExperimentGroup(
        id="single_llm_skills_on",
        label="单一 LLM + benchmark skills allowlist",
        runner="single_llm",
        websearch=False,
        skills_enabled=True,
    ),
    "single_llm_skills_off": ExperimentGroup(
        id="single_llm_skills_off",
        label="单一 LLM + 禁用 skills",
        runner="single_llm",
        websearch=False,
        skills_enabled=False,
    ),
    "chemqa_skills_on": ExperimentGroup(
        id="chemqa_skills_on",
        label="ChemQA fixed-lane review + benchmark skills allowlist",
        runner="chemqa",
        websearch=False,
        skills_enabled=True,
    ),
}

EXPERIMENT_SPECS: dict[str, ExperimentSpec] = {
    "single_llm_skills_on": ExperimentSpec(
        id="single_llm_skills_on",
        label=EXPERIMENT_GROUPS["single_llm_skills_on"].label,
        runner_kind="single_llm",
        websearch_enabled=False,
        skills_enabled=True,
        single_agent_id=BASELINE_AGENT_IDS["single_llm_skills_on"],
        skill_allowlist=tuple(BENCHMARK_SKILLS_ALLOWLIST),
    ),
    "single_llm_skills_off": ExperimentSpec(
        id="single_llm_skills_off",
        label=EXPERIMENT_GROUPS["single_llm_skills_off"].label,
        runner_kind="single_llm",
        websearch_enabled=False,
        skills_enabled=False,
        single_agent_id=BASELINE_AGENT_IDS["single_llm_skills_off"],
        skill_allowlist=(),
    ),
    "chemqa_skills_on": ExperimentSpec(
        id="chemqa_skills_on",
        label=EXPERIMENT_GROUPS["chemqa_skills_on"].label,
        runner_kind="chemqa",
        websearch_enabled=False,
        skills_enabled=True,
        slot_set=CHEMQA_SLOT_SETS["chemqa_skills_on"],
        skill_allowlist=tuple(BENCHMARK_SKILLS_ALLOWLIST),
    ),
}


def build_effective_experiment_specs(
    specs: dict[str, ExperimentSpec],
    *,
    skill_health_reports: dict[str, dict[str, Any]],
) -> dict[str, ExperimentSpec]:
    available = {skill for skill, report in skill_health_reports.items() if report.get("available") is True}
    effective: dict[str, ExperimentSpec] = {}
    for group_id, spec in specs.items():
        if spec.skills_enabled and spec.skill_allowlist:
            filtered = tuple(skill for skill in spec.skill_allowlist if skill in available)
            effective[group_id] = replace(spec, skill_allowlist=filtered)
        else:
            effective[group_id] = spec
    return effective


def select_group_ids(raw: str) -> list[str]:
    group_ids = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [item for item in group_ids if item not in EXPERIMENT_GROUPS]
    if unknown:
        raise BenchmarkError(f"Unknown group ids: {', '.join(unknown)}")
    if not group_ids:
        raise BenchmarkError("No experiment groups selected.")
    return group_ids
