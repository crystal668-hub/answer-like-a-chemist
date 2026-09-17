"""Real Docker fault and multimodal contracts, without provider calls."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from benchmarking.runtime.agent_workspace import (
    AttemptWorkspaceManager,
    WorkspaceTemplate,
)
from benchmarking.runtime.container_runtime import (
    DockerContainerRuntime,
)

IMAGE = os.environ.get("BENCHMARK_TEST_CONTAINER_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="real Docker acceptance is opt-in")


def run_root():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    root = (
        Path("state/benchmark-runs/temporary/infra-acceptance/no-llm")
        / f"infra-acceptance-no-llm-{stamp}"
    )
    root.mkdir(parents=True)
    return root.resolve(), stamp


def test_initialized_environment_survives_orchestrator_crash_and_recovers():
    root, stamp = run_root()
    template = root / "template"
    template.mkdir()
    (template / "AGENTS.md").write_text("# Test attempt\n")
    code = r"""
import json,sys,time
from pathlib import Path,PurePosixPath
from benchmarking.runtime.agent_workspace import AttemptIdentity,AttemptWorkspaceManager,WorkspaceTemplate
from benchmarking.runtime.container_runtime import DockerContainerRuntime,ContainerAttemptSpec,ContainerMount
root=Path(sys.argv[1]); stamp=sys.argv[2]
manager=AttemptWorkspaceManager(runtime_root=root/'runtime',output_root=root,run_id=root.name,invocation_id='old',
 templates={'test':WorkspaceTemplate('test',root/'template')},protected_roots=())
identity=AttemptIdentity(root.name,'old','group','single_llm','agent','crash',0,stamp,'test')
lease=manager.prepare(identity)
env={'BENCHMARK_SKILL_SCRATCH_DIR':'/benchmark/workspace/scratch','BENCHMARK_PYPI_CUTOFF':'2026-09-11T00:00:00Z',
 'BENCHMARK_ATTEMPT_IDENTITY':json.dumps(identity.sentinel_fields())}
runtime=DockerContainerRuntime()
handle=runtime.create(ContainerAttemptSpec(identity,sys.argv[3],('/opt/benchmark/.venv/bin/python','-m',
 'benchmarking.runtime.container_attempt','-c',"from pathlib import Path; import time; Path('/benchmark/workspace/scratch/ready').touch(); time.sleep(300)"),
 environment=env,mounts=(ContainerMount(lease.active_workspace,PurePosixPath('/benchmark/workspace'),'rw','workspace'),)))
(root/'handle.json').write_text(json.dumps({'id':handle.container_id,'workspace':str(lease.active_workspace)}))
runtime.start(handle)
deadline=time.monotonic()+90
while not (lease.scratch_dir/'ready').exists():
 if time.monotonic()>deadline: raise RuntimeError('initialization timeout')
 time.sleep(.1)
# Hard-stop the owned container to leave the initialized environment on disk.
runtime.kill(handle)
"""
    runtime = DockerContainerRuntime()
    try:
        subprocess.run(
            [sys.executable, "-c", code, str(root), stamp, IMAGE],
            check=True,
            timeout=120,
        )
        reports = runtime.recover_orphans(runtime_root=root / "runtime")
        (root / "orphan-recovery.json").write_text(json.dumps(reports, indent=2))
        manager = AttemptWorkspaceManager(
            runtime_root=root / "runtime",
            output_root=root,
            run_id=root.name,
            invocation_id="new",
            templates={"test": WorkspaceTemplate("test", template)},
            protected_roots=(),
        )
        recovered = manager.recover_all_incomplete()
        (root / "workspace-recovery.json").write_text(json.dumps(recovered, indent=2))
        assert len(recovered["archives"]) == 1
        assert not recovered["quarantines"]
        assert manager.recover_all_incomplete()["status"] == "clean"
    finally:
        handle_file = root / "handle.json"
        if handle_file.exists():
            container_id = json.loads(handle_file.read_text())["id"]
            subprocess.run(
                ["docker", "rm", "-f", container_id], capture_output=True, timeout=30
            )
