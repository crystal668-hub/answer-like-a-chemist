from __future__ import annotations
import json
import uuid
from benchmarking.core.convergence import ConvergencePolicy


def runner_options(*, group, output_root, config_path, single_agent, single_timeout, experiment_specs, single_agent_thinking, single_convergence_policy=None, single_timeout_retries=3, single_timeout_retry_backoff_seconds=(5,15,45), no_timeout=False, workspace_manager=None, cancellation_token=None, process_registry=None, pypi_cutoff=None, vgb_skill_allowlist=(), admission_controller=None, manage_group_lifecycle=True, execution_backend="docker", container_image="openclaw-benchmark-single-llm:latest", container_cpus=None, container_memory_bytes=None, container_pids_limit=None, container_network=None):
    runtime_bundle_root = output_root / "input-bundles"
    if not manage_group_lifecycle and workspace_manager is not None:
        original_agent = single_agent
        single_agent = f"{single_agent[:51]}-{uuid.uuid4().hex[:12]}"
        original_workspace = workspace_manager.active_workspace_path(group_id=group.id, agent_id=original_agent)
        new_workspace = workspace_manager.active_workspace_path(group_id=group.id, agent_id=single_agent)
        source = json.loads(config_path.read_text())

        def rewrite(value):
            if isinstance(value, str):
                if value == str(original_workspace) or value.startswith(str(original_workspace) + "/"):
                    return str(new_workspace) + value[len(str(original_workspace)):]
                if value == original_agent:
                    return single_agent
                return value.replace("/" + original_agent + "/", "/" + single_agent + "/")
            if isinstance(value, list):
                return [rewrite(item) for item in value]
            if isinstance(value, dict):
                return {rewrite(key): rewrite(item) for key, item in value.items()}
            return value

        config_path = output_root / "runtime-config" / f"{single_agent}.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(rewrite(source), indent=2) + "\n")

    return dict(
        agent_id=single_agent,
        timeout_seconds=single_timeout,
        config_path=config_path,
        runtime_bundle_root=runtime_bundle_root,
        configured_skills=tuple(experiment_specs[group.id].skill_allowlist or ()),
        vgb_configured_skills=tuple(vgb_skill_allowlist) if group.skills_enabled else (),
        convergence_policy=single_convergence_policy or ConvergencePolicy(timeout_seconds=single_timeout),
        timeout_retries=single_timeout_retries,
        timeout_retry_backoff_seconds=single_timeout_retry_backoff_seconds,
        benchmark_agent_thinking=single_agent_thinking,
        no_timeout=no_timeout,
        workspace_manager=workspace_manager,
        cancellation_token=cancellation_token,
        process_registry=process_registry,
        pypi_cutoff=pypi_cutoff,
        admission_controller=admission_controller,
        execution_backend=execution_backend,
        container_image=container_image,
        container_cpus=container_cpus,
        container_memory_bytes=container_memory_bytes,
        container_pids_limit=container_pids_limit,
        container_network=container_network,
    )
