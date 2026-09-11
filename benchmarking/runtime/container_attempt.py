"""Container-owned Python environment and dependency evidence lifecycle."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from benchmarking.runtime.attempt_finalization import cleanup_owned_environment, register_environment, write_evidence
from benchmarking.runtime.dependency_evidence import validate_dependency_evidence

from benchmarking.runtime.attempt_environment import (
    collect_dependency_manifest,
    create_attempt_environment,
    dependency_install_events,
    remediate_forbidden_distributions,
)


def cleanup_plugin_skill_links(session_root: Path, *, host_recovery: bool = False) -> list[dict[str, str]]:
    """Remove only OpenClaw's generated links into the immutable image."""
    cache = session_root / "plugin-skills"
    records = []
    if session_root.is_symlink() or cache.is_symlink():
        raise ValueError("plugin skill cache root is a symlink")
    if not cache.is_dir():
        return records
    extension_root = Path("/usr/local/lib/node_modules/openclaw/dist/extensions")
    for link in sorted(cache.iterdir()):
        if not link.is_symlink():
            continue
        target = Path(os.readlink(link)) if host_recovery else link.resolve(strict=True)
        if ".." in target.parts:
            raise ValueError("plugin skill link has an untrusted target")
        if not target.is_relative_to(extension_root) or "skills" not in target.relative_to(extension_root).parts:
            raise ValueError(f"unexpected plugin skill link: {link.name}")
        records.append({"name": link.name, "image_target": str(target)})
        link.unlink()
    return records


def main() -> int:
    scratch = Path(os.environ["BENCHMARK_SKILL_SCRATCH_DIR"])
    notes = scratch / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    environment = None
    child = None
    manifest = {"status": "initialization_failed"}
    cleanup = {}

    def terminate(signum, _frame):
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
        raise InterruptedError(f"container attempt interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        register_environment(scratch, json.loads(os.environ["BENCHMARK_ATTEMPT_IDENTITY"]), "docker")
        environment = create_attempt_environment(
            scratch,
            bootstrap_python="/usr/local/bin/python",
            pypi_cutoff=os.environ["BENCHMARK_PYPI_CUTOFF"],
            cache_dir=scratch / "tmp/cache/uv",
        )
        env = {**os.environ, **environment.to_env()}
        child = subprocess.Popen([str(environment.python), *sys.argv[1:]], env=env, start_new_session=True)
        return child.wait()
    except Exception as exc:
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        print(manifest["error"], file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if environment is not None:
            try:
                events = []
                for transcript in sorted(Path("/benchmark/session").rglob("*.jsonl")):
                    events.extend(dependency_install_events(transcript))
                manifest = collect_dependency_manifest(
                    environment,
                    identity=json.loads(os.environ["BENCHMARK_ATTEMPT_IDENTITY"]),
                    install_events=events,
                )
                manifest["dependency_audit"] = remediate_forbidden_distributions(environment, manifest)
                manifest["validation"] = validate_dependency_evidence(
                    manifest, identity=json.loads(os.environ["BENCHMARK_ATTEMPT_IDENTITY"]), scratch=scratch,
                    expected_venv=str(environment.venv_dir), pypi_cutoff=environment.pypi_cutoff)
                manifest["status"] = manifest["validation"]["status"]
                write_evidence(notes / "dependency-manifest.json", manifest)
            except Exception as exc:
                manifest.update(status="manifest_failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                cleanup = cleanup_owned_environment(scratch)
        else:
            cleanup = cleanup_owned_environment(scratch)
        try:
            plugin_cleanup = {"status": "complete", "links": cleanup_plugin_skill_links(Path("/benchmark/session"))}
        except Exception as exc:
            plugin_cleanup = {"status": "failed", "error": str(exc)}
        for name, payload in (("dependency-manifest", manifest), ("dependency-cleanup", cleanup),
                              ("plugin-skill-cache-cleanup", plugin_cleanup)):
            try:
                write_evidence(notes / f"{name}.json", payload)
            except OSError as exc:
                print(f"{name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
