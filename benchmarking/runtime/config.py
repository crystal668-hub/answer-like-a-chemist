from __future__ import annotations

import json
from typing import Any

from benchmarking.core.experiments import ExperimentSpec
from benchmarking.runtime.provisioning import ProvisionedAgent, ProvisionedExperiment


class ConfigRenderError(ValueError):
    pass


def _deep_copy_jsonish(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _upsert_agent_entry(
    payload: dict[str, Any],
    *,
    provisioned_agent: ProvisionedAgent,
    model: str,
    skills: list[str] | None = None,
) -> None:
    agents = payload.setdefault("agents", {})
    entries = agents.get("entries")
    if entries is None:
        entries = agents.get("list", [])
        agents.pop("list", None)
        agents["entries"] = {str(item.get("id")): item for item in entries if isinstance(item, dict) and item.get("id")}
    if isinstance(entries, dict):
        entries_list = list(entries.values())
    else:
        entries_list = entries
    if not isinstance(entries_list, list):
        raise ConfigRenderError("OpenClaw config agents.entries is invalid")
    normalized_workspace = str(provisioned_agent.workspace.resolve())
    normalized_agent_dir = str(provisioned_agent.agent_dir.resolve())
    for entry in entries_list:
        if isinstance(entry, dict) and str(entry.get("id", "")) == provisioned_agent.agent_id:
            entry["name"] = provisioned_agent.agent_id
            entry["workspace"] = normalized_workspace
            entry["agentDir"] = normalized_agent_dir
            entry["model"] = model
            entry.pop("thinking", None)
            if skills is None:
                entry.pop("skills", None)
            else:
                entry["skills"] = list(skills)
            agents["entries"] = {str(item.get("id")): item for item in entries_list if isinstance(item, dict) and item.get("id")}
            return
    entry = {
        "id": provisioned_agent.agent_id,
        "name": provisioned_agent.agent_id,
        "workspace": normalized_workspace,
        "agentDir": normalized_agent_dir,
        "model": model,
    }
    if skills is not None:
        entry["skills"] = list(skills)
    entries_list.append(entry)
    agents["entries"] = {str(item.get("id")): item for item in entries_list if isinstance(item, dict) and item.get("id")}


def render_run_config(
    *,
    base_payload: dict[str, Any],
    spec: ExperimentSpec,
    provisioned: ProvisionedExperiment,
    judge_model: str,
    runner_model: str,
) -> dict[str, Any]:
    payload = _deep_copy_jsonish(base_payload)
    agents = payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        raise ConfigRenderError("OpenClaw config agents is not an object")
    legacy_entries = agents.pop("list", None)
    if legacy_entries is not None and not isinstance(legacy_entries, list):
        raise ConfigRenderError("OpenClaw config agents.list is not a list")
    if "entries" not in agents:
        if isinstance(legacy_entries, list):
            agents["entries"] = {
                str(item.get("id")): item for item in legacy_entries
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
        else:
            agents["entries"] = {}
    elif isinstance(agents["entries"], list):
        agents["entries"] = {
            str(item.get("id")): item for item in agents["entries"]
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigRenderError("OpenClaw config agents.defaults is not an object")
    defaults["skipBootstrap"] = True
    tools = payload.setdefault("tools", {})
    web = tools.setdefault("web", {})
    search = web.setdefault("search", {})
    search["enabled"] = spec.websearch_enabled
    fetch = web.setdefault("fetch", {})
    fetch["enabled"] = spec.websearch_enabled

    plugins = payload.setdefault("plugins", {})
    entries = plugins.setdefault("entries", {})
    duckduckgo = entries.setdefault("duckduckgo", {})
    duckduckgo["enabled"] = spec.websearch_enabled
    duckduckgo.setdefault("config", {})

    if provisioned.judge is not None:
        _upsert_agent_entry(payload, provisioned_agent=provisioned.judge, model=judge_model, skills=None)
    runner_skills = list(spec.skill_allowlist or ()) if spec.skills_enabled else []
    for runner_agent in provisioned.runner_agents:
        _upsert_agent_entry(
            payload,
            provisioned_agent=runner_agent,
            model=runner_model,
            skills=runner_skills,
        )
    return payload
