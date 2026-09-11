"""Composition of the actively maintained single-LLM business."""
from benchmarking.core.convergence import ConvergencePolicy
from benchmarking.core.defaults import BENCHMARK_SKILLS_ALLOWLIST
from . import experiments
from .orchestration import runner_options

NAME = "single"
STATUS = "active"
USES_ATTEMPT_QUEUE = True
SCHEDULING_MODE = "single-llm-queue"


def add_arguments(parser):
    parser.set_defaults(max_concurrent_groups=1, inter_wave_delay_seconds=0)


def convergence_metadata(args):
    return {"single_llm": ConvergencePolicy(timeout_seconds=args.single_timeout).to_meta()}


def group_waves(group_ids, args):
    return [group_ids]


def group_metadata(group_id, args):
    return {}


def cleanup():
    return []


def make_runner_options(*, args, group, output_root, config_path, single_agent, workspace_manager,
                        cancellation_token, process_registry, pypi_cutoff, admission_controller):
    from benchmarking.workflow.cli import parse_retry_backoff_seconds
    retries = max(0, int(getattr(args, "single_timeout_retries", 3)))
    return runner_options(
        group=group, output_root=output_root, config_path=config_path, single_agent=single_agent,
        single_timeout=args.single_timeout, experiment_specs=experiments.EXPERIMENT_SPECS,
        single_agent_thinking=args.single_agent_thinking,
        single_timeout_retries=retries,
        single_timeout_retry_backoff_seconds=parse_retry_backoff_seconds(
            str(getattr(args, "single_timeout_retry_backoff_seconds", "5,15,45")), max_retries=retries),
        no_timeout=bool(getattr(args, "no_timeout", False)), workspace_manager=workspace_manager,
        cancellation_token=cancellation_token, process_registry=process_registry, pypi_cutoff=pypi_cutoff,
        vgb_skill_allowlist=tuple(BENCHMARK_SKILLS_ALLOWLIST), admission_controller=admission_controller,
        manage_group_lifecycle=False, execution_backend=getattr(args, "execution_backend", "docker"),
        container_image=getattr(args, "container_image", "openclaw-benchmark-single-llm:latest"),
        container_cpus=getattr(args, "container_cpus", None),
        container_memory_bytes=getattr(args, "container_memory_bytes", None),
        container_pids_limit=getattr(args, "container_pids_limit", None),
    )

from benchmarking.runtime.agent_workspace import default_workspace_templates as workspace_templates
from .config import build_runner_config as build_runner_config

__all__ = ["workspace_templates", "build_runner_config"]
