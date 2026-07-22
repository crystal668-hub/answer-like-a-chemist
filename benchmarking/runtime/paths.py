from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> Path | None:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _preferred_subdir(root: Path, name: str, *, legacy_root: Path) -> Path:
    preferred = (root / name).resolve()
    if preferred.exists():
        return preferred
    legacy = (legacy_root / name).resolve()
    if legacy.exists():
        return legacy
    return preferred


def _preferred_path(preferred: Path, *legacy_candidates: Path) -> Path:
    resolved_preferred = preferred.resolve()
    if resolved_preferred.exists():
        return resolved_preferred
    for candidate in legacy_candidates:
        resolved_candidate = candidate.resolve()
        if resolved_candidate.exists():
            return resolved_candidate
    return resolved_preferred


openclaw_home = (Path.home() / ".openclaw").resolve()
project_root = _env_path("OPENCLAW_PROJECT_ROOT") or (openclaw_home / "workspace").resolve()
data_root = _env_path("OPENCLAW_DATA_ROOT") or (openclaw_home / "data").resolve()
skills_root = _env_path("OPENCLAW_SKILLS_ROOT") or _preferred_subdir(project_root, "skills", legacy_root=openclaw_home)
benchmarks_root = _env_path("OPENCLAW_BENCHMARKS_ROOT") or _preferred_path(
    data_root / "formal-benchmarks",
    project_root / "benchmarks",
)
openclaw_config = (openclaw_home / "openclaw.json").resolve()
openclaw_env = (openclaw_home / ".env").resolve()
benchmark_runtime_root = (openclaw_home / "benchmark" / "workspaces").resolve()
debate_runtime_root = (openclaw_home / "debateclaw" / "workspaces").resolve()
clawteam_home = (Path.home() / ".clawteam").resolve()
agents_root = (openclaw_home / "agents").resolve()
temp_benchmarks_root = (data_root / "temp-benchmarks").resolve()
project_state_root = (project_root / "state").resolve()
