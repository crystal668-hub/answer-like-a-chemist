"""Frozen legacy ChemQA execution; loaded only by its explicit entrypoint."""
from pathlib import Path
from .convergence import ChemQAConvergencePolicy as ConvergencePolicy
from benchmarking.runtime import paths
from benchmarking.runtime.agent_workspace import default_workspace_templates
from benchmarking.runtime.agent_workspace import WorkspaceTemplate
from benchmarking.scoring.registry import legacy_evaluators as evaluator_registry
from benchmarking.core.datasets import dataset_name_from_file, is_retired_benchmark
from benchmarking.workflow.dataset_selection import select_dataset_files as shared_select_dataset_files
from benchmarking.workflow.errors import BenchmarkError
from . import experiments
from .orchestration import runner_options

NAME = "chemdebate"
STATUS = "legacy-frozen"
USES_ATTEMPT_QUEUE = False
SCHEDULING_MODE = "legacy-chemqa-waves"
DEFAULT_CHEMQA_ROOT = paths.skills_root / "chemqa-review"
USES_JUDGE = True

def select_dataset_files(args):
    requested = [value.strip() for value in str(args.datasets or "").split(",") if value.strip()]
    if any(is_retired_benchmark(dataset=value) for value in requested):
        raise BenchmarkError("This benchmark has been retired; ChemQA execution is unavailable.")
    files = shared_select_dataset_files(args)
    if args.files:
        return files
    return [path for path in files if not is_retired_benchmark(dataset=dataset_name_from_file(path))]


def select_records(files, args):
    from benchmarking.workflow import dataset_selection
    records = dataset_selection.load_records(files)
    if any(is_retired_benchmark(dataset=record.dataset, eval_kind=record.eval_kind,
                               subset=record.grading.subset) for record in records):
        raise BenchmarkError("This benchmark has been retired; ChemQA execution is unavailable.")
    records = dataset_selection.filter_records_by_subsets(records, args.subsets)
    return dataset_selection.filter_records_by_ids(records, args.record_ids)


def sampling_metadata(args):
    return {"enabled": False, "count_per_subset": None, "seed": None}


def add_arguments(parser):
    from benchmarking.workflow import experiments as shared_experiments
    parser.add_argument("--subsets", help="Filter supported record subsets, comma-separated")
    parser.add_argument("--judge-agent", default=shared_experiments.DEFAULT_JUDGE_AGENT, help="rubric / 语义评测所用 judge agent id")
    parser.add_argument(
        "--judge-model",
        default=shared_experiments.DEFAULT_JUDGE_MODEL,
        help="judge runtime model，默认锁定为 openai/gpt-5.5",
    )
    parser.add_argument(
        "--judge-agent-thinking",
        default=shared_experiments.DEFAULT_JUDGE_AGENT_THINKING,
        choices=shared_experiments.THINKING_LEVEL_CHOICES,
        help="judge OpenClaw thinking level，默认 high",
    )
    parser.add_argument("--judge-timeout", type=int, default=300, help="Judge 每次评测超时秒数")
    parser.add_argument("--chemqa-root", default=str(DEFAULT_CHEMQA_ROOT), help="chemqa-review skill 根目录")
    parser.add_argument(
        "--chemqa-model-profile",
        default=experiments.DEFAULT_CHEMQA_MODEL_PROFILE,
        help="ChemQA fixed-lane review 所用 model profile，默认使用当前 benchmark 固定 profile",
    )
    parser.add_argument("--chemqa-timeout", type=int, default=1800, help="ChemQA fixed-lane review 每题超时秒数")
    parser.add_argument(
        "--max-unchanged-status-polls",
        type=int,
        default=2,
        help="ChemQA convergence limit for unchanged status polls",
    )
    parser.add_argument(
        "--max-recovery-attempts",
        type=int,
        default=2,
        help="ChemQA convergence limit for recovery attempts",
    )
    parser.add_argument(
        "--max-concurrent-groups",
        type=int,
        default=2,
        help="Maximum concurrent ChemQA groups per wave (default: 2)",
    )
    parser.add_argument(
        "--inter-wave-delay-seconds",
        type=int,
        default=10,
        help="相邻波次之间的等待秒数，默认 10，用于给系统释放资源的窗口",
    )
    parser.add_argument("--review-rounds", type=int, help="ChemQA review rounds 覆盖值")
    parser.add_argument("--rebuttal-rounds", type=int, help="ChemQA rebuttal rounds 覆盖值")


def convergence_metadata(args):
    return {"chemqa": ConvergencePolicy(timeout_seconds=args.chemqa_timeout,
        max_unchanged_status_polls=args.max_unchanged_status_polls,
        max_recovery_attempts=args.max_recovery_attempts).to_meta()}


def group_waves(group_ids, args):
    from benchmarking.workflow.cli import build_group_waves
    return build_group_waves(group_ids, max_concurrent_groups=args.max_concurrent_groups)


def group_metadata(group_id, args):
    return {"slot_set": experiments.CHEMQA_SLOT_SETS.get(group_id),
            "chemqa_model_profile": args.chemqa_model_profile, "service_status": STATUS}


def workspace_templates(project_root):
    templates = default_workspace_templates(project_root)
    resources = project_root / "benchmarking" / "resources" / "agent-workspace-templates"
    templates["chemqa-role-v1"] = WorkspaceTemplate(
        template_id="chemqa-role-v1", agents_base=resources / "common" / "AGENTS.base.md",
        agents_overlay=Path(__file__).parent / "resources" / "AGENTS.overlay.md")
    return templates


def make_runner_options(*, args, group, output_root, config_path, single_agent, workspace_manager,
                        cancellation_token, process_registry, pypi_cutoff, admission_controller):
    return runner_options(
        group=group, output_root=output_root, config_path=config_path,
        chemqa_root=Path(args.chemqa_root).expanduser().resolve(), chemqa_timeout=args.chemqa_timeout,
        chemqa_model_profile=args.chemqa_model_profile, review_rounds=args.review_rounds,
        rebuttal_rounds=args.rebuttal_rounds,
        chemqa_convergence_policy=ConvergencePolicy(timeout_seconds=args.chemqa_timeout,
            max_unchanged_status_polls=args.max_unchanged_status_polls,
            max_recovery_attempts=args.max_recovery_attempts),
        workspace_manager=workspace_manager, cancellation_token=cancellation_token,
        process_registry=process_registry,
    )

from .config import build_runner_config as build_runner_config
from .adapter import run_pending_cleanroom_cleanup as cleanup
__all__ = ["build_runner_config", "cleanup"]
