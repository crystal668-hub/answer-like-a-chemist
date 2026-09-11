from __future__ import annotations
from benchmarking.core.experiments import ExperimentSpec
from benchmarking.runtime.provisioning import ProvisionedAgent, ProvisionedExperiment, ensure_basic_agent_dirs
from benchmarking.runtime.config_pool import _render_run_config_or_raise, _ensure_benchmark_skills_extra_dir, _enable_benchmark_workdir_guard
from benchmarking.runtime.workspace_policy import build_workspace_access_policy

from .experiments import CHEMQA_SLOT_SETS
from .provisioning import actual_slot_ids

def build_runner_config(*, base_payload, context, group, spec, judge, single_agent_model, judge_model, workspace_manager, single_agent_id_override=None):
    runner_agents = []
    slot_set = spec.slot_set or CHEMQA_SLOT_SETS[group.id]
    slot_map = actual_slot_ids(slot_set)
    for actual_slot_id in slot_map.values():
        workspace = workspace_manager.active_workspace_path(group_id=group.id, agent_id=actual_slot_id)
        agent_dir = context.agents_root / actual_slot_id / "agent"
        ensure_basic_agent_dirs(agent_dir)
        runner_agents.append(
            ProvisionedAgent(
                agent_id=actual_slot_id,
                workspace=workspace,
                agent_dir=agent_dir,
            )
        )
    chemqa_spec = ExperimentSpec(
        id=spec.id,
        label=spec.label,
        runner_kind=spec.runner_kind,
        websearch_enabled=spec.websearch_enabled,
        skills_enabled=spec.skills_enabled,
        slot_set=slot_set,
        skill_allowlist=spec.skill_allowlist,
    )
    payload = _render_run_config_or_raise(
        base_payload=base_payload,
        spec=chemqa_spec,
        provisioned=ProvisionedExperiment(judge=judge, runner_agents=tuple(runner_agents)),
        judge_model=judge_model,
        runner_model=single_agent_model,
    )
    if chemqa_spec.skills_enabled:
        _ensure_benchmark_skills_extra_dir(payload, context.benchmark_skills_root)
    policies = {
        runner.agent_id: build_workspace_access_policy(
            active_workspace=runner.workspace,
            role="chemqa",
            skills_enabled=chemqa_spec.skills_enabled,
            protected_roots=workspace_manager.protected_roots,
            skill_read_scopes=(context.benchmark_skills_root,) if chemqa_spec.skills_enabled else (),
        )
        for runner in runner_agents
    }
    _enable_benchmark_workdir_guard(payload, agent_policies=policies)
    return payload
