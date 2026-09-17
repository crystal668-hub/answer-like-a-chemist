"""Frozen ChemQA execution for pinned verifier-grounded tracks."""
from pathlib import Path

from benchmarking.runtime import paths
from benchmarking.runtime.agent_workspace import (
    WorkspaceTemplate,
    default_workspace_templates,
)
from benchmarking.scoring.registry import DEFAULT_EVALUATORS
from benchmarking.workflow.track_selection import (
    filter_records_by_ids,
    load_vgb_records,
    select_track_files,
)

from . import experiments
from .convergence import ChemQAConvergencePolicy as ConvergencePolicy
from .orchestration import runner_options

NAME = "chemdebate"
STATUS = "legacy-frozen"
USES_ATTEMPT_QUEUE = False
SCHEDULING_MODE = "legacy-chemqa-waves"
DEFAULT_CHEMQA_ROOT = paths.skills_root / "chemqa-review"
USES_JUDGE = False

def select_records(files, args):
    return filter_records_by_ids(load_vgb_records(files), args.record_ids)


def evaluator_registry():
    return dict(DEFAULT_EVALUATORS)


def add_arguments(parser):
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

from .adapter import run_pending_cleanroom_cleanup as cleanup
from .config import build_runner_config as build_runner_config

__all__ = ["build_runner_config", "cleanup", "select_track_files"]
