"""Opt-in real Docker contract checks; artifacts stay with benchmark runs."""

import json
import os
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from benchmarking.runtime.agent_workspace import AttemptIdentity
from benchmarking.runtime.cancellation import CancellationReason, CancellationToken
from benchmarking.runtime.container_runtime import (
    ContainerAttemptSpec,
    ContainerMount,
    DockerContainerRuntime,
)

IMAGE = os.environ.get("BENCHMARK_TEST_CONTAINER_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="set BENCHMARK_TEST_CONTAINER_IMAGE for real Docker checks")


@pytest.mark.parametrize("mode", ["install", "timeout", "cancel"])
def test_real_attempt_environment(mode):
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    root = Path("state/benchmark-runs/temporary/infra-contract/no-llm") / f"infra-contract-no-llm-{stamp}"
    workspace = root.resolve() / "workspace"
    scratch = workspace / "scratch"
    scratch.mkdir(parents=True)
    identity = AttemptIdentity(root.name, stamp, "contract", "single_llm", "agent", mode, 0, stamp, "test")
    code = (
        "import subprocess,sys,json; from pathlib import Path; "
        "assert sys.prefix == '/benchmark/workspace/scratch/venv'; "
        "subprocess.run(['sh','-lc','export PATH=\"/benchmark/workspace/scratch/.runtime-bin:$PATH\"; uv pip install packaging==24.2'],check=True); "
        "p=subprocess.run(['uv','pip','freeze'],check=True,capture_output=True,text=True); "
        "Path('/benchmark/workspace/scratch/actual-freeze.json').write_text(json.dumps(p.stdout.splitlines()))"
        if mode == "install" else "import time; time.sleep(90)"
    )
    env = {"BENCHMARK_SKILL_SCRATCH_DIR": "/benchmark/workspace/scratch", "BENCHMARK_PYPI_CUTOFF": datetime.now(UTC).isoformat(), "BENCHMARK_ATTEMPT_IDENTITY": json.dumps(identity.sentinel_fields())}
    runtime = DockerContainerRuntime()
    handle = runtime.create(ContainerAttemptSpec(identity, str(IMAGE), ("/opt/benchmark/.venv/bin/python", "-m", "benchmarking.runtime.container_attempt", "-c", code), environment=env, mounts=(ContainerMount(workspace, PurePosixPath("/benchmark/workspace"), "rw", "workspace"),)))
    token = CancellationToken()
    timer = None
    try:
        runtime.start(handle)
        if mode == "cancel":
            timer = threading.Timer(8, lambda: token.cancel(CancellationReason(source="test")))
            timer.start()
        result = runtime.collect(handle, timeout_seconds=8 if mode == "timeout" else 120, cancellation_token=token)
        if mode == "install":
            assert result.return_code == 0, result.stderr
        else:
            assert result.cancelled if mode == "cancel" else result.timed_out
        manifest = json.loads((scratch / "notes/dependency-manifest.json").read_text())
        assert manifest["status"] == "complete", manifest
        assert manifest["python"]["platform"].startswith("Linux")
        assert manifest["python"]["executable"] == "/benchmark/workspace/scratch/venv/bin/python"
        assert manifest["pypi"]["replay_lock"]["status"] == "generated"
        if mode == "install":
            assert manifest["pypi"]["freeze"] == json.loads((scratch / "actual-freeze.json").read_text())
            assert "packaging==24.2" in manifest["pypi"]["freeze"]
        assert not (scratch / "venv").exists()
        assert not (scratch / "tmp/cache/uv").exists()
    finally:
        if timer:
            timer.cancel()
        report = runtime.remove(handle, force=True)
        (root / "cleanup.json").write_text(json.dumps(report.__dict__))
        assert report.removed


def test_real_orphan_recovery_preserves_live_owner():
    from benchmarking.runtime.agent_workspace import (
        SCHEMA_VERSION,
        SENTINEL_FILENAME,
        SENTINEL_KIND,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    root = Path("state/benchmark-runs/temporary/infra-contract/no-llm") / f"infra-contract-no-llm-{stamp}"
    root.mkdir(parents=True)
    runtime = DockerContainerRuntime()
    handles = []
    try:
        for kind in ("stale", "live"):
            workspace = root.resolve() / kind
            workspace.mkdir()
            ident = AttemptIdentity(root.name, stamp, "contract", "single_llm", "agent", kind, 0, kind + stamp, "test")
            (workspace / SENTINEL_FILENAME).write_text(json.dumps({"kind": SENTINEL_KIND, "schema_version": SCHEMA_VERSION, **ident.sentinel_fields(), "workspace_path": str(workspace)}))
            if kind == "stale":
                # A separate orchestrator creates the container and then exits.
                code = """import json,sys
from pathlib import Path,PurePosixPath
from benchmarking.runtime.agent_workspace import AttemptIdentity
from benchmarking.runtime.container_runtime import DockerContainerRuntime,ContainerAttemptSpec,ContainerMount
identity=AttemptIdentity(**json.loads(sys.argv[1]))
h=DockerContainerRuntime().create(ContainerAttemptSpec(identity,sys.argv[2],('sleep','90'),mounts=(ContainerMount(Path(sys.argv[3]),PurePosixPath('/benchmark/workspace'),'rw','workspace'),)))
print(h.container_id)
"""
                import sys
                process = subprocess.run([sys.executable, "-c", code, json.dumps(ident.sentinel_fields()), str(IMAGE), str(workspace)], capture_output=True, text=True, check=True)
                handles.append(process.stdout.strip())
            else:
                handle = runtime.create(ContainerAttemptSpec(ident, str(IMAGE), ("sleep", "90"), mounts=(ContainerMount(workspace, PurePosixPath("/benchmark/workspace"), "rw", "workspace"),)))
                handles.append(handle.container_id)
        reports = runtime.recover_orphans(runtime_root=root)
        selected = {item["container_id"]: item for item in reports if item["container_id"] in handles}
        assert selected[handles[0]]["removed"]
        assert not selected[handles[1]]["removed"]
        (root / "recovery.json").write_text(json.dumps(selected, indent=2))
    finally:
        for container_id in handles:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, check=False)
