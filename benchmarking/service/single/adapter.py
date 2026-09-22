from __future__ import annotations

import time
import uuid
from pathlib import Path

from benchmarking.core.answer_processing import normalize_answer_tracks
from benchmarking.core.convergence import ConvergencePolicy
from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime import subprocess_utils
from benchmarking.runtime.agent_workspace import (
    AttemptWorkspaceManager,
    default_workspace_templates,
)
from benchmarking.runtime.cancellation import (
    CancellationToken,
    OwnedProcessRegistry,
)
from benchmarking.runtime.container_runtime import DockerContainerRuntime
from benchmarking.runtime.workspace_policy import ContaminationAudit
from benchmarking.core.defaults import (
    DEFAULT_SINGLE_AGENT_THINKING,
)
from benchmarking.service.single.prompts import build_single_llm_prompt
from benchmarking.workflow.run_state import slugify
from benchmarking.service.single.runner import SingleLLMRunner as BaseSingleLLMRunner

from benchmarking.runtime.runner_support import _compatibility_protected_roots, _CancellationRunnerMixin

class SingleLLMRunner(_CancellationRunnerMixin, BaseSingleLLMRunner):
    def __init__(
        self,
        *,
        agent_id: str,
        timeout_seconds: int,
        config_path: Path,
        runtime_bundle_root: Path,
        configured_skills: tuple[str, ...] | list[str] = (),
        vgb_configured_skills: tuple[str, ...] | list[str] = (),
        convergence_policy: ConvergencePolicy | None = None,
        timeout_retries: int = 3,
        timeout_retry_backoff_seconds: tuple[int | float, ...] | list[int | float] = (5, 15, 45),
        sleep_fn=time.sleep,
        benchmark_agent_thinking: str = DEFAULT_SINGLE_AGENT_THINKING,
        no_timeout: bool = False,
        pypi_cutoff: str | None = None,
        workspace_manager: AttemptWorkspaceManager | None = None,
        contamination_auditor=None,
        cancellation_token: CancellationToken | None = None,
        process_registry: OwnedProcessRegistry | None = None,
        execution_backend: str = "docker",
        container_image: str = "openclaw-benchmark-single-llm:latest",
        container_cpus: float | None = None,
        container_memory_bytes: int | None = None,
        container_pids_limit: int | None = None,
        container_network=None,
        admission_controller=None,
    ) -> None:
        self._cancellation_enabled = cancellation_token is not None
        self._cancellation_token = cancellation_token or CancellationToken()
        self._process_registry = process_registry or OwnedProcessRegistry(
            cancellation_token=self._cancellation_token
        )
        compatibility_manager = workspace_manager is None
        if workspace_manager is None:
            compatibility_root = runtime_bundle_root.expanduser().resolve()
            runtime_root = compatibility_root / ".benchmark-test-workspaces" / "runs"
            output_root = compatibility_root / ".benchmark-test-output"
            workspace_manager = AttemptWorkspaceManager(
                runtime_root=runtime_root,
                output_root=output_root,
                run_id="runner-test",
                invocation_id=uuid.uuid4().hex,
                templates=default_workspace_templates(runtime_paths.project_root),
                protected_roots=_compatibility_protected_roots(
                    runtime_root=runtime_root,
                    output_root=output_root,
                ),
            )
        if compatibility_manager and contamination_auditor is None:
            contamination_auditor = lambda **_kwargs: ContaminationAudit(status="clean")
        if str(execution_backend).strip().lower() != "docker":
            raise ValueError("Host single-LLM execution has been retired; use Docker")
        super().__init__(
            agent_id=agent_id,
            timeout_seconds=timeout_seconds,
            config_path=config_path,
            runtime_bundle_root=runtime_bundle_root,
            configured_skills=configured_skills,
            vgb_configured_skills=vgb_configured_skills,
            convergence_policy=convergence_policy,
            timeout_retries=timeout_retries,
            timeout_retry_backoff_seconds=timeout_retry_backoff_seconds,
            sleep_fn=sleep_fn,
            no_timeout=no_timeout,
            pypi_cutoff=pypi_cutoff,
            admission_controller=admission_controller,
            execution_backend=execution_backend,
            container_runtime=DockerContainerRuntime() if execution_backend == "docker" else None,
            container_image=container_image,
            container_cpus=container_cpus,
            container_memory_bytes=container_memory_bytes,
            container_pids_limit=container_pids_limit,
            container_network=container_network,
            run_subprocess=lambda *args, **kwargs: subprocess_utils.run_owned_subprocess(
                *args,
                cancellation_token=self._cancellation_token,
                process_registry=self._process_registry,
                **kwargs,
            ),
            parse_json_stdout=subprocess_utils.parse_json_stdout,
            unwrap_agent_payload=subprocess_utils.unwrap_agent_payload,
            summarize_payloads=subprocess_utils.summarize_payloads,
            normalize_answer_tracks=normalize_answer_tracks,
            ensure_runtime_bundle=lambda record, *, bundle_root: None,
            build_single_llm_prompt=build_single_llm_prompt,
            slugify=slugify,
            benchmark_agent_thinking=benchmark_agent_thinking,
            workspace_manager=workspace_manager,
            allowed_workspace_roots=(
                runtime_paths.skills_root,
                runtime_paths.project_root / "scripts" / "run_skill.py",
            ),
            contamination_auditor=contamination_auditor,
        )
