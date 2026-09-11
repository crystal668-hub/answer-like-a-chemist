from benchmarking.core.experiments import ExperimentGroup, ExperimentSpec
from benchmarking.core.defaults import BENCHMARK_SKILLS_ALLOWLIST

DEFAULT_CHEMQA_PRESET = "chemqa-review@1"
DEFAULT_CHEMQA_MODEL_PROFILE = "chemqa-review-su8-coord-qwen-ds-kimi-glm-minimax"
CHEMQA_SLOT_SETS = {"chemqa_skills_on": "A"}

EXPERIMENT_GROUPS = {
    'chemqa_skills_on': ExperimentGroup(
        id="chemqa_skills_on",
        label="ChemQA fixed-lane review + benchmark skills allowlist",
        runner="chemqa",
        websearch=False,
        skills_enabled=True,
    ),
}

EXPERIMENT_SPECS = {
    'chemqa_skills_on': ExperimentSpec(
        id="chemqa_skills_on",
        label=EXPERIMENT_GROUPS["chemqa_skills_on"].label,
        runner_kind="chemqa",
        websearch_enabled=False,
        skills_enabled=True,
        slot_set=CHEMQA_SLOT_SETS["chemqa_skills_on"],
        skill_allowlist=tuple(BENCHMARK_SKILLS_ALLOWLIST),
    ),
}

