from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ProvisionedAgent:
    agent_id: str
    workspace: Path
    agent_dir: Path


@dataclass(frozen=True)
class ProvisionedExperiment:
    judge: ProvisionedAgent | None
    runner_agents: tuple[ProvisionedAgent, ...]


def ensure_basic_agent_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
