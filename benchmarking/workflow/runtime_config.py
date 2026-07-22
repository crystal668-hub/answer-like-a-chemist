from __future__ import annotations

from pathlib import Path
from typing import Any

from benchmarking.core.experiments import ExperimentSpec
from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime.agent_workspace import AttemptWorkspaceManager, default_workspace_templates
from benchmarking.runtime.config_pool import (
    RuntimeConfigContext,
    RuntimeConfigError,
    build_run_scoped_config_payload as _build_run_scoped_config_payload,
)
from benchmarking.runtime.workspace_policy import ProtectedRoot
from benchmarking.workflow.errors import BenchmarkError
from benchmarking.workflow.experiments import (
    CHEMQA_SLOT_SETS,
    EXPERIMENT_SPECS,
    JUDGE_AGENT_ID,
    ExperimentGroup,
)


def runtime_config_context(experiment_specs: dict[str, ExperimentSpec] | None = None) -> RuntimeConfigContext:
    return RuntimeConfigContext(
        agents_root=runtime_paths.agents_root,
        judge_agent_id=JUDGE_AGENT_ID,
        chemqa_slot_sets=CHEMQA_SLOT_SETS,
        experiment_specs=experiment_specs or EXPERIMENT_SPECS,
        benchmark_skills_root=runtime_paths.skills_root,
    )


def build_production_protected_roots(*, runtime_root: Path, output_root: Path) -> tuple[ProtectedRoot, ...]:
    roots = [
        ProtectedRoot("benchmark_dataset_root", runtime_paths.benchmarks_root, "runtime_paths.benchmarks_root"),
        ProtectedRoot(
            "temp_benchmark_dataset_root",
            runtime_paths.temp_benchmarks_root,
            "runtime_paths.temp_benchmarks_root",
        ),
        ProtectedRoot(
            "verifier_release_root",
            runtime_paths.data_root / "verifier-grounded-releases",
            'runtime_paths.data_root / "verifier-grounded-releases"',
        ),
        ProtectedRoot(
            "verifier_runtime_root",
            runtime_paths.project_state_root / "verifier-grounded-runtimes",
            'runtime_paths.project_state_root / "verifier-grounded-runtimes"',
        ),
        ProtectedRoot(
            "verifier_resource_root",
            runtime_paths.project_root / "benchmarking" / "resources" / "verifier_grounded",
            'runtime_paths.project_root / "benchmarking/resources/verifier_grounded"',
        ),
        ProtectedRoot("benchmark_runtime_root", runtime_root, "AttemptWorkspaceManager.runtime_root"),
        ProtectedRoot(
            "benchmark_results_root",
            runtime_paths.project_state_root / "benchmark-runs",
            'runtime_paths.project_state_root / "benchmark-runs"',
        ),
        ProtectedRoot("current_output_root", output_root, "AttemptWorkspaceManager.output_root"),
        ProtectedRoot("agents_root", runtime_paths.agents_root, "runtime_paths.agents_root"),
    ]
    roots.extend(
        ProtectedRoot(
            "legacy_benchmark_workspace",
            runtime_paths.benchmark_runtime_root / name,
            f"runtime_paths.benchmark_runtime_root / {name}",
        )
        for name in (
            "benchmark-single-skills-on",
            "benchmark-single-skills-off",
            "benchmark-judge",
            "custom-single-agent",
        )
    )
    return tuple(roots)


def _compatibility_protected_roots(*, runtime_root: Path, output_root: Path) -> tuple[ProtectedRoot, ...]:
    return (
        ProtectedRoot("benchmark_runtime_root", runtime_root, "compatibility.runtime_root"),
        ProtectedRoot("current_output_root", output_root, "compatibility.output_root"),
    )


def build_run_scoped_config_payload(
    base_payload: dict[str, Any],
    *,
    group: ExperimentGroup,
    single_agent_model: str,
    judge_model: str,
    workspace_manager: AttemptWorkspaceManager | None = None,
    single_agent_id_override: str | None = None,
) -> dict[str, Any]:
    workspace_manager = workspace_manager or AttemptWorkspaceManager(
        runtime_root=runtime_paths.benchmark_runtime_root / "runs",
        output_root=runtime_paths.project_state_root / "benchmark-config-preview",
        run_id="config-preview",
        invocation_id="config-preview",
        templates=default_workspace_templates(runtime_paths.project_root),
        protected_roots=_compatibility_protected_roots(
            runtime_root=runtime_paths.benchmark_runtime_root / "runs",
            output_root=runtime_paths.project_state_root / "benchmark-config-preview",
        ),
    )
    try:
        return _build_run_scoped_config_payload(
            base_payload,
            context=runtime_config_context(),
            group=group,
            single_agent_model=single_agent_model,
            judge_model=judge_model,
            workspace_manager=workspace_manager,
            single_agent_id_override=single_agent_id_override,
        )
    except RuntimeConfigError as exc:
        raise BenchmarkError(str(exc)) from exc
