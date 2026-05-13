from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SLOT_SENTINEL_FILENAME = ".debateclaw-slot.json"
SLOT_SENTINEL_KIND = "debateclaw-slot-workspace"
SLOT_SENTINEL_VERSION = 1
BENCHMARK_TOOLS_FILENAME = "TOOLS.md"


def render_single_agent_skill_tools_md() -> str:
    run_skill_command = (
        'python /Users/xutao/.openclaw/workspace/scripts/run_skill.py '
        '--workspace-root /Users/xutao/.openclaw/workspace '
        '--execution-cwd "$PWD" '
        "--script skills/<skill>/scripts/<script>.py -- ..."
    )
    run_skill_exec_example = f"exec {json.dumps({'command': run_skill_command})}"
    return "\n".join(
        [
            "# Benchmark-managed TOOLS.md",
            "",
            "This file is injected into the single-LLM skills-on benchmark agent workspace.",
            "",
            "## Local Skill Script Execution",
            "",
            "When you want to execute a local script, use this exact OpenClaw tool-call form:",
            "",
            f"`{run_skill_exec_example}`",
            "",
            "The tool name must be exactly `exec`; the JSON object after it must contain `command`.",
            "",
            "Positive example:",
            "",
            f"`{run_skill_exec_example}`",
            "",
            "Negative examples:",
            "",
            "- `python3` is invalid as a tool name; it is a shell program inside the `command` string only.",
            "- `script`, `cmd`, or `command` are invalid tool names.",
            "- `exec {}` is invalid because the required `command` field is missing.",
            "- direct `python skills/...` bypasses the canonical wrapper.",
            "- `bash`, `reasoning`, and `system-event-scheduler` are invalid tool names.",
            "",
            "Do not search for alternate runners or call skill scripts directly with `python` or `python3`.",
            "",
        ]
    )


@dataclass(frozen=True)
class ProvisionedAgent:
    agent_id: str
    workspace: Path
    agent_dir: Path


@dataclass(frozen=True)
class ProvisionedExperiment:
    judge: ProvisionedAgent
    runner_agents: tuple[ProvisionedAgent, ...]


def ensure_basic_agent_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def provision_single_agent_skill_tools_md(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / BENCHMARK_TOOLS_FILENAME).write_text(
        render_single_agent_skill_tools_md(),
        encoding="utf-8",
    )


def provision_slot_workspace(
    *,
    workspace: Path,
    workspace_root: Path,
    slot_id: str,
    agents_template_text: str,
    last_session_id: str = "",
) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text(agents_template_text, encoding="utf-8")
    payload = {
        "kind": SLOT_SENTINEL_KIND,
        "version": SLOT_SENTINEL_VERSION,
        "slot": slot_id,
        "workspace": str(workspace.resolve()),
        "workspace_root": str(workspace_root.resolve()),
        "last_session_id": last_session_id,
        "managed_by": "debateclaw",
    }
    (workspace / SLOT_SENTINEL_FILENAME).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
