from benchmarking.core.experiments import ExperimentGroup, ExperimentSpec
from benchmarking.core.defaults import BENCHMARK_SKILLS_ALLOWLIST, BASELINE_AGENT_IDS

EXPERIMENT_GROUPS = {
    'single_llm_skills_on': ExperimentGroup(
        id="single_llm_skills_on",
        label="单一 LLM + benchmark skills allowlist",
        runner="single_llm",
        websearch=False,
        skills_enabled=True,
    ),
    'single_llm_skills_off': ExperimentGroup(
        id="single_llm_skills_off",
        label="单一 LLM + 禁用 skills",
        runner="single_llm",
        websearch=False,
        skills_enabled=False,
    ),
}

EXPERIMENT_SPECS = {
    'single_llm_skills_on': ExperimentSpec(
        id="single_llm_skills_on",
        label=EXPERIMENT_GROUPS["single_llm_skills_on"].label,
        runner_kind="single_llm",
        websearch_enabled=False,
        skills_enabled=True,
        single_agent_id=BASELINE_AGENT_IDS["single_llm_skills_on"],
        skill_allowlist=tuple(BENCHMARK_SKILLS_ALLOWLIST),
    ),
    'single_llm_skills_off': ExperimentSpec(
        id="single_llm_skills_off",
        label=EXPERIMENT_GROUPS["single_llm_skills_off"].label,
        runner_kind="single_llm",
        websearch_enabled=False,
        skills_enabled=False,
        single_agent_id=BASELINE_AGENT_IDS["single_llm_skills_off"],
        skill_allowlist=(),
    ),
}

