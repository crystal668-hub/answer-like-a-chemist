# Skill Injection Routing Architecture Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace benchmark skill injection from a broad text inventory with a record-scoped route-select-load-execute-trace architecture that lets agents use the right chemistry skill only when the task needs it.

**Architecture:** Add a deterministic `SkillPlan` preflight router before each benchmark record runs, shrink OpenClaw agent configs to the selected skills for that record, remove the 84-skill routing matrix from user prompts, preexecute deterministic providers where possible, and record trace/compliance metadata in `runner_meta`. Treat skill use as an observable contract, not as prompt-only advice.

**Tech Stack:** Python 3, pytest/unittest, OpenClaw config JSON, existing `benchmarking/*` modules, local skill bundles under `workspace/skills`.

---

## 中文摘要

这次 benchmark 复盘显示，`single_llm_skills_on` 和 `single_llm_skills_off` 在临时数据集上的结果没有质量收益：两组都是 `4/12`，`avg_normalized_score = 0.125`；skills-on 更慢，并且在 SuperChem RPF 上更差。transcript 中真正的核心问题不是“skill 内容不够”，而是 skill 注入架构没有形成可靠闭环：

- skills-on 给 agent 注入了 84 个 skill entries 和一张 compact routing table。
- 实际运行中几乎没有真实 skill 使用；只观察到一次读取 SuperChem 本地 question bundle 的工具调用。
- 没有看到 `rdkit`、`opsin`、`pubchem`、`chem-calculator` 或 paper pipeline 的有效调用。
- OpenClaw runtime 把 skills 渲染成 `<available_skills>` 文本，并提示“用 read 工具读取 skill 文件”；这是提示词建议，不是强制执行。
- 当前 benchmark runner 使用组级配置，`agents.list[].skills` 是整组 84-skill allowlist；没有按 record 缩小 skill 集。

因此，新 skills 没有在模型推理中带来可观察增益，反而增加了系统提示和注意力负担。修复方向是把 skill 从“文本注入”升级成“可路由、可限制、可执行、可追踪、可判责”的 runtime contract。

## 当前代码入口

需要重点修改的模块：

- `workspace/benchmarking/chemistry_routing.py`
  - 当前有 `route_skill_for_text()`、`requirements_for_text()`、`render_compact_skill_routing_table()`。
  - 新增 `SkillPlan`、计划生成、selected-skill 排序、trace/compliance helper。
- `workspace/benchmarking/prompts.py`
  - 当前 `build_single_llm_prompt()` 在 skills enabled 时注入完整 compact matrix。
  - 改为接受 `skill_plan`，只注入本 record 的简短 route contract。
- `workspace/benchmarking/config_renderer.py`
  - 当前 `render_run_config()` 把整个 `spec.skill_allowlist` 写入每个 runner agent。
  - 需要支持每条 record 的 selected skill filter。
- `workspace/benchmarking/runtime_config.py`
  - 当前 `ConfigPool.config_for_group()` 只生成 group 级 config。
  - 新增 record-scoped config 写入或 helper，避免污染 group 基线配置。
- `workspace/benchmarking/runners/single_llm.py`
  - 当前 `SingleLLMRunner.run()` 直接 build prompt 并复用 group config。
  - 改为先生成 `SkillPlan`，可选预执行 deterministic provider，再使用 record-scoped config/prompt 运行 agent。
- `workspace/benchmarking/reporting.py`
  - 结果 schema 保持兼容，通过 `runner_meta` 添加 skill trace，不需要立即提升顶层 schema。
- `workspace/benchmark_test.py`
  - 更新 wrapper 注入参数、常量、兼容测试。
- `workspace/GLOBAL_DEV_SPEC.md`
  - 实现后必须更新架构说明，因为 skill execution flow 发生变化。

需要更新或新增的测试：

- `workspace/tests/test_experimental_chemistry_skill_matrix.py`
- `workspace/tests/test_benchmark_prompts.py`
- `workspace/tests/test_benchmark_config_runtime.py`
- `workspace/tests/test_benchmark_test.py`
- 新增：`workspace/tests/test_benchmark_skill_planning.py`

## 目标状态

每条 benchmark record 执行前产生一个结构化计划：

```json
{
  "mode": "planned",
  "skills_enabled": true,
  "selected_skills": ["chem-calculator"],
  "primary_skill": "chem-calculator",
  "requirements": [
    {
      "skill": "chem-calculator",
      "trigger": "pH",
      "reason": "numeric chemistry calculation",
      "required": true,
      "preexecute": true
    }
  ],
  "prompt_contract": "Required skill route: chem-calculator. Use it for the numeric chemistry calculation or record why it is skipped.",
  "trace_required": true
}
```

运行后 `runner_meta` 至少包含：

```json
{
  "skill_plan": {
    "selected_skills": ["chem-calculator"],
    "primary_skill": "chem-calculator",
    "trace_required": true
  },
  "skill_executions": [
    {
      "skill": "chem-calculator",
      "mode": "preexecute",
      "status": "completed",
      "summary": "computed buffer pH input payload"
    }
  ],
  "skill_trace_required": true,
  "skill_trace_satisfied": true,
  "skill_noncompliance": false
}
```

## Non-Goals

- 不在本计划中重写 OpenClaw runtime 的 skill loader。
- 不要求所有 84 个 experimental skills 都有可执行 wrapper。
- 不把每个 skill 的完整 `SKILL.md` 注入 prompt。
- 不改变 benchmark evaluator 的打分逻辑。
- 不改变 ChemQA DebateClaw 基本协议拓扑。
- 不把 websearch 作为 skill 使用的替代品；websearch 仍是独立工具能力。

## Design Principles

1. Skill inventory is not reasoning context.
2. Route selection happens before the model sees the question.
3. The model should see only selected skills, not the full catalog.
4. Deterministic providers should run outside model attention when inputs are extractable.
5. Skill use must leave a machine-readable trace.
6. Missing required trace is a benchmark-visible execution-quality issue.
7. Config scoping is the enforcement boundary; prompt text is only the final instruction layer.
8. Existing no-skills baselines must remain clean and comparable.

## Task 1: Add Structured Skill Planning

**Files:**
- Modify: `workspace/benchmarking/chemistry_routing.py`
- Create: `workspace/tests/test_benchmark_skill_planning.py`

- [ ] **Step 1: Write tests for record-scoped plans**

Create `workspace/tests/test_benchmark_skill_planning.py`:

```python
from __future__ import annotations

from benchmarking.chemistry_routing import build_skill_plan
from benchmarking.datasets import BenchmarkRecord


def _record(prompt: str, *, eval_kind: str = "chembench_open_ended") -> BenchmarkRecord:
    return BenchmarkRecord(
        record_id="demo",
        dataset="chembench",
        source_file="/tmp/demo.jsonl",
        eval_kind=eval_kind,
        prompt=prompt,
        reference_answer="",
        payload={},
    )


def test_build_skill_plan_disabled_has_no_selected_skills() -> None:
    plan = build_skill_plan(_record("Calculate the pH."), skills_enabled=False)

    assert plan.mode == "disabled"
    assert plan.selected_skills == ()
    assert plan.primary_skill is None
    assert plan.trace_required is False
    assert "Do not use OpenClaw skills" in plan.prompt_contract


def test_build_skill_plan_selects_numeric_chemistry_skill() -> None:
    plan = build_skill_plan(
        _record("Calculate the pH of a buffer from concentration and pKa."),
        skills_enabled=True,
    )

    assert plan.mode == "planned"
    assert plan.primary_skill == "chem-calculator"
    assert plan.selected_skills == ("chem-calculator",)
    assert plan.trace_required is True
    assert plan.requirements[0]["skill"] == "chem-calculator"
    assert "chem-calculator" in plan.prompt_contract


def test_build_skill_plan_limits_selected_skills() -> None:
    plan = build_skill_plan(
        _record("Parse a CIF crystal structure and calculate pH for a solution."),
        skills_enabled=True,
        max_selected_skills=1,
    )

    assert len(plan.selected_skills) == 1
    assert plan.primary_skill in plan.selected_skills


def test_build_skill_plan_handles_no_route_without_catalog_noise() -> None:
    plan = build_skill_plan(_record("Explain why this qualitative answer follows."), skills_enabled=True)

    assert plan.mode == "no_route"
    assert plan.selected_skills == ()
    assert plan.trace_required is False
    assert "Experimental chemistry skill routing rules" not in plan.prompt_contract
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest workspace/tests/test_benchmark_skill_planning.py -q
```

Expected: FAIL because `build_skill_plan` and `SkillPlan` do not exist.

- [ ] **Step 3: Implement `SkillPlan` and `build_skill_plan`**

In `workspace/benchmarking/chemistry_routing.py`, add:

```python
from dataclasses import asdict, dataclass


CORE_DETERMINISTIC_SKILLS = {"chem-calculator", "rdkit", "opsin", "pubchem"}


@dataclass(frozen=True)
class SkillPlan:
    mode: str
    skills_enabled: bool
    selected_skills: tuple[str, ...]
    primary_skill: str | None
    requirements: tuple[dict[str, Any], ...]
    prompt_contract: str
    trace_required: bool

    def to_meta(self) -> dict[str, Any]:
        return asdict(self)
```

Add:

```python
def build_skill_plan(
    record: Any,
    *,
    skills_enabled: bool,
    max_selected_skills: int = 3,
) -> SkillPlan:
    if not skills_enabled:
        return SkillPlan(
            mode="disabled",
            skills_enabled=False,
            selected_skills=(),
            primary_skill=None,
            requirements=(),
            prompt_contract="Do not use OpenClaw skills or local skill tools for this run.",
            trace_required=False,
        )

    text = _record_route_text(record)
    raw_requirements = requirements_for_text(text)
    if not raw_requirements:
        return SkillPlan(
            mode="no_route",
            skills_enabled=True,
            selected_skills=(),
            primary_skill=None,
            requirements=(),
            prompt_contract=(
                "No chemistry skill route was selected for this record. "
                "Use ordinary reasoning and avoid loading unrelated skills."
            ),
            trace_required=False,
        )

    selected: list[str] = []
    requirements: list[dict[str, Any]] = []
    for item in raw_requirements:
        skill = str(item["skill"])
        if skill in selected:
            continue
        if len(selected) >= max_selected_skills:
            break
        selected.append(skill)
        requirements.append(
            {
                "skill": skill,
                "trigger": str(item.get("trigger") or ""),
                "reason": str(item.get("reason") or ""),
                "required": True,
                "preexecute": skill in CORE_DETERMINISTIC_SKILLS,
            }
        )

    primary = selected[0] if selected else None
    lines = ["Selected chemistry skill route for this record:"]
    for item in requirements:
        lines.append(
            f"- `{item['skill']}` required by trigger `{item['trigger']}`: {item['reason']}"
        )
    lines.append(
        "Use the selected skill when it materially applies. "
        "If you skip it, state `SKILL TRACE: skipped <skill> because <reason>` in the answer."
    )
    return SkillPlan(
        mode="planned",
        skills_enabled=True,
        selected_skills=tuple(selected),
        primary_skill=primary,
        requirements=tuple(requirements),
        prompt_contract="\n".join(lines),
        trace_required=bool(selected),
    )


def _record_route_text(record: Any) -> str:
    parts = [
        str(getattr(record, "dataset", "") or ""),
        str(getattr(record, "eval_kind", "") or ""),
        str(getattr(record, "prompt", "") or ""),
        str(getattr(record, "reference_answer", "") or ""),
    ]
    payload = getattr(record, "payload", None)
    if isinstance(payload, dict):
        parts.extend(str(value) for value in payload.values() if isinstance(value, (str, int, float)))
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests**

Run:

```bash
pytest workspace/tests/test_benchmark_skill_planning.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workspace/benchmarking/chemistry_routing.py workspace/tests/test_benchmark_skill_planning.py
git commit -m "feat: add benchmark skill planning"
```

## Task 2: Replace Full Prompt Matrix With Selected Route Contract

**Files:**
- Modify: `workspace/benchmarking/prompts.py`
- Modify: `workspace/tests/test_benchmark_prompts.py`
- Modify: `workspace/tests/test_experimental_chemistry_skill_matrix.py`
- Modify: `workspace/tests/test_benchmark_test.py`

- [ ] **Step 1: Update prompt tests**

In `workspace/tests/test_benchmark_prompts.py`, replace the current skills-on prompt assertion with:

```python
def test_single_llm_prompt_uses_selected_skill_plan_without_catalog() -> None:
    from benchmarking.chemistry_routing import build_skill_plan

    record = BenchmarkRecord(
        record_id="fs-1",
        dataset="frontierscience",
        source_file="/tmp/frontierscience.jsonl",
        eval_kind="frontierscience_olympiad",
        prompt="Calculate the pH of a buffer from concentration and pKa.",
        reference_answer="4.7",
        payload={"track": "olympiad"},
    )
    skill_plan = build_skill_plan(record, skills_enabled=True)

    prompt = build_single_llm_prompt(
        record,
        websearch_enabled=True,
        skills_enabled=True,
        skill_plan=skill_plan,
    )

    assert "Selected chemistry skill route for this record:" in prompt
    assert "`chem-calculator`" in prompt
    assert "`pymatgen`" not in prompt
    assert "Experimental chemistry skill routing rules" not in prompt
```

Update the no-skills test to pass `skill_plan=None` and keep:

```python
assert "Do not use OpenClaw skills" in skills_off
```

- [ ] **Step 2: Update legacy benchmark wrapper prompt test**

In `workspace/tests/test_benchmark_test.py`, update `test_build_single_llm_prompt_injects_skill_routing_only_when_enabled` so skills-on asserts selected route only:

```python
from benchmarking.chemistry_routing import build_skill_plan

skill_plan = build_skill_plan(record, skills_enabled=True)
skills_on = benchmark_test.build_single_llm_prompt(
    record,
    websearch_enabled=True,
    skills_enabled=True,
    input_bundle=None,
    skill_plan=skill_plan,
)

self.assertIn("Selected chemistry skill route for this record", skills_on)
self.assertIn("`chem-calculator`", skills_on)
self.assertNotIn("`pymatgen`", skills_on)
self.assertNotIn("Experimental chemistry skill routing rules", skills_on)
```

- [ ] **Step 3: Keep matrix rendering test but stop treating it as runtime prompt**

In `workspace/tests/test_experimental_chemistry_skill_matrix.py`, rename:

```python
def test_single_agent_prompt_injects_same_compact_matrix() -> None:
```

to:

```python
def test_single_agent_prompt_does_not_inject_full_compact_matrix() -> None:
```

Use a `pymatgen` plan and assert only selected route appears:

```python
from benchmarking.chemistry_routing import build_skill_plan

skill_plan = build_skill_plan(record, skills_enabled=True)
prompt = build_single_llm_prompt(record, websearch_enabled=False, skill_plan=skill_plan)

assert "Selected chemistry skill route for this record:" in prompt
assert "`pymatgen`" in prompt
assert "`tooluniverse-chemical-safety`" not in prompt
assert "Experimental chemistry skill routing rules:" not in prompt
```

- [ ] **Step 4: Run tests to verify failure**

Run:

```bash
pytest workspace/tests/test_benchmark_prompts.py workspace/tests/test_experimental_chemistry_skill_matrix.py workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_build_single_llm_prompt_injects_skill_routing_only_when_enabled -q
```

Expected: FAIL because `build_single_llm_prompt()` does not accept `skill_plan`.

- [ ] **Step 5: Modify prompt builder**

In `workspace/benchmarking/prompts.py`, change the function signature:

```python
def build_single_llm_prompt(
    record: BenchmarkRecord,
    *,
    websearch_enabled: bool,
    skills_enabled: bool = True,
    input_bundle: RuntimeBundleLike | None = None,
    skill_plan: Any | None = None,
) -> str:
```

Replace the skills block with:

```python
    if skills_enabled:
        contract = str(getattr(skill_plan, "prompt_contract", "") or "").strip()
        if contract:
            instructions.append(contract)
        else:
            instructions.append(
                "No chemistry skill route was selected for this record. "
                "Use ordinary reasoning and avoid loading unrelated skills."
            )
    else:
        instructions.append("Do not use OpenClaw skills or local skill tools for this run.")
```

Remove the import of `render_compact_skill_routing_table` from this module.

- [ ] **Step 6: Run tests**

Run:

```bash
pytest workspace/tests/test_benchmark_prompts.py workspace/tests/test_experimental_chemistry_skill_matrix.py workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_build_single_llm_prompt_injects_skill_routing_only_when_enabled -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add workspace/benchmarking/prompts.py workspace/tests/test_benchmark_prompts.py workspace/tests/test_experimental_chemistry_skill_matrix.py workspace/tests/test_benchmark_test.py
git commit -m "feat: scope skill prompt to selected route"
```

## Task 3: Add Record-Scoped OpenClaw Config Rendering

**Files:**
- Modify: `workspace/benchmarking/config_renderer.py`
- Modify: `workspace/benchmarking/runtime_config.py`
- Modify: `workspace/tests/test_benchmark_config_runtime.py`

- [ ] **Step 1: Add config renderer test**

Add to `workspace/tests/test_benchmark_config_runtime.py`:

```python
def test_render_run_config_accepts_runner_skill_override(self) -> None:
    base = {
        "agents": {"list": []},
        "tools": {"web": {"search": {"enabled": False}}},
        "plugins": {"entries": {"duckduckgo": {"enabled": False, "config": {}}}},
    }
    spec = ExperimentSpec(
        id="single_llm_skills_on",
        label="Single LLM with skills",
        runner_kind="single_llm",
        websearch_enabled=True,
        skills_enabled=True,
        single_agent_id="benchmark-single-skills-on",
        skill_allowlist=("chem-calculator", "rdkit", "pymatgen"),
    )
    provisioned = ProvisionedExperiment(
        judge=ProvisionedAgent("benchmark-judge", Path("/tmp/judge"), Path("/tmp/agents/judge")),
        runner_agents=(
            ProvisionedAgent("benchmark-single-skills-on", Path("/tmp/single"), Path("/tmp/agents/single")),
        ),
    )

    rendered = render_run_config(
        base_payload=base,
        spec=spec,
        provisioned=provisioned,
        judge_model="su8/gpt-5.4",
        runner_model="qwen3.5-plus",
        runner_skill_override=("chem-calculator",),
    )

    agents = {entry["id"]: entry for entry in rendered["agents"]["list"]}
    assert agents["benchmark-single-skills-on"]["skills"] == ["chem-calculator"]
```

- [ ] **Step 2: Add ConfigPool record path test**

Add a test with a temp context:

```python
def test_config_pool_writes_record_scoped_config_with_selected_skills(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        base_config = root / "openclaw.json"
        base_config.write_text(
            json.dumps(
                {
                    "agents": {"list": []},
                    "tools": {"web": {"search": {"enabled": False}}},
                    "plugins": {"entries": {"duckduckgo": {"enabled": False, "config": {}}}},
                }
            ),
            encoding="utf-8",
        )
        context = RuntimeConfigContext(
            baseline_workspace_root=root / "benchmark-runtime",
            chemqa_workspace_roots={"A": root / "benchmark-runtime" / "chemqa_skills_on"},
            agents_root=root / "agents",
            judge_agent_id="benchmark-judge",
            chemqa_slot_sets={"chemqa_skills_on": "A"},
            experiment_specs={
                "single_llm_skills_on": ExperimentSpec(
                    id="single_llm_skills_on",
                    label="Single LLM with skills",
                    runner_kind="single_llm",
                    websearch_enabled=True,
                    skills_enabled=True,
                    single_agent_id="benchmark-single-skills-on",
                    skill_allowlist=("chem-calculator", "rdkit"),
                )
            },
            load_slot_agents_template=lambda: "# slot template\n",
            benchmark_skills_root=root / "workspace" / "skills",
        )
        pool = ConfigPool(
            base_config_path=base_config,
            output_root=root / "out",
            context=context,
            single_agent_model="qwen3.5-plus",
            judge_model="su8/gpt-5.4",
        )
        group = RuntimeConfigGroup(
            id="single_llm_skills_on",
            label="Single LLM with skills",
            runner="single_llm",
            websearch=True,
            skills_enabled=True,
        )

        path = pool.config_for_record(
            group,
            record_id="chembench-0001",
            selected_skills=("chem-calculator",),
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        agents = {entry["id"]: entry for entry in payload["agents"]["list"]}
        assert agents["benchmark-single-skills-on"]["skills"] == ["chem-calculator"]
        assert path.name == "single_llm_skills_on-chembench-0001-openclaw.json"
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
pytest workspace/tests/test_benchmark_config_runtime.py -q
```

Expected: FAIL because override arguments do not exist.

- [ ] **Step 4: Add override argument to `render_run_config`**

In `workspace/benchmarking/config_renderer.py`, update:

```python
def render_run_config(
    *,
    base_payload: dict[str, Any],
    spec: ExperimentSpec,
    provisioned: ProvisionedExperiment,
    judge_model: str,
    runner_model: str,
    runner_skill_override: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
```

Replace runner skill calculation:

```python
    if runner_skill_override is not None:
        runner_skills = list(runner_skill_override)
    else:
        runner_skills = list(spec.skill_allowlist or ()) if spec.skills_enabled else []
```

- [ ] **Step 5: Thread override through runtime config**

In `workspace/benchmarking/runtime_config.py`, update `_render_run_config_or_raise()` and `build_run_scoped_config_payload()` to accept:

```python
runner_skill_override: tuple[str, ...] | list[str] | None = None
```

Pass it through to `render_run_config()`.

Add method to `ConfigPool`:

```python
def config_for_record(
    self,
    group: ExperimentGroupLike,
    *,
    record_id: str,
    selected_skills: tuple[str, ...] | list[str],
) -> Path:
    safe_record = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(record_id))[:80]
    payload = build_run_scoped_config_payload(
        self._payload,
        context=self.context,
        group=group,
        single_agent_model=self._single_agent_model,
        judge_model=self._judge_model,
        single_agent_id_override=self._single_agent_id_override,
        runner_skill_override=tuple(selected_skills),
    )
    path = self._config_dir / f"{group.id}-{safe_record}-openclaw.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
```

- [ ] **Step 6: Run tests**

Run:

```bash
pytest workspace/tests/test_benchmark_config_runtime.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add workspace/benchmarking/config_renderer.py workspace/benchmarking/runtime_config.py workspace/tests/test_benchmark_config_runtime.py
git commit -m "feat: render record-scoped skill configs"
```

## Task 4: Wire SkillPlan Into SingleLLMRunner

**Files:**
- Modify: `workspace/benchmarking/runners/single_llm.py`
- Modify: `workspace/benchmark_test.py`
- Modify: `workspace/tests/test_benchmark_test.py`

- [ ] **Step 1: Add runner test for skill plan metadata and config scoping**

In `workspace/tests/test_benchmark_test.py`, add:

```python
def test_single_llm_runner_uses_record_scoped_skill_plan_and_config(self) -> None:
    captured: dict[str, object] = {}
    original_run_subprocess = benchmark_test.run_subprocess
    original_ensure_runtime_bundle = benchmark_test.ensure_runtime_bundle
    try:
        def fake_run_subprocess(command: list[str], *, env=None, cwd=None, timeout=None):
            captured["command"] = list(command)
            captured["env"] = dict(env or {})
            return benchmark_test.subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"result": {"payloads": [{"text": "Reasoning\nFINAL ANSWER: 4.7"}], "meta": {}}}),
                stderr="",
            )

        class FakeConfigPool:
            def config_for_record(self, group, *, record_id, selected_skills):
                captured["record_id"] = record_id
                captured["selected_skills"] = tuple(selected_skills)
                return Path("/tmp/record-scoped-openclaw.json")

        benchmark_test.run_subprocess = fake_run_subprocess
        benchmark_test.ensure_runtime_bundle = lambda record, bundle_root: None
        runner = benchmark_test.SingleLLMRunner(
            agent_id="benchmark-single-skills-on",
            timeout_seconds=30,
            config_path=Path("/tmp/group.json"),
            runtime_bundle_root=Path("/tmp"),
            config_pool=FakeConfigPool(),
        )
        record = benchmark_test.BenchmarkRecord(
            record_id="chembench-0001",
            dataset="chembench",
            source_file="/tmp/demo.jsonl",
            eval_kind="chembench_open_ended",
            prompt="Calculate the pH of a buffer from concentration and pKa.",
            reference_answer="4.7",
            payload={},
        )

        out = runner.run(record, benchmark_test.EXPERIMENT_GROUPS["single_llm_skills_on"])

        assert captured["selected_skills"] == ("chem-calculator",)
        assert captured["env"]["OPENCLAW_CONFIG_PATH"] == "/tmp/record-scoped-openclaw.json"
        assert out.runner_meta["skill_plan"]["primary_skill"] == "chem-calculator"
        assert out.runner_meta["skill_trace_required"] is True
    finally:
        benchmark_test.run_subprocess = original_run_subprocess
        benchmark_test.ensure_runtime_bundle = original_ensure_runtime_bundle
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_single_llm_runner_uses_record_scoped_skill_plan_and_config -q
```

Expected: FAIL because runner does not accept `config_pool` or build `SkillPlan`.

- [ ] **Step 3: Update runner constructor**

In `workspace/benchmarking/runners/single_llm.py`, import:

```python
from ..chemistry_routing import build_skill_plan
```

Add optional constructor argument:

```python
        config_pool: Any | None = None,
```

Store:

```python
        self.config_pool = config_pool
```

- [ ] **Step 4: Build plan and config in `run()`**

Before prompt construction:

```python
        skills_enabled = bool(getattr(group, "skills_enabled", True))
        skill_plan = build_skill_plan(record, skills_enabled=skills_enabled)
        config_path = self.config_path
        if self.config_pool is not None and skills_enabled:
            config_path = self.config_pool.config_for_record(
                group,
                record_id=str(getattr(record, "record_id", "") or "record"),
                selected_skills=skill_plan.selected_skills,
            )
```

Pass the plan to prompt builder:

```python
        prompt = self._build_single_llm_prompt(
            record,
            websearch_enabled=group.websearch,
            skills_enabled=skills_enabled,
            input_bundle=input_bundle,
            skill_plan=skill_plan,
        )
```

Use `config_path` in env:

```python
        env["OPENCLAW_CONFIG_PATH"] = str(config_path)
```

Add metadata:

```python
        runner_meta["skill_plan"] = skill_plan.to_meta()
        runner_meta["skill_trace_required"] = skill_plan.trace_required
        runner_meta.setdefault("skill_executions", [])
        runner_meta["skill_trace_satisfied"] = _skill_trace_satisfied(
            full_response_text=full_response_text,
            runner_meta=runner_meta,
            selected_skills=skill_plan.selected_skills,
        )
        runner_meta["skill_noncompliance"] = bool(
            skill_plan.trace_required and not runner_meta["skill_trace_satisfied"]
        )
```

Add helper:

```python
def _skill_trace_satisfied(
    *,
    full_response_text: str,
    runner_meta: dict[str, Any],
    selected_skills: tuple[str, ...],
) -> bool:
    if not selected_skills:
        return True
    executions = runner_meta.get("skill_executions") or []
    if isinstance(executions, list):
        executed = {
            str(item.get("skill"))
            for item in executions
            if isinstance(item, dict) and str(item.get("status") or "") in {"completed", "skipped"}
        }
        if set(selected_skills) & executed:
            return True
    text = str(full_response_text or "").lower()
    return any(f"skill trace:" in text and skill.lower() in text for skill in selected_skills)
```

- [ ] **Step 5: Update `benchmark_test.SingleLLMRunner` wrapper**

In `workspace/benchmark_test.py`, update the wrapper constructor to accept and pass `config_pool=None`:

```python
class SingleLLMRunner(_BenchmarkingSingleLLMRunner):
    def __init__(
        self,
        *,
        agent_id: str,
        timeout_seconds: int,
        config_path: Path,
        runtime_bundle_root: Path,
        config_pool: Any | None = None,
    ) -> None:
        super().__init__(
            ...
            config_pool=config_pool,
        )
```

Update wherever the runner is created in `run_group()` to pass the shared config pool object when available.

- [ ] **Step 6: Run focused tests**

Run:

```bash
pytest workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_single_llm_runner_invokes_openclaw_with_high_thinking workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_single_llm_runner_uses_record_scoped_skill_plan_and_config -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add workspace/benchmarking/runners/single_llm.py workspace/benchmark_test.py workspace/tests/test_benchmark_test.py
git commit -m "feat: wire skill planning into single runner"
```

## Task 5: Add Deterministic Provider Preexecution

**Files:**
- Create: `workspace/benchmarking/skill_preexecution.py`
- Modify: `workspace/benchmarking/runners/single_llm.py`
- Create: `workspace/tests/test_benchmark_skill_preexecution.py`

- [ ] **Step 1: Write preexecution tests**

Create `workspace/tests/test_benchmark_skill_preexecution.py`:

```python
from __future__ import annotations

from benchmarking.chemistry_routing import SkillPlan
from benchmarking.skill_preexecution import preexecute_skill_plan


def test_preexecute_returns_skipped_for_non_deterministic_skill() -> None:
    plan = SkillPlan(
        mode="planned",
        skills_enabled=True,
        selected_skills=("pymatgen",),
        primary_skill="pymatgen",
        requirements=(
            {
                "skill": "pymatgen",
                "trigger": "CIF",
                "reason": "structure analysis",
                "required": True,
                "preexecute": False,
            },
        ),
        prompt_contract="Selected route",
        trace_required=True,
    )

    executions = preexecute_skill_plan(plan, record_prompt="Analyze this CIF.", skills_root="/tmp/skills")

    assert executions == []


def test_preexecute_records_unavailable_input_without_crashing() -> None:
    plan = SkillPlan(
        mode="planned",
        skills_enabled=True,
        selected_skills=("rdkit",),
        primary_skill="rdkit",
        requirements=(
            {
                "skill": "rdkit",
                "trigger": "SMILES",
                "reason": "structure reasoning",
                "required": True,
                "preexecute": True,
            },
        ),
        prompt_contract="Selected route",
        trace_required=True,
    )

    executions = preexecute_skill_plan(plan, record_prompt="No machine-readable molecule here.", skills_root="/tmp/skills")

    assert executions[0]["skill"] == "rdkit"
    assert executions[0]["status"] == "skipped"
    assert executions[0]["reason"] == "no_extractable_input"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest workspace/tests/test_benchmark_skill_preexecution.py -q
```

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement minimal safe preexecution module**

Create `workspace/benchmarking/skill_preexecution.py`:

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .chemistry_routing import SkillPlan


SMILES_RE = re.compile(r"\b(?:SMILES|smiles)\s*[:=]\s*([A-Za-z0-9@+\-\[\]\(\)=#$\\/%.]+)")


def preexecute_skill_plan(
    plan: SkillPlan,
    *,
    record_prompt: str,
    skills_root: str | Path,
) -> list[dict[str, Any]]:
    executions: list[dict[str, Any]] = []
    root = Path(skills_root)
    for requirement in plan.requirements:
        if not bool(requirement.get("preexecute")):
            continue
        skill = str(requirement.get("skill") or "")
        if skill == "rdkit":
            executions.append(_preexecute_rdkit(record_prompt=record_prompt, skills_root=root))
        elif skill in {"chem-calculator", "opsin", "pubchem"}:
            executions.append(
                {
                    "skill": skill,
                    "mode": "preexecute",
                    "status": "skipped",
                    "reason": "preexecution_adapter_not_implemented",
                }
            )
    return executions


def _preexecute_rdkit(*, record_prompt: str, skills_root: Path) -> dict[str, Any]:
    match = SMILES_RE.search(record_prompt or "")
    if not match:
        return {
            "skill": "rdkit",
            "mode": "preexecute",
            "status": "skipped",
            "reason": "no_extractable_input",
        }
    script = skills_root / "rdkit" / "scripts" / "structure_summary.py"
    if not script.is_file():
        return {
            "skill": "rdkit",
            "mode": "preexecute",
            "status": "skipped",
            "reason": "adapter_missing",
            "script": str(script),
        }
    return {
        "skill": "rdkit",
        "mode": "preexecute",
        "status": "skipped",
        "reason": "adapter_wireup_pending",
        "input": {"smiles": match.group(1)},
        "script": str(script),
    }
```

- [ ] **Step 4: Wire preexecution into runner metadata**

In `workspace/benchmarking/runners/single_llm.py`, import:

```python
from ..skill_preexecution import preexecute_skill_plan
```

Add constructor arg:

```python
        skills_root: Path | None = None,
```

Before prompt construction:

```python
        skill_executions: list[dict[str, Any]] = []
        if skills_enabled and self.skills_root is not None:
            skill_executions = preexecute_skill_plan(
                skill_plan,
                record_prompt=str(getattr(record, "prompt", "") or ""),
                skills_root=self.skills_root,
            )
```

After runner meta creation:

```python
        runner_meta["skill_executions"] = skill_executions + list(runner_meta.get("skill_executions") or [])
```

Pass `skills_root=runtime_paths.skills_root` from `benchmark_test.SingleLLMRunner`.

- [ ] **Step 5: Run tests**

Run:

```bash
pytest workspace/tests/test_benchmark_skill_preexecution.py workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_single_llm_runner_uses_record_scoped_skill_plan_and_config -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add workspace/benchmarking/skill_preexecution.py workspace/benchmarking/runners/single_llm.py workspace/benchmark_test.py workspace/tests/test_benchmark_skill_preexecution.py workspace/tests/test_benchmark_test.py
git commit -m "feat: record deterministic skill preexecution traces"
```

## Task 6: Classify Skill Noncompliance in Benchmark Results

**Files:**
- Modify: `workspace/benchmark_test.py`
- Modify: `workspace/benchmarking/reporting.py`
- Modify: `workspace/tests/test_benchmark_test.py`

- [ ] **Step 1: Add result classification test**

Add to `workspace/tests/test_benchmark_test.py`:

```python
def test_run_group_marks_skill_noncompliance_as_degraded_execution(self) -> None:
    class StubRunner:
        def __init__(self, **kwargs):
            pass

        def run(self, record, group):
            return benchmark_test.RunnerResult(
                status=benchmark_test.RunStatus.COMPLETED,
                answer=benchmark_test.AnswerPayload(
                    short_answer_text="4.7",
                    full_response_text="FINAL ANSWER: 4.7",
                ),
                raw={},
                runner_meta={
                    "skill_trace_required": True,
                    "skill_trace_satisfied": False,
                    "skill_noncompliance": True,
                    "skill_plan": {"selected_skills": ["chem-calculator"]},
                },
            )

    original_runner = benchmark_test.SingleLLMRunner
    benchmark_test.SingleLLMRunner = StubRunner
    try:
        record = benchmark_test.BenchmarkRecord(
            record_id="r1",
            dataset="chembench",
            source_file="/tmp/demo.jsonl",
            eval_kind="chembench_open_ended",
            prompt="Calculate pH.",
            reference_answer="4.7",
            payload={},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            results = benchmark_test.run_group(
                group=benchmark_test.EXPERIMENT_GROUPS["single_llm_skills_on"],
                records=[record],
                output_root=Path(tmpdir),
                config_path=Path("/tmp/openclaw.json"),
                judge_client=None,
                single_agent="benchmark-single-skills-on",
                timeout_seconds=30,
                runtime_bundle_root=Path(tmpdir) / "bundles",
            )

        assert results[0].degraded_execution is True
        assert results[0].runner_meta["skill_noncompliance"] is True
    finally:
        benchmark_test.SingleLLMRunner = original_runner
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
pytest workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_run_group_marks_skill_noncompliance_as_degraded_execution -q
```

Expected: FAIL because axes do not account for `skill_noncompliance`.

- [ ] **Step 3: Update result axes classification**

In `workspace/benchmark_test.py`, find the area that builds `axes` from `run_result.runner_meta`. Add:

```python
skill_noncompliance = bool((run_result.runner_meta or {}).get("skill_noncompliance"))
if skill_noncompliance:
    axes["degraded_execution"] = True
    axes.setdefault("recovery_mode", "none")
```

Do not mark the answer non-evaluable solely because of missing skill trace. The evaluation should still score the answer, while aggregate diagnostics expose degraded execution.

- [ ] **Step 4: Add aggregate diagnostics**

In `workspace/benchmarking/reporting.py`, add to `aggregate_bucket()`:

```python
        "skill_trace_required_count": sum(1 for item in items if (item.runner_meta or {}).get("skill_trace_required")),
        "skill_trace_satisfied_count": sum(1 for item in items if (item.runner_meta or {}).get("skill_trace_satisfied")),
        "skill_noncompliance_count": sum(1 for item in items if (item.runner_meta or {}).get("skill_noncompliance")),
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
pytest workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_run_group_marks_skill_noncompliance_as_degraded_execution workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_aggregate_results_includes_status_axes -q
```

Expected: PASS after updating any aggregate expected fields in tests.

- [ ] **Step 6: Commit**

```bash
git add workspace/benchmark_test.py workspace/benchmarking/reporting.py workspace/tests/test_benchmark_test.py
git commit -m "feat: report skill trace compliance"
```

## Task 7: Extend The Same Contract To ChemQA

**Files:**
- Modify: `workspace/benchmarking/prompts.py`
- Modify: `workspace/benchmarking/runners/chemqa.py`
- Modify: `workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`
- Modify: `workspace/tests/test_benchmark_test.py`

- [ ] **Step 1: Add ChemQA goal test**

Add to `workspace/tests/test_benchmark_test.py`:

```python
def test_chemqa_goal_includes_selected_skill_contract_when_provided(self) -> None:
    from benchmarking.chemistry_routing import build_skill_plan

    record = benchmark_test.BenchmarkRecord(
        record_id="fs-1",
        dataset="frontierscience",
        source_file="/tmp/frontierscience.jsonl",
        eval_kind="frontierscience_olympiad",
        prompt="Calculate the pH of a buffer from concentration and pKa.",
        reference_answer="4.7",
        payload={"track": "olympiad"},
    )
    plan = build_skill_plan(record, skills_enabled=True)

    goal = benchmark_test.build_chemqa_goal(record, websearch_enabled=True, skill_plan=plan)

    assert "Selected chemistry skill route for this record:" in goal
    assert "`chem-calculator`" in goal
    assert "`pymatgen`" not in goal
```

- [ ] **Step 2: Update `build_chemqa_goal()`**

In `workspace/benchmarking/prompts.py`, add argument:

```python
    skill_plan: Any | None = None,
```

After websearch instruction:

```python
    contract = str(getattr(skill_plan, "prompt_contract", "") or "").strip()
    if contract:
        instructions.append(contract)
```

- [ ] **Step 3: Compute plan in ChemQA runner**

In `workspace/benchmarking/runners/chemqa.py`, compute:

```python
skill_plan = build_skill_plan(record, skills_enabled=bool(getattr(group, "skills_enabled", True)))
```

Pass to `build_chemqa_goal()`. Add to `runner_meta`:

```python
"skill_plan": skill_plan.to_meta(),
"skill_trace_required": skill_plan.trace_required,
```

- [ ] **Step 4: Pass selected skills into ChemQA run metadata**

When materializing launch metadata or environment variables for ChemQA, add:

```python
env["CHEMQA_BENCHMARK_SKILL_PLAN_JSON"] = json.dumps(skill_plan.to_meta(), ensure_ascii=False)
```

In `workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py`, read that environment variable once and include selected skill contract in role prompts. If env var is missing or invalid, proceed with no skill contract.

- [ ] **Step 5: Run focused tests**

Run:

```bash
pytest workspace/tests/test_benchmark_test.py::BenchmarkTestCase::test_chemqa_goal_includes_selected_skill_contract_when_provided -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add workspace/benchmarking/prompts.py workspace/benchmarking/runners/chemqa.py workspace/skills/chemqa-review/scripts/chemqa_review_openclaw_driver.py workspace/tests/test_benchmark_test.py
git commit -m "feat: pass selected skill contracts to chemqa"
```

## Task 8: Update Documentation And Global Spec

**Files:**
- Modify: `workspace/GLOBAL_DEV_SPEC.md`
- Modify or create: `workspace/docs/superpowers/specs/2026-05-07-skill-injection-routing-architecture-repair-plan.md`

- [ ] **Step 1: Update global spec capability status**

In `workspace/GLOBAL_DEV_SPEC.md`, replace the current skill matrix wording that says prompts receive compact grouped route summaries and agents read `SKILL.md` after route selection with the new implemented behavior:

```markdown
- `DONE`: Provide an experimental medium-or-higher-value chemistry skill routing matrix via `workspace/skills/chemistry-routing-matrix.json`.
  Benchmark execution now computes a per-record `SkillPlan`, narrows runner agent `skills` config to the selected route set, injects only the selected route contract into prompts, records `runner_meta.skill_plan` and skill trace compliance, and avoids exposing the full 84-skill catalog as task context.
```

- [ ] **Step 2: Add architecture note**

Under `Source modules -> workspace/benchmarking/`, update:

```markdown
- `chemistry_routing.py`
  - Loads the chemistry routing matrix and builds record-scoped `SkillPlan` objects.
- `skill_preexecution.py`
  - Records deterministic provider preexecution/skipped traces for selected core chemistry skills.
```

- [ ] **Step 3: Run docs sanity search**

Run:

```bash
rg -n "compact grouped route summaries|Experimental chemistry skill routing rules|84 local skill routes" workspace/GLOBAL_DEV_SPEC.md workspace/docs/superpowers/specs
```

Expected: no stale claim that runtime prompts inject the full compact matrix by default.

- [ ] **Step 4: Commit**

```bash
git add workspace/GLOBAL_DEV_SPEC.md workspace/docs/superpowers/specs/2026-05-07-skill-injection-routing-architecture-repair-plan.md
git commit -m "docs: document skill routing architecture repair"
```

## Task 9: Final Verification On Temporary Benchmark Shape

**Files:**
- No source changes unless verification reveals a defect.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest \
  workspace/tests/test_benchmark_skill_planning.py \
  workspace/tests/test_benchmark_skill_preexecution.py \
  workspace/tests/test_benchmark_prompts.py \
  workspace/tests/test_experimental_chemistry_skill_matrix.py \
  workspace/tests/test_benchmark_config_runtime.py \
  workspace/tests/test_benchmark_test.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Inspect generated prompt length**

Use a small script or pytest assertion to compare old failure mode:

```python
from benchmarking.chemistry_routing import build_skill_plan
from benchmarking.datasets import BenchmarkRecord
from benchmarking.prompts import build_single_llm_prompt

record = BenchmarkRecord(
    record_id="pH-demo",
    dataset="chembench",
    source_file="/tmp/demo.jsonl",
    eval_kind="chembench_open_ended",
    prompt="Calculate the pH of a buffer from concentration and pKa.",
    reference_answer="4.7",
    payload={},
)
plan = build_skill_plan(record, skills_enabled=True)
prompt = build_single_llm_prompt(record, websearch_enabled=True, skills_enabled=True, skill_plan=plan)
assert "Experimental chemistry skill routing rules" not in prompt
assert "`pymatgen`" not in prompt
assert len(prompt) < 4000
```

- [ ] **Step 3: Run a tiny benchmark smoke test**

Use one chemistry numeric record and one no-route qualitative record. The exact command depends on the local benchmark CLI arguments, but the smoke test must include `single_llm_skills_on` and inspect per-record JSON.

Expected per-record JSON:

```json
{
  "runner_meta": {
    "skill_plan": {
      "selected_skills": ["chem-calculator"]
    },
    "skill_trace_required": true
  }
}
```

For no-route records:

```json
{
  "runner_meta": {
    "skill_plan": {
      "mode": "no_route",
      "selected_skills": []
    },
    "skill_trace_required": false
  }
}
```

- [ ] **Step 4: Commit any verification fixes**

Only if Step 1-3 revealed defects:

```bash
git add <changed files>
git commit -m "fix: stabilize benchmark skill routing verification"
```

## Acceptance Criteria

- `single_llm_skills_on` no longer exposes the full 84-skill catalog in the task prompt.
- `agents.list[].skills` for a skills-on record contains only that record's selected skills.
- Records with no route do not receive unrelated skill entries or route matrix text.
- Deterministic provider opportunities are visible in `runner_meta.skill_executions`, even when skipped due to missing extractable input.
- Missing required skill trace is visible as `runner_meta.skill_noncompliance = true` and contributes to aggregate diagnostics.
- The no-skills baseline still renders `skills: []` and still says not to use OpenClaw skills.
- Existing evaluator behavior and benchmark score compatibility remain unchanged.

## Rollout Plan

1. Implement Tasks 1-4 first for single-agent benchmark stability.
2. Run focused tests and one tiny smoke benchmark.
3. Implement Task 5 preexecution as conservative trace-only behavior first; do not make it answer-changing until traces are stable.
4. Implement Task 6 compliance diagnostics.
5. Implement Task 7 for ChemQA after single-agent behavior is verified.
6. Update `GLOBAL_DEV_SPEC.md`.
7. Run the same temporary dataset again and compare:
   - prompt/system prompt length
   - selected skills per record
   - actual skill trace count
   - `skill_noncompliance_count`
   - score/RPF and elapsed time against `temp-benchmark-20260507-172807`

## Risk Notes

- Per-record config files increase runtime-config file count. This is acceptable for benchmark runs and keeps enforcement simple.
- Trigger matching remains heuristic. Keep `max_selected_skills` low and prefer no route over broad noisy route.
- Some skills do not have deterministic adapters. They should be selected for availability and prompt contract only; trace compliance can be satisfied by explicit `SKILL TRACE: skipped ...` until wrappers exist.
- Preexecution should start trace-only. Do not feed large tool outputs back into prompt until output summarization and size caps are tested.
- Treat `skill_noncompliance` as degraded execution metadata, not an automatic scoring failure, so quality metrics remain comparable.

## Self-Review

- Spec coverage: The plan addresses benchmark findings, full catalog noise, lack of true skill use, per-record config scoping, prompt reduction, deterministic providers, trace metadata, and compliance reporting.
- Placeholder scan: No task uses `TBD`, open-ended "add tests", or unspecified implementation steps.
- Type consistency: `SkillPlan`, `build_skill_plan`, `selected_skills`, `runner_skill_override`, `config_for_record`, `skill_executions`, and `skill_noncompliance` are used consistently across tasks.
