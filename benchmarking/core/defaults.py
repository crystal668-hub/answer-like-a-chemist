from benchmarking.skills.tree import benchmark_skill_allowlist

DEFAULT_SINGLE_AGENT = "benchmark-single-skills-off"
DEFAULT_SINGLE_AGENT_MODEL = "qwen3.5-plus"
DEFAULT_JUDGE_AGENT = "benchmark-judge"
DEFAULT_JUDGE_MODEL = "openai/gpt-5.5"
THINKING_LEVEL_CHOICES = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "adaptive",
)
DEFAULT_SINGLE_AGENT_THINKING = "high"
DEFAULT_JUDGE_AGENT_THINKING = "high"
BENCHMARK_SKILLS_ALLOWLIST = list(benchmark_skill_allowlist())
BASELINE_AGENT_IDS = {
    "single_llm_skills_on": "benchmark-single-skills-on",
    "single_llm_skills_off": "benchmark-single-skills-off",
}
JUDGE_AGENT_ID = "benchmark-judge"


