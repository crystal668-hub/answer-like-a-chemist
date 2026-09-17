from __future__ import annotations
from benchmarking.core.experiments import ExperimentSpec
from benchmarking.runtime.provisioning import ProvisionedAgent, ProvisionedExperiment, ensure_basic_agent_dirs
from benchmarking.runtime.config_pool import RuntimeConfigError, _render_run_config_or_raise, _ensure_benchmark_skills_extra_dir, _enable_benchmark_workdir_guard
from benchmarking.runtime.workspace_policy import build_workspace_access_policy

def build_runner_config(*, base_payload, context, group, spec, judge, single_agent_model, judge_model, workspace_manager, single_agent_id_override=None):
    runner_agents = []
    agent_id = spec.resolve_single_agent_id(single_agent_id_override)
    if not agent_id:
        raise RuntimeConfigError(f"Experiment group `{group.id}` missing single-agent id in experiment spec.")
    workspace = workspace_manager.active_workspace_path(group_id=group.id, agent_id=agent_id)
    agent_dir = context.agents_root / agent_id / "agent"
    ensure_basic_agent_dirs(agent_dir)
    runner_agents.append(
        ProvisionedAgent(
            agent_id=agent_id,
            workspace=workspace,
            agent_dir=agent_dir,
        )
    )
    single_spec = ExperimentSpec(
        id=spec.id,
        label=spec.label,
        runner_kind=spec.runner_kind,
        websearch_enabled=spec.websearch_enabled,
        skills_enabled=spec.skills_enabled,
        single_agent_id=agent_id,
        slot_set=spec.slot_set,
        skill_allowlist=spec.skill_allowlist,
    )
    payload = _render_run_config_or_raise(
        base_payload=base_payload,
        spec=single_spec,
        provisioned=ProvisionedExperiment(judge=judge, runner_agents=tuple(runner_agents)),
        judge_model=judge_model,
        runner_model=single_agent_model,
    )
    payload["agents"]["list"] = [entry for entry in payload["agents"]["list"]
                                  if isinstance(entry, dict) and entry.get("id") == agent_id]
    skill_scopes = (
        context.benchmark_skills_root,
        context.benchmark_skills_root.parent / "scripts" / "run_skill.py",
    ) if single_spec.skills_enabled else ()
    if single_spec.skills_enabled:
        _ensure_benchmark_skills_extra_dir(payload, context.benchmark_skills_root)
    policies = {
        runner.agent_id: build_workspace_access_policy(
            active_workspace=runner.workspace,
            role="single_llm",
            skills_enabled=single_spec.skills_enabled,
            protected_roots=workspace_manager.protected_roots,
            skill_read_scopes=skill_scopes,
        )
        for runner in runner_agents
    }
    _enable_benchmark_workdir_guard(payload, agent_policies=policies)
    return payload
