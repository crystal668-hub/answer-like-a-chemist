from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from benchmarking.core.answer_processing import normalize_space
from .convergence import ChemQAConvergencePolicy as ConvergencePolicy
from benchmarking.service.chemdebate.status import is_chemqa_success_status
from benchmarking.service.chemdebate.status import is_chemqa_terminal_status
from benchmarking.service.chemdebate.status import normalize_chemqa_run_status
from benchmarking.runtime import bundles as runtime_bundles
from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime import subprocess_utils
from benchmarking.runtime.agent_workspace import (
    AttemptWorkspaceManager,
)
from benchmarking.runtime.cancellation import (
    CancellationToken,
    OwnedProcessRegistry,
)
from benchmarking.service.chemdebate.cleanroom import (
    CleanroomError,
    CleanroomRuntime,
    iter_pending_cleanup_manifests,
)
from .provisioning import actual_slot_ids
from benchmarking.runtime.session_isolation import inspect_postflight_session
from benchmarking.runtime.workspace_policy import ContaminationAudit
from benchmarking.service.chemdebate.response import (
    build_chemqa_full_response,
    build_chemqa_response_from_submission,
    load_yaml_mapping,
)
from benchmarking.workflow.errors import BenchmarkError, CleanupFatalError
from .experiments import (
    DEFAULT_CHEMQA_PRESET,
)
from benchmarking.service.chemdebate.prompts import build_chemqa_goal
from benchmarking.service.chemdebate.prompts import resolve_chemqa_answer_kind
from benchmarking.workflow.run_state import now_stamp, slugify
from benchmarking.service.chemdebate.runner import ChemQARunner as BaseChemQARunner

from benchmarking.runtime.runner_support import _compatibility_protected_roots, _CancellationRunnerMixin

DEFAULT_OPENCLAW_ENV_FILE = runtime_paths.openclaw_env
DEFAULT_CLEANROOM_ROOT = runtime_paths.skills_root / "benchmark-cleanroom"

def _load_cleanroom_runtime(*, process_registry: OwnedProcessRegistry | None = None) -> CleanroomRuntime:
    try:
        return CleanroomRuntime.load(
            cleanroom_root=DEFAULT_CLEANROOM_ROOT,
            current_python=lambda: subprocess_utils.current_python(),
            run_subprocess=lambda *args, **kwargs: subprocess_utils.run_owned_subprocess(
                *args,
                process_registry=process_registry,
                **kwargs,
            ),
        )
    except CleanroomError as exc:
        raise BenchmarkError(str(exc)) from exc

def run_pending_cleanroom_cleanup() -> list[dict[str, Any]]:
    if not iter_pending_cleanup_manifests():
        return []
    return _load_cleanroom_runtime().run_pending_cleanroom_cleanup()

class ChemQARunner(_CancellationRunnerMixin, BaseChemQARunner):
    @staticmethod
    def _workspace_templates(project_root):
        from .execution import workspace_templates
        return workspace_templates(project_root)

    def __init__(
        self,
        *,
        chemqa_root: Path,
        timeout_seconds: int,
        config_path: Path,
        slot_set: str,
        review_rounds: int | None,
        rebuttal_rounds: int | None,
        model_profile: str,
        runtime_bundle_root: Path,
        launch_workspace_root: Path,
        convergence_policy: ConvergencePolicy | None = None,
        workspace_manager: AttemptWorkspaceManager | None = None,
        contamination_auditor=None,
        cancellation_token: CancellationToken | None = None,
        process_registry: OwnedProcessRegistry | None = None,
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
                run_id="chemqa-runner-test",
                invocation_id=uuid.uuid4().hex,
                templates=self._workspace_templates(runtime_paths.project_root),
                protected_roots=_compatibility_protected_roots(
                    runtime_root=runtime_root,
                    output_root=output_root,
                ),
            )
        if compatibility_manager and contamination_auditor is None:
            contamination_auditor = lambda **_kwargs: ContaminationAudit(status="clean")
        cleanroom = _load_cleanroom_runtime(process_registry=self._process_registry)
        super().__init__(
            chemqa_root=chemqa_root,
            timeout_seconds=timeout_seconds,
            config_path=config_path,
            slot_set=slot_set,
            review_rounds=review_rounds,
            rebuttal_rounds=rebuttal_rounds,
            model_profile=model_profile,
            runtime_bundle_root=runtime_bundle_root,
            launch_workspace_root=launch_workspace_root,
            launch_script=chemqa_root / "scripts" / "launch_from_preset.py",
            collect_script=chemqa_root / "scripts" / "collect_artifacts.py",
            runtime_dir=chemqa_root.parent / "debateclaw-v1" / "scripts",
            current_python=lambda: subprocess_utils.current_python(),
            run_subprocess=lambda *args, **kwargs: subprocess_utils.run_owned_subprocess(
                *args,
                cancellation_token=self._cancellation_token,
                process_registry=self._process_registry,
                **kwargs,
            ),
            parse_json_stdout=subprocess_utils.parse_json_stdout,
            deep_copy_jsonish=subprocess_utils.deep_copy_jsonish,
            ensure_runtime_bundle=lambda record, *, bundle_root: runtime_bundles.ensure_runtime_bundle(
                record,
                bundle_root=bundle_root,
            ),
            build_chemqa_goal=build_chemqa_goal,
            resolve_chemqa_answer_kind=resolve_chemqa_answer_kind,
            cleanup_manifest_path=cleanroom.cleanup_manifest_path,
            build_cleanup_manifest_payload=cleanroom.build_cleanup_manifest_payload,
            write_cleanup_manifest=cleanroom.write_cleanup_manifest,
            register_pending_cleanup_manifest=cleanroom.register_pending_cleanup_manifest,
            update_cleanup_manifest=cleanroom.update_cleanup_manifest,
            invoke_cleanroom_cleanup=cleanroom.invoke_cleanroom_cleanup,
            unregister_pending_cleanup_manifest=cleanroom.unregister_pending_cleanup_manifest,
            now_stamp=now_stamp,
            slugify=slugify,
            default_chemqa_preset=DEFAULT_CHEMQA_PRESET,
            default_openclaw_env_file=DEFAULT_OPENCLAW_ENV_FILE,
            actual_slot_ids=actual_slot_ids,
            workspace_manager=workspace_manager,
            session_audit_resolver=lambda agent_id, session_id, *, session_store_path=None: inspect_postflight_session(
                agent_id,
                session_id,
                config_path=config_path,
                session_store_path=session_store_path,
            ),
            contamination_auditor=contamination_auditor,
            allowed_workspace_roots=(runtime_paths.skills_root,),
            unique_run_suffix=not compatibility_manager,
            normalize_chemqa_run_status=normalize_chemqa_run_status,
            is_chemqa_terminal_status=is_chemqa_terminal_status,
            is_chemqa_success_status=is_chemqa_success_status,
            build_chemqa_full_response=build_chemqa_full_response,
            build_chemqa_response_from_submission=build_chemqa_response_from_submission,
            load_yaml_mapping=load_yaml_mapping,
            normalize_space=normalize_space,
            benchmark_error_factory=BenchmarkError,
            cleanup_error_factory=CleanupFatalError,
            convergence_policy=convergence_policy,
        )

    def _wait_for_terminal_status(self, run_id: str, *, timeout_seconds: int) -> dict[str, Any]:
        if not hasattr(self, "_is_chemqa_terminal_status"):
            self._is_chemqa_terminal_status = is_chemqa_terminal_status
        if not hasattr(self, "_normalize_chemqa_run_status"):
            self._normalize_chemqa_run_status = normalize_chemqa_run_status
        if not hasattr(self, "_benchmark_error_factory"):
            self._benchmark_error_factory = BenchmarkError
        if not hasattr(self, "convergence_policy"):
            self.convergence_policy = ConvergencePolicy(timeout_seconds=timeout_seconds)
        return super()._wait_for_terminal_status(run_id, timeout_seconds=timeout_seconds)

    def _candidate_protocol_dirs(self, run_id: str, run_status: dict[str, Any]) -> list[Path]:
        if not hasattr(self, "_actual_slot_ids"):
            self._actual_slot_ids = actual_slot_ids
        return super()._candidate_protocol_dirs(run_id, run_status)

    def _build_candidate_submission_fallback(
        self,
        run_id: str,
        run_status: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        if not hasattr(self, "_load_yaml_mapping"):
            self._load_yaml_mapping = load_yaml_mapping
        if not hasattr(self, "_build_chemqa_response_from_submission"):
            self._build_chemqa_response_from_submission = build_chemqa_response_from_submission
        if not hasattr(self, "_normalize_space"):
            self._normalize_space = normalize_space
        return super()._build_candidate_submission_fallback(run_id, run_status)
