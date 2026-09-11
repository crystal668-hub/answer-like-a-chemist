"""Real Docker fault and multimodal contracts, without provider calls."""

import base64
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.runtime.agent_workspace import (
    AttemptIdentity,
    AttemptWorkspaceManager,
    WorkspaceTemplate,
)
from benchmarking.runtime.bundles import RuntimePathProjection, ensure_runtime_bundle
from benchmarking.runtime.container_runtime import (
    ContainerAttemptSpec,
    ContainerMount,
    DockerContainerRuntime,
)
from benchmarking.service.single.prompts import build_single_llm_prompt

IMAGE = os.environ.get("BENCHMARK_TEST_CONTAINER_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="real Docker acceptance is opt-in")
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="


def run_root():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    root = (
        Path("state/benchmark-runs/temporary/infra-acceptance/no-llm")
        / f"infra-acceptance-no-llm-{stamp}"
    )
    root.mkdir(parents=True)
    return root.resolve(), stamp


@pytest.mark.parametrize("dataset", ["hle", "superchem"])
@pytest.mark.parametrize("skills", [True, False])
def test_prompt_reads_markdown_image_and_rejects_other_bundle(dataset, skills):
    root, stamp = run_root()
    (root / "image.png").write_bytes(base64.b64decode(PNG))
    payload = (
        {"image": str(root / "image.png")}
        if dataset == "hle"
        else {
            "question_image_paths": ["image.png"],
            "modality": "multimodal",
            "options": {"A": "one"},
        }
    )
    record = BenchmarkRecord(
        record_id="image",
        dataset=dataset,
        source_file=str(root / "data.jsonl"),
        prompt="Inspect the image",
        reference_answer="A",
        payload=payload,
        eval_kind="hle" if dataset == "hle" else "superchem_multiple_choice_rpf",
    )
    bundle = ensure_runtime_bundle(record, bundle_root=root / "input-bundles")
    workspace = root / "workspace"
    workspace.mkdir()
    projection = RuntimePathProjection(workspace, root / "skills", bundle)
    prompt = build_single_llm_prompt(
        record,
        websearch_enabled=False,
        skills_enabled=skills,
        input_bundle=projection.visible_bundle(),
        configured_skills=set(),
    )
    (workspace / "prompt.txt").write_text(prompt)
    code = r"""
import fs from 'node:fs';
import path from 'node:path';
import {validateToolCall} from '/opt/benchmark/benchmarking/runtime/openclaw_plugins/benchmark-workdir-guard/index.js';
const policy = {read_scopes:[{scope_id:'active_workspace',path:'/benchmark/workspace',kind:'directory'},
  {scope_id:'input',path:'/benchmark/input',kind:'directory'}],write_scopes:[],exec_workdir_scopes:[],protected_roots:[]};
const prompt=fs.readFileSync('/benchmark/workspace/prompt.txt','utf8');
const question=prompt.match(/Read the question bundle file first: (.+)/)[1];
if(!validateToolCall({policy,toolName:'read',params:{path:question}}).ok) throw Error('question blocked');
const markdown=fs.readFileSync(question,'utf8');
const image=path.join(path.dirname(question),markdown.match(new RegExp('images/[A-Za-z0-9.-]+'))[0]);
if(!validateToolCall({policy,toolName:'image',params:{images:[image]}}).ok) throw Error('image blocked');
if(fs.readFileSync(image).subarray(1,4).toString()!=='PNG') throw Error('missing image');
if(validateToolCall({policy,toolName:'read',params:{path:'/benchmark/other/question.md'}}).ok) throw Error('other record allowed');
if(validateToolCall({policy,toolName:'image',params:{images:[image,'/benchmark/other/image.png']}}).ok) throw Error('other image allowed');
if(validateToolCall({policy,toolName:'write',params:{path:image}}).ok) throw Error('input writable');
console.log(JSON.stringify({question,image,read:true}));
"""
    identity = AttemptIdentity(
        root.name, stamp, str(skills), "single_llm", "agent", dataset, 0, stamp, "test"
    )
    runtime = DockerContainerRuntime()
    handle = runtime.create(
        ContainerAttemptSpec(
            identity,
            IMAGE,
            ("node", "--input-type=module", "-e", code),
            mounts=(
                ContainerMount(
                    workspace, PurePosixPath("/benchmark/workspace"), "rw", "workspace"
                ),
                ContainerMount(
                    bundle.bundle_dir, PurePosixPath("/benchmark/input"), "ro", "input"
                ),
            ),
        )
    )
    try:
        runtime.start(handle)
        result = runtime.collect(handle, timeout_seconds=30)
        (root / "result.json").write_text(
            json.dumps({"stdout": result.stdout, "stderr": result.stderr})
        )
        assert result.return_code == 0, result.stderr
    finally:
        assert runtime.remove(handle, force=True).removed


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
