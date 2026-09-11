"""Active experiment catalog. Legacy execution uses its own entrypoint."""
from benchmarking.core.defaults import (
    DEFAULT_SINGLE_AGENT, DEFAULT_SINGLE_AGENT_MODEL, DEFAULT_JUDGE_AGENT,
    DEFAULT_JUDGE_MODEL, THINKING_LEVEL_CHOICES, DEFAULT_SINGLE_AGENT_THINKING,
    DEFAULT_JUDGE_AGENT_THINKING, BENCHMARK_SKILLS_ALLOWLIST, BASELINE_AGENT_IDS, JUDGE_AGENT_ID,
)
from benchmarking.core.experiments import ExperimentGroup
from benchmarking.service.single.experiments import EXPERIMENT_GROUPS, EXPERIMENT_SPECS
from benchmarking.workflow.errors import BenchmarkError


def select_group_ids(raw: str, *, groups=None) -> list[str]:
    catalog = EXPERIMENT_GROUPS if groups is None else groups
    group_ids = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [item for item in group_ids if item not in catalog]
    if unknown:
        raise BenchmarkError(f"Unknown or inactive group ids: {', '.join(unknown)}. ChemQA is legacy; use benchmarking.service.chemdebate.cli explicitly.")
    if not group_ids:
        raise BenchmarkError("No experiment groups selected.")
    return group_ids
