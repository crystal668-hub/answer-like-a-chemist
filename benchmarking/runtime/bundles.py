from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarking.core.records import BenchmarkRecord


class RuntimeBundleError(RuntimeError):
    pass


@dataclass
class RuntimeBundle:
    bundle_dir: Path
    question_markdown: Path
    image_files: list[Path]
    prompt_text: str | None = None

    def to_meta(self) -> dict[str, Any]:
        return {
            "bundle_dir": str(self.bundle_dir),
            "question_markdown": str(self.question_markdown),
            "image_files": [str(path) for path in self.image_files],
        }


@dataclass(frozen=True)
class RuntimePathProjection:
    workspace: Path
    skills_root: Path
    bundle: RuntimeBundle | None = None

    def audit_mappings(self) -> dict[str, str]:
        mappings = {
            "/benchmark/workspace": str(self.workspace.resolve()),
            "/benchmark/session": str(self.workspace.resolve() / "scratch/session"),
            "/opt/benchmark/skills": str(self.skills_root.resolve()),
            "/opt/benchmark/scripts/run_skill.py": str(
                self.skills_root.resolve().parent / "scripts/run_skill.py"
            ),
        }
        if self.bundle is not None:
            mappings["/benchmark/input"] = str(Path(self.bundle.bundle_dir).resolve())
        return mappings

    def visible_bundle(self) -> RuntimeBundle | None:
        if self.bundle is None:
            return None
        root = Path(self.bundle.bundle_dir).resolve()

        def project(path: Path) -> Path:
            candidate = Path(path)
            if candidate.is_symlink() or not candidate.is_file():
                raise RuntimeBundleError(f"Input asset is missing or a symlink: {candidate}")
            try:
                relative = candidate.resolve().relative_to(root)
            except ValueError as exc:
                raise RuntimeBundleError(f"Input asset escapes its bundle: {candidate}") from exc
            return Path("/benchmark/input") / relative

        return RuntimeBundle(
            Path("/benchmark/input"),
            project(self.bundle.question_markdown),
            [project(path) for path in self.bundle.image_files],
            getattr(self.bundle, "prompt_text", None),
        )

    def to_meta(self) -> dict[str, Any]:
        return {"schema_version": 1, "container_to_host": self.audit_mappings()}


def ensure_runtime_bundle(record: BenchmarkRecord, *, bundle_root: Path) -> None:
    del record, bundle_root
    return None
