#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import threading
import time
import uuid
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import yaml

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from benchmarking.runtime import bundles as _runtime_bundles
from benchmarking.runtime import cleanroom as _cleanroom
from benchmarking.workflow import orchestration as _orchestration
from benchmarking.analysis.launcher import analysis_paths, launch_automated_evaluation
from benchmarking.core.contracts import AnswerPayload, FailureInfo, RecoveryInfo, RunStatus, RunnerResult
from benchmarking.core.convergence import ConvergencePolicy
from benchmarking.core.datasets import (
    BenchmarkRecord,
    GradingSpec,
    RecordValidationError,
    classify_subset as classify_record_subset,
    dataset_name_from_file as dataset_name_from_record_file,
    load_records as load_benchmark_records,
    source_pair_key as record_source_pair_key,
)
from benchmarking.core.experiments import ExperimentSpec
from benchmarking.core.reporting import (
    GroupRecordResult as _SharedGroupRecordResult,
    aggregate_bucket,
    aggregate_results,
    average_optional_metric,
    build_error_group_record_result as _shared_build_error_group_record_result,
    materialize_group_failure_results as _shared_materialize_group_failure_results,
)
from benchmarking.core.status import (
    is_chemqa_success_status,
    is_chemqa_terminal_status,
    normalize_chemqa_run_status,
    normalize_run_status_value,
)
from benchmarking.dashboard.progress import ProgressWriter
from benchmarking.runtime.bundles import (
    RuntimeBundle,
    RuntimeBundleError,
    _superchem_asset_cache_relative_path,
    build_hle_question_markdown,
    build_superchem_question_markdown,
    ensure_runtime_bundle as _shared_ensure_runtime_bundle,
    superchem_image_paths,
)
from benchmarking.runtime.agent_workspace import (
    AttemptIdentity,
    AttemptOutcome,
    AttemptWorkspaceManager,
    ContaminationAudit,
    ProtectedRoot,
    WorkspaceIsolationError,
    default_workspace_templates,
)
from benchmarking.runtime.config_pool import (
    ConfigPool as _RuntimeConfigPool,
    RuntimeConfigContext,
    RuntimeConfigError,
    actual_slot_ids,
    build_run_scoped_config_payload as _build_run_scoped_config_payload,
    logical_slot_ids,
    slot_role_map,
)
from benchmarking.runtime.openclaw_env import build_openclaw_subprocess_env, proxy_environment_report
from benchmarking.runtime.session_isolation import (
    SessionIsolationError,
    inspect_postflight_session,
    merge_preflight_postflight_audit,
    reset_agent_main_session_if_stale,
)
from benchmarking.runtime.web_search_preflight import run_web_search_preflight
from benchmarking.scoring.evaluation import EvaluationRegistryError, evaluate_record, register_default_evaluators
from benchmarking.scoring.evaluators import (
    EvaluationError,
    EvaluationResult,
    build_execution_error_evaluation as _shared_build_execution_error_evaluation,
    evaluate_chembench_open_ended as _shared_evaluate_chembench_open_ended,
    evaluate_frontierscience_olympiad as _shared_evaluate_frontierscience_olympiad,
    evaluate_frontierscience_research as _shared_evaluate_frontierscience_research,
    evaluate_generic_semantic as _shared_evaluate_generic_semantic,
    evaluate_hle as _shared_evaluate_hle,
    evaluate_superchem_multiple_choice_rpf as _shared_evaluate_superchem_multiple_choice_rpf,
    extract_candidate_short_answer,
    extract_final_answer_line,
    last_nonempty_line,
    maybe_json_loads,
    normalize_answer_tracks,
    normalize_space,
    parse_frontierscience_research_rubric,
    parse_superchem_checkpoint_weight,
    parse_superchem_checkpoints,
    parse_superchem_option_answer as _shared_parse_superchem_option_answer,
    safe_json_extract as _shared_safe_json_extract,
    superchem_valid_options,
)
from benchmarking.scoring.verifier_grounded_runtime import (
    VerifierGroundedRuntimeError,
    load_public_sample_answers,
)
from benchmarking.skills.health import check_all_skill_health, summarize_skill_health
from benchmarking.skills.tree import benchmark_skill_allowlist, load_chemistry_skill_inventory
from benchmarking.workflow.prompts import build_chemqa_goal, build_single_llm_prompt, resolve_chemqa_answer_kind
from benchmarking.workflow.runners import ChemQARunner as _BenchmarkingChemQARunner
from benchmarking.workflow.runners import SingleLLMRunner as _BenchmarkingSingleLLMRunner
from benchmarking.workflow.runners import build_runner

_runner_factory = build_runner

import runtime_paths


DEFAULT_WORKSPACE = runtime_paths.project_root
DEFAULT_BENCHMARK_ROOT = runtime_paths.benchmarks_root
DEFAULT_CHEMQA_ROOT = runtime_paths.skills_root / "chemqa-review"
DEFAULT_BENCHMARK_CLEANROOM_ROOT = runtime_paths.skills_root / "benchmark-cleanroom"
DEFAULT_OPENCLAW_ENV_FILE = runtime_paths.openclaw_env
DEFAULT_OPENCLAW_CONFIG = runtime_paths.openclaw_config
DEFAULT_OUTPUT_DIR = runtime_paths.project_state_root / "benchmark-runs"
DEFAULT_SINGLE_AGENT = "benchmark-single-skills-off"
DEFAULT_SINGLE_AGENT_MODEL = "qwen3.5-plus"
DEFAULT_JUDGE_AGENT = "benchmark-judge"
DEFAULT_JUDGE_MODEL = "openai/gpt-5.5"
DEFAULT_CHEMQA_PRESET = "chemqa-review@1"
DEFAULT_CHEMQA_MODEL_PROFILE = "chemqa-review-su8-coord-qwen-ds-kimi-glm-minimax"
THINKING_LEVEL_CHOICES = ("off", "minimal", "low", "medium", "high", "xhigh")
DEFAULT_SINGLE_AGENT_THINKING = "high"
DEFAULT_JUDGE_AGENT_THINKING = "high"
BENCHMARK_SKILLS_ALLOWLIST = list(benchmark_skill_allowlist())
CHEMQA_SLOT_SETS = {
    "chemqa_skills_on": "A",
}
BASELINE_AGENT_IDS = {
    "single_llm_skills_on": "benchmark-single-skills-on",
    "single_llm_skills_off": "benchmark-single-skills-off",
}
JUDGE_AGENT_ID = "benchmark-judge"


def RunOutput(
    *,
    short_answer_text: str,
    full_response_text: str,
    raw: dict[str, Any],
    runner_meta: dict[str, Any],
) -> RunnerResult:
    return RunnerResult(
        status=RunStatus.COMPLETED,
        answer=AnswerPayload(
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
        ),
        raw=raw,
        runner_meta=runner_meta,
    )


def current_python() -> str:
    venv = os.environ.get("VIRTUAL_ENV", "").strip()
    if venv:
        venv_root = Path(venv).expanduser()
        for candidate in (venv_root / "bin" / "python", venv_root / "Scripts" / "python.exe"):
            if candidate.is_file():
                return str(candidate)
    return str(Path(sys.executable).expanduser())
SUBSET_ORDER = (
    "chembench",
    "frontierscience_Olympiad",
    "frontierscience_Research",
    "superchem_multimodal",
    "hle_chemistry",
)
SUPERCHEM_SUBSETS = ("superchem_multimodal",)


class BenchmarkError(RuntimeError):
    pass


class CleanupFatalError(BenchmarkError):
    pass


@dataclass(frozen=True)
class ExperimentGroup:
    id: str
    label: str
    runner: str
    websearch: bool
    skills_enabled: bool = True


EXPERIMENT_GROUPS: dict[str, ExperimentGroup] = {
    "single_llm_skills_on": ExperimentGroup(
        id="single_llm_skills_on",
        label="单一 LLM + benchmark skills allowlist",
        runner="single_llm",
        websearch=False,
        skills_enabled=True,
    ),
    "single_llm_skills_off": ExperimentGroup(
        id="single_llm_skills_off",
        label="单一 LLM + 禁用 skills",
        runner="single_llm",
        websearch=False,
        skills_enabled=False,
    ),
    "chemqa_skills_on": ExperimentGroup(
        id="chemqa_skills_on",
        label="ChemQA fixed-lane review + benchmark skills allowlist",
        runner="chemqa",
        websearch=False,
        skills_enabled=True,
    ),
}
EXPERIMENT_SPECS: dict[str, ExperimentSpec] = {
    "single_llm_skills_on": ExperimentSpec(
        id="single_llm_skills_on",
        label=EXPERIMENT_GROUPS["single_llm_skills_on"].label,
        runner_kind="single_llm",
        websearch_enabled=False,
        skills_enabled=True,
        single_agent_id=BASELINE_AGENT_IDS["single_llm_skills_on"],
        skill_allowlist=tuple(BENCHMARK_SKILLS_ALLOWLIST),
    ),
    "single_llm_skills_off": ExperimentSpec(
        id="single_llm_skills_off",
        label=EXPERIMENT_GROUPS["single_llm_skills_off"].label,
        runner_kind="single_llm",
        websearch_enabled=False,
        skills_enabled=False,
        single_agent_id=BASELINE_AGENT_IDS["single_llm_skills_off"],
        skill_allowlist=(),
    ),
    "chemqa_skills_on": ExperimentSpec(
        id="chemqa_skills_on",
        label=EXPERIMENT_GROUPS["chemqa_skills_on"].label,
        runner_kind="chemqa",
        websearch_enabled=False,
        skills_enabled=True,
        slot_set=CHEMQA_SLOT_SETS["chemqa_skills_on"],
        skill_allowlist=tuple(BENCHMARK_SKILLS_ALLOWLIST),
    ),
}


def build_effective_experiment_specs(
    specs: dict[str, ExperimentSpec],
    *,
    skill_health_reports: dict[str, dict[str, Any]],
) -> dict[str, ExperimentSpec]:
    available = {skill for skill, report in skill_health_reports.items() if report.get("available") is True}
    effective: dict[str, ExperimentSpec] = {}
    for group_id, spec in specs.items():
        if spec.skills_enabled and spec.skill_allowlist:
            filtered = tuple(skill for skill in spec.skill_allowlist if skill in available)
            effective[group_id] = replace(spec, skill_allowlist=filtered)
        else:
            effective[group_id] = spec
    return effective


GroupRecordResult = _SharedGroupRecordResult


def format_timestamp(epoch: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch or time.time()))


def cleanroom_skill_root() -> Path:
    return DEFAULT_BENCHMARK_CLEANROOM_ROOT


def cleanroom_runtime_lease_module_path() -> Path:
    return cleanroom_skill_root() / "scripts" / "runtime_lease.py"


def load_cleanroom_runtime_lease_module() -> Any:
    try:
        return _cleanroom.load_cleanroom_runtime_lease_module(cleanroom_runtime_lease_module_path())
    except _cleanroom.CleanroomError as exc:
        raise BenchmarkError(str(exc)) from exc


try:
    cleanroom_runtime_lease = load_cleanroom_runtime_lease_module()
except Exception:
    cleanroom_runtime_lease = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run three-group skills benchmark experiments.")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT), help="formal-benchmarks/ 根目录")
    parser.add_argument("--chemqa-root", default=str(DEFAULT_CHEMQA_ROOT), help="chemqa-review skill 根目录")
    parser.add_argument("--openclaw-config", default=str(DEFAULT_OPENCLAW_CONFIG), help="基础 OpenClaw 配置文件")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="结果输出目录")
    parser.add_argument(
        "--exact-output-dir",
        help="若提供，则直接把该目录作为本次输出根目录，而不是自动创建 benchmark-时间戳 子目录",
    )
    parser.add_argument(
        "--merge-existing-per-record",
        action="store_true",
        help="聚合结果时合并输出目录中已存在的 per-record 结果，适合断点续跑/部分重跑",
    )
    parser.add_argument(
        "--groups",
        default=",".join(EXPERIMENT_GROUPS.keys()),
        help="要运行的实验组，逗号分隔。默认三组全跑",
    )
    parser.add_argument(
        "--datasets",
        help="仅运行指定数据集，逗号分隔；默认扫描 formal-benchmarks/*/data/*.jsonl",
    )
    parser.add_argument(
        "--subsets",
        help=(
            "仅运行指定子集，逗号分隔；例如 "
            "frontierscience_Research,superchem_multimodal"
        ),
    )
    parser.add_argument(
        "--random-count-per-subset",
        type=int,
        help=(
            "按子集随机抽样时，每个子集抽取多少题；当前支持 chembench / "
            "frontierscience_Olympiad / frontierscience_Research / "
            "superchem_multimodal"
        ),
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=0,
        help="随机抽样的 seed，默认 0，便于复现",
    )
    parser.add_argument(
        "--files",
        help="仅运行指定 jsonl 文件，逗号分隔，优先级高于 --datasets",
    )
    parser.add_argument("--limit", type=int, help="最多运行多少条题目")
    parser.add_argument("--offset", type=int, default=0, help="跳过前多少条题目")
    parser.add_argument(
        "--record-ids",
        help="仅运行指定 record/task id，逗号分隔；按给定顺序运行",
    )
    parser.add_argument(
        "--single-agent-id-override",
        help="覆盖 single_llm 组的 agent id；未提供时按实验组规范使用默认 baseline agent",
    )
    parser.add_argument(
        "--single-agent-model",
        default=DEFAULT_SINGLE_AGENT_MODEL,
        help="单一 LLM baseline runtime model，默认锁定为 qwen3.5-plus",
    )
    parser.add_argument(
        "--single-agent-thinking",
        default=DEFAULT_SINGLE_AGENT_THINKING,
        choices=THINKING_LEVEL_CHOICES,
        help="单一 LLM baseline OpenClaw thinking level，默认 high",
    )
    parser.add_argument(
        "--chemqa-model-profile",
        default=DEFAULT_CHEMQA_MODEL_PROFILE,
        help="ChemQA fixed-lane review 所用 model profile，默认使用当前 benchmark 固定 profile",
    )
    parser.add_argument("--judge-agent", default=DEFAULT_JUDGE_AGENT, help="rubric / 语义评测所用 judge agent id")
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help="judge runtime model，默认锁定为 openai/gpt-5.5",
    )
    parser.add_argument(
        "--judge-agent-thinking",
        default=DEFAULT_JUDGE_AGENT_THINKING,
        choices=THINKING_LEVEL_CHOICES,
        help="judge OpenClaw thinking level，默认 high",
    )
    parser.add_argument("--single-timeout", type=int, default=900, help="单一 LLM 每题超时秒数")
    parser.add_argument(
        "--single-timeout-retries",
        type=int,
        default=3,
        help="单一 LLM timeout-family 失败后的 fresh-session 最大重试次数，默认 3",
    )
    parser.add_argument(
        "--no-timeout",
        action="store_true",
        help="取消单题作答时间上限，让模型自由探索，但保留进程级兜底安全阀",
    )
    parser.add_argument(
        "--no-analysis",
        action="store_true",
        help="本轮 benchmark run 结束后不启动自动化评估流程，适合临时测试型 run",
    )
    parser.add_argument(
        "--single-timeout-retry-backoff-seconds",
        default="5,15,45",
        help="单一 LLM timeout 重试前等待秒数，逗号分隔，默认 5,15,45",
    )
    parser.add_argument("--chemqa-timeout", type=int, default=1800, help="ChemQA fixed-lane review 每题超时秒数")
    parser.add_argument("--judge-timeout", type=int, default=300, help="Judge 每次评测超时秒数")
    parser.add_argument(
        "--max-unchanged-status-polls",
        type=int,
        default=2,
        help="ChemQA convergence limit for unchanged status polls",
    )
    parser.add_argument(
        "--max-recovery-attempts",
        type=int,
        default=2,
        help="ChemQA convergence limit for recovery attempts",
    )
    parser.add_argument(
        "--max-concurrent-groups",
        type=int,
        default=2,
        help="最多同时运行多少个实验组；默认 2，以降低 WSL 峰值资源占用",
    )
    parser.add_argument(
        "--inter-wave-delay-seconds",
        type=int,
        default=10,
        help="相邻波次之间的等待秒数，默认 10，用于给系统释放资源的窗口",
    )
    parser.add_argument("--review-rounds", type=int, help="ChemQA review rounds 覆盖值")
    parser.add_argument("--rebuttal-rounds", type=int, help="ChemQA rebuttal rounds 覆盖值")
    parser.add_argument("--list-datasets", action="store_true", help="列出可发现的数据集文件后退出")
    parser.add_argument(
        "--print-selected-records",
        action="store_true",
        help="打印本次实际选中的题目清单后退出",
    )
    return parser.parse_args()


def require_cleanroom_runtime_lease() -> Any:
    try:
        return _cleanroom.require_cleanroom_runtime_lease(
            cleanroom_runtime_lease,
            cleanroom_root=cleanroom_skill_root(),
        )
    except _cleanroom.CleanroomError as exc:
        raise BenchmarkError(str(exc)) from exc


def cleanup_manifest_path(output_root: Path, run_id: str) -> Path:
    try:
        return _cleanroom.cleanup_manifest_path(
            output_root,
            run_id,
            cleanroom_runtime_lease=cleanroom_runtime_lease,
            cleanroom_root=cleanroom_skill_root(),
        )
    except _cleanroom.CleanroomError as exc:
        raise BenchmarkError(str(exc)) from exc


def build_cleanup_manifest_payload(
    *,
    run_id: str,
    benchmark_kind: str,
    group_id: str,
    output_root: Path,
    launch_home: Path | None = None,
    clawteam_data_dir: Path | None = None,
    session_assignments: dict[str, str] | None = None,
    control_roots: list[Path] | None = None,
    generated_roots: list[Path] | None = None,
    artifact_roots: list[Path] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return _cleanroom.build_cleanup_manifest_payload(
            run_id=run_id,
            benchmark_kind=benchmark_kind,
            group_id=group_id,
            output_root=output_root,
            cleanroom_runtime_lease=cleanroom_runtime_lease,
            cleanroom_root=cleanroom_skill_root(),
            launch_home=launch_home,
            clawteam_data_dir=clawteam_data_dir,
            session_assignments=session_assignments,
            control_roots=control_roots,
            generated_roots=generated_roots,
            artifact_roots=artifact_roots,
            extra=extra,
        )
    except _cleanroom.CleanroomError as exc:
        raise BenchmarkError(str(exc)) from exc


def write_cleanup_manifest(path: Path, payload: dict[str, Any]) -> Path:
    try:
        return _cleanroom.write_cleanup_manifest(
            path,
            payload,
            cleanroom_runtime_lease=cleanroom_runtime_lease,
            cleanroom_root=cleanroom_skill_root(),
        )
    except _cleanroom.CleanroomError as exc:
        raise BenchmarkError(str(exc)) from exc


def update_cleanup_manifest(path: Path, patch: dict[str, Any]) -> dict[str, Any]:
    try:
        return _cleanroom.update_cleanup_manifest(
            path,
            patch,
            cleanroom_runtime_lease=cleanroom_runtime_lease,
            cleanroom_root=cleanroom_skill_root(),
        )
    except _cleanroom.CleanroomError as exc:
        raise BenchmarkError(str(exc)) from exc


def register_pending_cleanup_manifest(path: Path) -> None:
    _cleanroom.register_pending_cleanup_manifest(path, cleanup_callback=run_pending_cleanroom_cleanup)


def unregister_pending_cleanup_manifest(path: Path) -> None:
    _cleanroom.unregister_pending_cleanup_manifest(path)


def iter_pending_cleanup_manifests() -> list[Path]:
    return _cleanroom.iter_pending_cleanup_manifests()


def invoke_cleanroom_cleanup(
    *,
    manifest_path: Path,
    grace_seconds: float = 5.0,
    kill_after_seconds: float = 10.0,
) -> dict[str, Any]:
    try:
        return _cleanroom.invoke_cleanroom_cleanup(
            manifest_path=manifest_path,
            cleanroom_root=cleanroom_skill_root(),
            current_python=current_python,
            run_subprocess=run_subprocess,
            grace_seconds=grace_seconds,
            kill_after_seconds=kill_after_seconds,
        )
    except _cleanroom.CleanroomError as exc:
        raise BenchmarkError(str(exc)) from exc


def run_pending_cleanroom_cleanup() -> list[dict[str, Any]]:
    return _cleanroom.run_pending_cleanroom_cleanup(
        invoke_cleanup=lambda manifest_path: invoke_cleanroom_cleanup(manifest_path=manifest_path),
    )


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def slugify(value: str, *, limit: int = 64) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    cleaned = cleaned or "item"
    if len(cleaned) <= limit:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned[: limit - 9]}-{digest}".strip("-")


def runtime_config_context(experiment_specs: dict[str, ExperimentSpec] | None = None) -> RuntimeConfigContext:
    return RuntimeConfigContext(
        agents_root=runtime_paths.agents_root,
        judge_agent_id=JUDGE_AGENT_ID,
        chemqa_slot_sets=CHEMQA_SLOT_SETS,
        experiment_specs=experiment_specs or EXPERIMENT_SPECS,
        benchmark_skills_root=runtime_paths.skills_root,
    )


def build_production_protected_roots(*, runtime_root: Path, output_root: Path) -> tuple[ProtectedRoot, ...]:
    roots = [
        ProtectedRoot("benchmark_dataset_root", runtime_paths.benchmarks_root, "runtime_paths.benchmarks_root"),
        ProtectedRoot(
            "temp_benchmark_dataset_root",
            runtime_paths.temp_benchmarks_root,
            "runtime_paths.temp_benchmarks_root",
        ),
        ProtectedRoot(
            "verifier_release_root",
            runtime_paths.data_root / "verifier-grounded-releases",
            'runtime_paths.data_root / "verifier-grounded-releases"',
        ),
        ProtectedRoot(
            "verifier_runtime_root",
            runtime_paths.project_state_root / "verifier-grounded-runtimes",
            'runtime_paths.project_state_root / "verifier-grounded-runtimes"',
        ),
        ProtectedRoot(
            "verifier_resource_root",
            runtime_paths.project_root / "benchmarking" / "resources" / "verifier_grounded",
            'runtime_paths.project_root / "benchmarking/resources/verifier_grounded"',
        ),
        ProtectedRoot("benchmark_runtime_root", runtime_root, "AttemptWorkspaceManager.runtime_root"),
        ProtectedRoot(
            "benchmark_results_root",
            runtime_paths.project_state_root / "benchmark-runs",
            'runtime_paths.project_state_root / "benchmark-runs"',
        ),
        ProtectedRoot("current_output_root", output_root, "AttemptWorkspaceManager.output_root"),
        ProtectedRoot("agents_root", runtime_paths.agents_root, "runtime_paths.agents_root"),
    ]
    roots.extend(
        ProtectedRoot(
            "legacy_benchmark_workspace",
            runtime_paths.benchmark_runtime_root / name,
            f"runtime_paths.benchmark_runtime_root / {name}",
        )
        for name in (
            "benchmark-single-skills-on",
            "benchmark-single-skills-off",
            "benchmark-judge",
            "custom-single-agent",
        )
    )
    return tuple(roots)


def _compatibility_protected_roots(*, runtime_root: Path, output_root: Path) -> tuple[ProtectedRoot, ...]:
    return (
        ProtectedRoot("benchmark_runtime_root", runtime_root, "compatibility.runtime_root"),
        ProtectedRoot("current_output_root", output_root, "compatibility.output_root"),
    )


def build_run_scoped_config_payload(
    base_payload: dict[str, Any],
    *,
    group: ExperimentGroup,
    single_agent_model: str,
    judge_model: str,
    workspace_manager: AttemptWorkspaceManager | None = None,
    single_agent_id_override: str | None = None,
) -> dict[str, Any]:
    workspace_manager = workspace_manager or AttemptWorkspaceManager(
        runtime_root=runtime_paths.benchmark_runtime_root / "runs",
        output_root=runtime_paths.project_state_root / "benchmark-config-preview",
        run_id="config-preview",
        invocation_id="config-preview",
        templates=default_workspace_templates(runtime_paths.project_root),
        protected_roots=_compatibility_protected_roots(
            runtime_root=runtime_paths.benchmark_runtime_root / "runs",
            output_root=runtime_paths.project_state_root / "benchmark-config-preview",
        ),
    )
    try:
        return _build_run_scoped_config_payload(
            base_payload,
            context=runtime_config_context(),
            group=group,
            single_agent_model=single_agent_model,
            judge_model=judge_model,
            workspace_manager=workspace_manager,
            single_agent_id_override=single_agent_id_override,
        )
    except RuntimeConfigError as exc:
        raise BenchmarkError(str(exc)) from exc


def run_subprocess(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=env,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def ensure_success(result: subprocess.CompletedProcess[str], command: list[str]) -> None:
    if result.returncode != 0:
        raise BenchmarkError(
            "Command failed\n"
            f"command: {' '.join(command)}\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def parse_json_stdout(result: subprocess.CompletedProcess[str], command: list[str]) -> Any:
    ensure_success(result, command)
    output = result.stdout.strip() or result.stderr.strip()
    if not output:
        raise BenchmarkError(f"Empty stdout/stderr from command: {' '.join(command)}")
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        try:
            return safe_json_extract(output)
        except Exception as exc:
            raise BenchmarkError(
                "JSON decode failed\n"
                f"command: {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ) from exc


def deep_copy_jsonish(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def unwrap_agent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        return result
    return payload if isinstance(payload, dict) else {}


def build_temp_openclaw_config_payload(base_payload: dict[str, Any], *, enable_websearch: bool) -> dict[str, Any]:
    payload = deep_copy_jsonish(base_payload)
    tools = payload.setdefault("tools", {})
    web = tools.setdefault("web", {})
    search = web.setdefault("search", {})
    search["enabled"] = enable_websearch
    fetch = web.setdefault("fetch", {})
    fetch["enabled"] = enable_websearch

    plugins = payload.setdefault("plugins", {})
    entries = plugins.setdefault("entries", {})
    duckduckgo = entries.setdefault("duckduckgo", {})
    duckduckgo["enabled"] = enable_websearch
    duckduckgo.setdefault("config", {})
    return payload


class ConfigPool(_RuntimeConfigPool):
    def __init__(
        self,
        *,
        base_config_path: Path,
        output_root: Path,
        run_id: str,
        invocation_id: str,
        workspace_manager: AttemptWorkspaceManager,
        single_agent_model: str | None = None,
        judge_model: str | None = None,
        single_agent_id_override: str | None = None,
        experiment_specs: dict[str, ExperimentSpec] | None = None,
    ) -> None:
        super().__init__(
            base_config_path=base_config_path,
            output_root=output_root,
            context=runtime_config_context(experiment_specs=experiment_specs),
            run_id=run_id,
            invocation_id=invocation_id,
            workspace_manager=workspace_manager,
            single_agent_model=single_agent_model,
            judge_model=judge_model,
            single_agent_id_override=single_agent_id_override,
        )


def discover_dataset_files(root: Path) -> list[Path]:
    return sorted(path.resolve() for path in root.glob("*/data/*.jsonl") if path.is_file())


def dataset_name_from_file(path: Path) -> str:
    return dataset_name_from_record_file(path)


def load_records(paths: Iterable[Path]) -> list[BenchmarkRecord]:
    try:
        return load_benchmark_records(paths)
    except RecordValidationError as exc:
        raise BenchmarkError(str(exc)) from exc


def classify_subset(record: BenchmarkRecord) -> str:
    return classify_record_subset(record)


def source_pair_key(record: BenchmarkRecord) -> str:
    return record_source_pair_key(record)


def sample_superchem_pairs(
    grouped: dict[str, list[BenchmarkRecord]],
    *,
    per_subset_count: int,
    seed: int,
) -> list[BenchmarkRecord]:
    if not all(grouped.get(subset) for subset in SUPERCHEM_SUBSETS):
        return []

    by_uuid: dict[str, dict[str, BenchmarkRecord]] = {}
    for subset in SUPERCHEM_SUBSETS:
        for record in grouped.get(subset, []):
            by_uuid.setdefault(source_pair_key(record), {})[subset] = record

    paired = [pair for pair in by_uuid.values() if all(subset in pair for subset in SUPERCHEM_SUBSETS)]
    if not paired:
        return []
    if len(paired) < per_subset_count:
        raise BenchmarkError(f"SUPERChem 成对题目仅有 {len(paired)} 题，无法随机抽取 {per_subset_count} 题。")

    rng = random.Random(seed)
    sampled_pairs = rng.sample(paired, per_subset_count)
    sampled: list[BenchmarkRecord] = []
    for pair in sampled_pairs:
        for subset in SUPERCHEM_SUBSETS:
            sampled.append(pair[subset])
    return sampled



def sample_records_per_subset(records: list[BenchmarkRecord], *, per_subset_count: int, seed: int) -> list[BenchmarkRecord]:
    if per_subset_count <= 0:
        raise BenchmarkError("--random-count-per-subset 必须是正整数")

    grouped: dict[str, list[BenchmarkRecord]] = {}
    for record in records:
        grouped.setdefault(classify_subset(record), []).append(record)

    available_supported = [subset for subset in SUBSET_ORDER if grouped.get(subset)]
    if not available_supported:
        raise BenchmarkError("当前选定的数据范围内没有可用于按子集抽样的记录。")

    rng = random.Random(seed)
    sampled: list[BenchmarkRecord] = []
    handled_subsets: set[str] = set()
    superchem_sampled = sample_superchem_pairs(grouped, per_subset_count=per_subset_count, seed=seed)
    if superchem_sampled:
        sampled.extend(superchem_sampled)
        handled_subsets.update(SUPERCHEM_SUBSETS)
    for subset in available_supported:
        if subset in handled_subsets:
            continue
        subset_records = grouped[subset]
        if len(subset_records) < per_subset_count:
            raise BenchmarkError(
                f"子集 `{subset}` 仅有 {len(subset_records)} 题，无法随机抽取 {per_subset_count} 题。"
            )
        sampled.extend(rng.sample(subset_records, per_subset_count))
    return sampled



def apply_offset_limit(records: list[BenchmarkRecord], *, offset: int = 0, limit: int | None = None) -> list[BenchmarkRecord]:
    if offset < 0:
        raise BenchmarkError("--offset 不能为负数")
    sliced = records[offset:]
    if limit is not None:
        if limit < 0:
            raise BenchmarkError("--limit 不能为负数")
        sliced = sliced[:limit]
    return sliced


def safe_json_extract(text: str) -> Any:
    try:
        return _shared_safe_json_extract(text)
    except EvaluationError as exc:
        raise BenchmarkError(str(exc)) from exc


def parse_superchem_option_answer(text: str, *, valid_options: Iterable[str]) -> str:
    try:
        return _shared_parse_superchem_option_answer(text, valid_options=valid_options)
    except EvaluationError as exc:
        raise BenchmarkError(str(exc)) from exc


def _resolve_local_image_path(raw_path: str) -> Path | None:
    return _runtime_bundles._resolve_local_image_path(raw_path)


def _resolve_record_local_image_path(record: BenchmarkRecord, raw_path: str) -> Path | None:
    return _runtime_bundles._resolve_record_local_image_path(record, raw_path)


def _resolve_record_relative_image_path(record: BenchmarkRecord, raw_path: str) -> Path | None:
    return _runtime_bundles._resolve_record_relative_image_path(record, raw_path)


SUPERCHEM_MEDIA_UPLOAD_URL_RE = _runtime_bundles.SUPERCHEM_MEDIA_UPLOAD_URL_RE
SUPERCHEM_MARKDOWN_IMAGE_URL_RE = _runtime_bundles.SUPERCHEM_MARKDOWN_IMAGE_URL_RE
SUPERCHEM_ASSET_BASE_URL = _runtime_bundles.SUPERCHEM_ASSET_BASE_URL
SUPERCHEM_LEGACY_FALLBACK_MAX_IMAGES = _runtime_bundles.SUPERCHEM_LEGACY_FALLBACK_MAX_IMAGES


def _dedupe_text_preserve_order(values: Iterable[str]) -> list[str]:
    return _runtime_bundles._dedupe_text_preserve_order(values)


def _extract_superchem_media_locators(text: Any) -> list[str]:
    return _runtime_bundles._extract_superchem_media_locators(text)


def _superchem_absolutize_media_locator(locator: str) -> str:
    return _runtime_bundles._superchem_absolutize_media_locator(locator)


def _superchem_infer_extension(locator: str) -> str:
    return _runtime_bundles._superchem_infer_extension(locator)


def _superchem_asset_cache_relative_path(locator: str) -> Path:
    return _runtime_bundles._superchem_asset_cache_relative_path(locator)


def _superchem_path_matches_locator(raw_path: str, locator: str) -> bool:
    return _runtime_bundles._superchem_path_matches_locator(raw_path, locator)


def _superchem_payload_image_path_items(record: BenchmarkRecord, *, include_explanation: bool = False) -> list[str]:
    return _runtime_bundles._superchem_payload_image_path_items(record, include_explanation=include_explanation)


def _superchem_visible_image_locators(record: BenchmarkRecord) -> list[str]:
    return _runtime_bundles._superchem_visible_image_locators(record)


def _superchem_find_payload_path_for_locator(record: BenchmarkRecord, locator: str) -> str | None:
    return _runtime_bundles._superchem_find_payload_path_for_locator(record, locator)


def _superchem_legacy_runtime_image_path_items(record: BenchmarkRecord) -> list[str]:
    return _runtime_bundles._superchem_legacy_runtime_image_path_items(record)


def _superchem_image_path_items(record: BenchmarkRecord) -> list[str]:
    return _runtime_bundles._superchem_image_path_items(record)


def superchem_image_paths(record: BenchmarkRecord) -> list[Path]:
    return _runtime_bundles.superchem_image_paths(record)


def _rewrite_superchem_media_locators(text: str, image_rewrites: dict[str, str]) -> str:
    return _runtime_bundles._rewrite_superchem_media_locators(text, image_rewrites)


def build_superchem_question_markdown(
    record: BenchmarkRecord,
    *,
    image_relpaths: list[str],
    image_rewrites: dict[str, str] | None = None,
) -> str:
    return _runtime_bundles.build_superchem_question_markdown(
        record,
        image_relpaths=image_relpaths,
        image_rewrites=image_rewrites,
    )


DATA_URI_IMAGE_RE = _runtime_bundles.DATA_URI_IMAGE_RE


def _extension_from_image_mime(mime_type: str) -> str:
    return _runtime_bundles._extension_from_image_mime(mime_type)


def build_hle_question_markdown(record: BenchmarkRecord, *, image_relpaths: list[str]) -> str:
    return _runtime_bundles.build_hle_question_markdown(record, image_relpaths=image_relpaths)


def ensure_runtime_bundle(record: BenchmarkRecord, *, bundle_root: Path) -> RuntimeBundle | None:
    try:
        return _shared_ensure_runtime_bundle(record, bundle_root=bundle_root)
    except RuntimeBundleError as exc:
        raise BenchmarkError(str(exc)) from exc


def summarize_payloads(payloads: list[dict[str, Any]]) -> str:
    texts = [str(item.get("text") or "").strip() for item in payloads if str(item.get("text") or "").strip()]
    return "\n\n".join(texts).strip()


def normalize_answer_tracks(*, short_answer_text: str = "", full_response_text: str = "") -> tuple[str, str]:
    short_text = str(short_answer_text or "").strip()
    full_text = str(full_response_text or "").strip()
    if not short_text and full_text:
        short_text = extract_candidate_short_answer(full_text)
    if not full_text and short_text:
        full_text = f"FINAL ANSWER: {short_text}"
    return short_text, full_text


def render_chemqa_submission_rationale(final_submission: dict[str, Any], *, final_answer_text: str = "") -> str:
    parts: list[str] = []
    summary = normalize_space(str(final_submission.get("summary") or ""))
    if summary:
        parts.extend(["Summary:", summary])

    submission_trace = list(final_submission.get("submission_trace") or [])
    if submission_trace:
        parts.append("")
        parts.append("Reasoning / submission trace:")
        for item in submission_trace:
            if not isinstance(item, dict):
                continue
            step = normalize_space(str(item.get("step") or item.get("phase") or "reasoning"))
            detail = normalize_space(str(item.get("detail") or item.get("summary") or item.get("finding") or ""))
            status = normalize_space(str(item.get("status") or ""))
            bullet = f"- {step}"
            if status:
                bullet += f" [{status}]"
            if detail:
                bullet += f": {detail}"
            parts.append(bullet)

    claim_anchors = list(final_submission.get("claim_anchors") or [])
    if claim_anchors:
        parts.append("")
        parts.append("Claim anchors:")
        for item in claim_anchors:
            if not isinstance(item, dict):
                continue
            claim = normalize_space(str(item.get("claim") or ""))
            anchor = normalize_space(str(item.get("anchor") or ""))
            if claim:
                parts.append(f"- {anchor + ': ' if anchor else ''}{claim}")

    evidence_limits = list(final_submission.get("evidence_limits") or [])
    if evidence_limits:
        parts.append("")
        parts.append("Evidence limits:")
        for item in evidence_limits:
            text = normalize_space(str(item or ""))
            if text:
                parts.append(f"- {text}")

    final_answer = normalize_space(final_answer_text or str(final_submission.get("direct_answer") or ""))
    if final_answer:
        parts.append("")
        parts.append(f"FINAL ANSWER: {final_answer}")

    return "\n".join(part for part in parts if part is not None).strip()


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}



def build_chemqa_response_from_submission(*, final_submission: dict[str, Any], final_answer_text: str = "") -> tuple[str, str]:
    short_answer_text = normalize_space(final_answer_text or str(final_submission.get("direct_answer") or ""))
    full_response_text = render_chemqa_submission_rationale(final_submission, final_answer_text=short_answer_text)
    return normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text=full_response_text)


def extract_chemqa_scoreable_answer(value: Any) -> str:
    if isinstance(value, str):
        stripped = normalize_space(value)
        if not stripped:
            return ""
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except Exception:
                return stripped
            return extract_chemqa_scoreable_answer(parsed)
        return stripped
    if isinstance(value, dict):
        for key in ("direct_answer", "answer", "value", "final_answer"):
            candidate = extract_chemqa_scoreable_answer(value.get(key))
            if candidate:
                return candidate
        return ""
    return ""


def build_chemqa_full_response(*, qa_result: dict[str, Any]) -> tuple[str, str]:
    artifact_paths = dict(qa_result.get("artifact_paths") or {})
    final_answer_artifact_path = str(artifact_paths.get("final_answer_artifact") or "").strip()
    if final_answer_artifact_path:
        path = Path(final_answer_artifact_path)
        if path.is_file():
            try:
                final_artifact = json.loads(path.read_text(encoding="utf-8"))
                short_answer_text = extract_chemqa_scoreable_answer(final_artifact.get("evaluator_answer"))
                full_response_text = str(final_artifact.get("full_answer") or final_artifact.get("display_answer") or "").strip()
                return normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text=full_response_text)
            except Exception:
                pass
    short_answer_text = extract_chemqa_scoreable_answer(qa_result.get("final_answer"))
    final_submission_path = str(artifact_paths.get("final_submission") or "").strip()
    if final_submission_path:
        path = Path(final_submission_path)
        if path.is_file():
            try:
                final_submission = json.loads(path.read_text(encoding="utf-8"))
                return build_chemqa_response_from_submission(final_submission=final_submission, final_answer_text=short_answer_text)
            except Exception:
                pass
    final_answer_path = str(artifact_paths.get("final_answer") or "").strip()
    if final_answer_path:
        path = Path(final_answer_path)
        if path.is_file():
            fallback_text = path.read_text(encoding="utf-8").strip()
            if not short_answer_text:
                return "", fallback_text
            return normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text=fallback_text)
    if not short_answer_text:
        return "", ""
    return normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text="")


class JudgeClient:
    def __init__(
        self,
        *,
        judge_agent: str,
        timeout_seconds: int,
        config_path: Path,
        thinking: str = DEFAULT_JUDGE_AGENT_THINKING,
        workspace_manager: AttemptWorkspaceManager | None = None,
        contamination_auditor=None,
    ) -> None:
        self.judge_agent = judge_agent
        self.timeout_seconds = timeout_seconds
        self.config_path = config_path
        self.thinking = thinking
        self._lock = threading.Lock()
        compatibility_manager = workspace_manager is None
        if workspace_manager is None:
            workspace_manager = AttemptWorkspaceManager(
                runtime_root=config_path.expanduser().resolve().parent / ".benchmark-test-workspaces" / "runs",
                output_root=config_path.expanduser().resolve().parent / ".benchmark-test-output",
                run_id="judge-test",
                invocation_id=uuid.uuid4().hex,
                templates=default_workspace_templates(runtime_paths.project_root),
                protected_roots=_compatibility_protected_roots(
                    runtime_root=config_path.expanduser().resolve().parent / ".benchmark-test-workspaces" / "runs",
                    output_root=config_path.expanduser().resolve().parent / ".benchmark-test-output",
                ),
            )
        if compatibility_manager and contamination_auditor is None:
            contamination_auditor = lambda **_kwargs: ContaminationAudit(status="clean")
        self.workspace_manager = workspace_manager
        self._contamination_auditor = contamination_auditor
        self.last_workspace_isolation: dict[str, Any] = {}

    def evaluate_json(self, prompt: str) -> dict[str, Any]:
        session_id = f"benchmark-judge-{uuid.uuid4().hex[:12]}"
        identity = AttemptIdentity(
            run_id=self.workspace_manager.run_id,
            invocation_id=self.workspace_manager.invocation_id,
            group_id="benchmark-judge-runtime",
            runner_kind="judge",
            agent_id=self.judge_agent,
            record_id=f"judge-call-{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:12]}",
            attempt_index=0,
            session_id=session_id,
            template_id="judge-v1",
        )
        command = [
            "openclaw",
            "agent",
            "--local",
            "--agent",
            self.judge_agent,
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--thinking",
            self.thinking,
            "--timeout",
            str(self.timeout_seconds),
            "--json",
        ]
        with self._lock:
            try:
                lease = self.workspace_manager.prepare(identity)
            except WorkspaceIsolationError as exc:
                raise BenchmarkError(f"Judge workspace isolation failed: {exc.message}") from exc
            env = build_openclaw_subprocess_env(base_env=os.environ.copy(), config_path=self.config_path)
            env.update(
                {
                    "BENCHMARK_WORKSPACE_DIR": str(lease.active_workspace),
                    "BENCHMARK_SKILL_SCRATCH_DIR": str(lease.scratch_dir),
                    "BENCHMARK_SKILL_REQUEST_DIR": str(lease.request_dir),
                    "BENCHMARK_SKILL_OUTPUT_DIR": str(lease.output_dir),
                    "BENCHMARK_SKILL_NOTES_DIR": str(lease.notes_dir),
                }
            )
            outcome_status = "failed"
            contamination_audit = ContaminationAudit(
                status="unavailable",
                findings=({"rule_id": "judge_call_incomplete", "tool_name": "", "command_excerpt": ""},),
            )
            call_error: Exception | None = None
            try:
                preflight_audit = reset_agent_main_session_if_stale(
                    self.judge_agent,
                    session_id,
                    config_path=self.config_path,
                )
                result = run_subprocess(command, env=env, timeout=self.timeout_seconds + 30)
                postflight_audit = inspect_postflight_session(
                    self.judge_agent,
                    session_id,
                    config_path=self.config_path,
                )
                audit = merge_preflight_postflight_audit(preflight_audit, postflight_audit)
                if audit.get("session_isolation_ok") is not True:
                    requested_session = str(audit.get("requested_session_id") or session_id)
                    actual_session = str(audit.get("postflight_entry_session_id") or "")
                    raise BenchmarkError(
                        "Judge OpenClaw session isolation failed: "
                        f"requested `{requested_session}` but postflight entry pointed to `{actual_session}`."
                    )
                payload = parse_json_stdout(result, command)
                result_payload = unwrap_agent_payload(payload)
                reply = summarize_payloads(list((result_payload.get("payloads") or [])))
                parsed = safe_json_extract(reply)
                if not isinstance(parsed, dict):
                    raise BenchmarkError(f"Judge must return a JSON object, got: {reply}")
                if self._contamination_auditor is not None:
                    contamination_audit = self._contamination_auditor(
                        lease=lease,
                        runner_meta={"session_isolation": audit},
                        allowed_roots=[],
                        environment=env,
                    )
                else:
                    contamination_audit = self.workspace_manager.audit_attempt(
                        lease,
                        {"session_isolation": audit},
                        environment=env,
                    )
                if contamination_audit.status != "clean":
                    raise BenchmarkError(
                        "Judge workspace contamination detected."
                        if contamination_audit.status == "contaminated"
                        else "Judge workspace contamination audit unavailable."
                    )
                outcome_status = "completed"
            except SessionIsolationError as exc:
                call_error = BenchmarkError(f"Judge OpenClaw session isolation failed: {exc}")
            except Exception as exc:
                call_error = exc
            isolation_meta = lease.to_meta()
            isolation_meta.update(
                {
                    "contaminated": contamination_audit.status == "contaminated",
                    "audit_status": contamination_audit.status,
                    "findings": [dict(finding) for finding in contamination_audit.findings],
                }
            )
            try:
                archive = self.workspace_manager.seal(
                    lease,
                    AttemptOutcome(
                        runner_status=outcome_status,
                        archive_reason="attempt_terminal",
                        contamination_audit=contamination_audit,
                    ),
                )
            except WorkspaceIsolationError as exc:
                isolation_meta["archive_ok"] = False
                isolation_meta["archive_error"] = dict(exc.details)
                self.last_workspace_isolation = isolation_meta
                raise BenchmarkError(f"Judge workspace archive failed: {exc.message}") from exc
            isolation_meta.update(archive.to_meta())
            self.last_workspace_isolation = isolation_meta
            if call_error is not None:
                raise call_error
            return parsed


class SingleLLMRunner(_BenchmarkingSingleLLMRunner):
    def __init__(
        self,
        *,
        agent_id: str,
        timeout_seconds: int,
        config_path: Path,
        runtime_bundle_root: Path,
        configured_skills: tuple[str, ...] | list[str] = (),
        skill_health_summary: dict[str, Any] | None = None,
        convergence_policy: ConvergencePolicy | None = None,
        timeout_retries: int = 3,
        timeout_retry_backoff_seconds: tuple[int | float, ...] | list[int | float] = (5, 15, 45),
        sleep_fn=time.sleep,
        benchmark_agent_thinking: str = DEFAULT_SINGLE_AGENT_THINKING,
        no_timeout: bool = False,
        workspace_manager: AttemptWorkspaceManager | None = None,
        contamination_auditor=None,
    ) -> None:
        compatibility_manager = workspace_manager is None
        if workspace_manager is None:
            compatibility_root = runtime_bundle_root.expanduser().resolve()
            workspace_manager = AttemptWorkspaceManager(
                runtime_root=compatibility_root / ".benchmark-test-workspaces" / "runs",
                output_root=compatibility_root / ".benchmark-test-output",
                run_id="runner-test",
                invocation_id=uuid.uuid4().hex,
                templates=default_workspace_templates(runtime_paths.project_root),
                protected_roots=_compatibility_protected_roots(
                    runtime_root=compatibility_root / ".benchmark-test-workspaces" / "runs",
                    output_root=compatibility_root / ".benchmark-test-output",
                ),
            )
        if compatibility_manager and contamination_auditor is None:
            contamination_auditor = lambda **_kwargs: ContaminationAudit(status="clean")
        super().__init__(
            agent_id=agent_id,
            timeout_seconds=timeout_seconds,
            config_path=config_path,
            runtime_bundle_root=runtime_bundle_root,
            configured_skills=configured_skills,
            skill_health_summary=skill_health_summary,
            convergence_policy=convergence_policy,
            timeout_retries=timeout_retries,
            timeout_retry_backoff_seconds=timeout_retry_backoff_seconds,
            sleep_fn=sleep_fn,
            no_timeout=no_timeout,
            run_subprocess=run_subprocess,
            parse_json_stdout=parse_json_stdout,
            unwrap_agent_payload=unwrap_agent_payload,
            summarize_payloads=summarize_payloads,
            normalize_answer_tracks=normalize_answer_tracks,
            ensure_runtime_bundle=ensure_runtime_bundle,
            build_single_llm_prompt=build_single_llm_prompt,
            slugify=slugify,
            benchmark_agent_thinking=benchmark_agent_thinking,
            workspace_manager=workspace_manager,
            allowed_workspace_roots=(runtime_paths.skills_root, runtime_paths.project_root / "scripts" / "run_skill.py"),
            contamination_auditor=contamination_auditor,
        )


class ChemQARunner(_BenchmarkingChemQARunner):
    def __init__(
        self,
        *,
        chemqa_root: Path,
        timeout_seconds: int,
        config_path: Path,
        slot_set: str,
        review_rounds: int | None,
        rebuttal_rounds: int | None,
        model_profile: str,
        runtime_bundle_root: Path,
        launch_workspace_root: Path,
        convergence_policy: ConvergencePolicy | None = None,
        workspace_manager: AttemptWorkspaceManager | None = None,
        contamination_auditor=None,
    ) -> None:
        compatibility_manager = workspace_manager is None
        if workspace_manager is None:
            compatibility_root = runtime_bundle_root.expanduser().resolve()
            workspace_manager = AttemptWorkspaceManager(
                runtime_root=compatibility_root / ".benchmark-test-workspaces" / "runs",
                output_root=compatibility_root / ".benchmark-test-output",
                run_id="chemqa-runner-test",
                invocation_id=uuid.uuid4().hex,
                templates=default_workspace_templates(runtime_paths.project_root),
                protected_roots=_compatibility_protected_roots(
                    runtime_root=compatibility_root / ".benchmark-test-workspaces" / "runs",
                    output_root=compatibility_root / ".benchmark-test-output",
                ),
            )
        if compatibility_manager and contamination_auditor is None:
            contamination_auditor = lambda **_kwargs: ContaminationAudit(status="clean")
        super().__init__(
            chemqa_root=chemqa_root,
            timeout_seconds=timeout_seconds,
            config_path=config_path,
            slot_set=slot_set,
            review_rounds=review_rounds,
            rebuttal_rounds=rebuttal_rounds,
            model_profile=model_profile,
            runtime_bundle_root=runtime_bundle_root,
            launch_workspace_root=launch_workspace_root,
            launch_script=chemqa_root / "scripts" / "launch_from_preset.py",
            collect_script=chemqa_root / "scripts" / "collect_artifacts.py",
            runtime_dir=chemqa_root.parent / "debateclaw-v1" / "scripts",
            current_python=current_python,
            run_subprocess=run_subprocess,
            parse_json_stdout=parse_json_stdout,
            deep_copy_jsonish=deep_copy_jsonish,
            ensure_runtime_bundle=ensure_runtime_bundle,
            build_chemqa_goal=build_chemqa_goal,
            resolve_chemqa_answer_kind=resolve_chemqa_answer_kind,
            cleanup_manifest_path=cleanup_manifest_path,
            build_cleanup_manifest_payload=build_cleanup_manifest_payload,
            write_cleanup_manifest=write_cleanup_manifest,
            register_pending_cleanup_manifest=register_pending_cleanup_manifest,
            update_cleanup_manifest=update_cleanup_manifest,
            invoke_cleanroom_cleanup=invoke_cleanroom_cleanup,
            unregister_pending_cleanup_manifest=unregister_pending_cleanup_manifest,
            now_stamp=now_stamp,
            slugify=slugify,
            default_chemqa_preset=DEFAULT_CHEMQA_PRESET,
            default_openclaw_env_file=DEFAULT_OPENCLAW_ENV_FILE,
            actual_slot_ids=actual_slot_ids,
            workspace_manager=workspace_manager,
            session_audit_resolver=lambda agent_id, session_id, *, session_store_path=None: inspect_postflight_session(
                agent_id,
                session_id,
                config_path=config_path,
                session_store_path=session_store_path,
            ),
            contamination_auditor=contamination_auditor,
            allowed_workspace_roots=(runtime_paths.skills_root,),
            unique_run_suffix=not compatibility_manager,
            normalize_chemqa_run_status=normalize_chemqa_run_status,
            is_chemqa_terminal_status=is_chemqa_terminal_status,
            is_chemqa_success_status=is_chemqa_success_status,
            build_chemqa_full_response=build_chemqa_full_response,
            build_chemqa_response_from_submission=build_chemqa_response_from_submission,
            load_yaml_mapping=load_yaml_mapping,
            normalize_space=normalize_space,
            benchmark_error_factory=BenchmarkError,
            cleanup_error_factory=CleanupFatalError,
            convergence_policy=convergence_policy,
        )

    def _wait_for_terminal_status(self, run_id: str, *, timeout_seconds: int) -> dict[str, Any]:
        if not hasattr(self, "_is_chemqa_terminal_status"):
            self._is_chemqa_terminal_status = is_chemqa_terminal_status
        if not hasattr(self, "_normalize_chemqa_run_status"):
            self._normalize_chemqa_run_status = normalize_chemqa_run_status
        if not hasattr(self, "_benchmark_error_factory"):
            self._benchmark_error_factory = BenchmarkError
        if not hasattr(self, "convergence_policy"):
            self.convergence_policy = ConvergencePolicy(timeout_seconds=timeout_seconds)
        return super()._wait_for_terminal_status(run_id, timeout_seconds=timeout_seconds)

    def _candidate_protocol_dirs(self, run_id: str, run_status: dict[str, Any]) -> list[Path]:
        if not hasattr(self, "_actual_slot_ids"):
            self._actual_slot_ids = actual_slot_ids
        return super()._candidate_protocol_dirs(run_id, run_status)

    def _build_candidate_submission_fallback(self, run_id: str, run_status: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
        if not hasattr(self, "_load_yaml_mapping"):
            self._load_yaml_mapping = load_yaml_mapping
        if not hasattr(self, "_build_chemqa_response_from_submission"):
            self._build_chemqa_response_from_submission = build_chemqa_response_from_submission
        if not hasattr(self, "_normalize_space"):
            self._normalize_space = normalize_space
        return super()._build_candidate_submission_fallback(run_id, run_status)


def build_runner(*, runner_kind: str, **kwargs):
    return _runner_factory(
        runner_kind=runner_kind,
        chemqa_runner_cls=ChemQARunner,
        single_llm_runner_cls=SingleLLMRunner,
        **kwargs,
    )


def evaluate_chembench_open_ended(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: JudgeClient | None = None,
) -> EvaluationResult:
    try:
        return _shared_evaluate_chembench_open_ended(
            record,
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
            answer_text=answer_text,
            judge=judge,
        )
    except EvaluationError as exc:
        raise BenchmarkError(str(exc)) from exc


def evaluate_frontierscience_olympiad(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: JudgeClient,
) -> EvaluationResult:
    try:
        return _shared_evaluate_frontierscience_olympiad(
            record,
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
            answer_text=answer_text,
            judge=judge,
        )
    except EvaluationError as exc:
        raise BenchmarkError(str(exc)) from exc


def evaluate_frontierscience_research(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: JudgeClient,
) -> EvaluationResult:
    try:
        return _shared_evaluate_frontierscience_research(
            record,
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
            answer_text=answer_text,
            judge=judge,
        )
    except EvaluationError as exc:
        raise BenchmarkError(str(exc)) from exc


def evaluate_superchem_multiple_choice_rpf(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: JudgeClient,
) -> EvaluationResult:
    try:
        return _shared_evaluate_superchem_multiple_choice_rpf(
            record,
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
            answer_text=answer_text,
            judge=judge,
        )
    except EvaluationError as exc:
        raise BenchmarkError(str(exc)) from exc


def evaluate_hle(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: JudgeClient,
) -> EvaluationResult:
    try:
        return _shared_evaluate_hle(
            record,
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
            answer_text=answer_text,
            judge=judge,
        )
    except EvaluationError as exc:
        raise BenchmarkError(str(exc)) from exc


def evaluate_generic_semantic(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: JudgeClient,
) -> EvaluationResult:
    try:
        return _shared_evaluate_generic_semantic(
            record,
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
            answer_text=answer_text,
            judge=judge,
        )
    except EvaluationError as exc:
        raise BenchmarkError(str(exc)) from exc


def evaluate_answer(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: JudgeClient,
) -> EvaluationResult:
    try:
        return evaluate_record(
            record,
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
            answer_text=answer_text,
            judge=judge,
        )
    except (EvaluationError, EvaluationRegistryError) as exc:
        raise BenchmarkError(str(exc)) from exc


register_default_evaluators()


LEGACY_SUMMARY_CSV_FILENAMES = (
    "summary_by_group.csv",
    "summary_by_group_and_subset.csv",
)


def remove_legacy_summary_csvs(output_root: Path) -> None:
    for filename in LEGACY_SUMMARY_CSV_FILENAMES:
        path = output_root / filename
        if path.exists():
            path.unlink()


def select_group_ids(raw: str) -> list[str]:
    group_ids = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [item for item in group_ids if item not in EXPERIMENT_GROUPS]
    if unknown:
        raise BenchmarkError(f"Unknown group ids: {', '.join(unknown)}")
    if not group_ids:
        raise BenchmarkError("No experiment groups selected.")
    return group_ids


def select_dataset_files(args: argparse.Namespace) -> list[Path]:
    root = Path(args.benchmark_root).expanduser().resolve()
    if args.files:
        files = [Path(item.strip()).expanduser().resolve() for item in args.files.split(",") if item.strip()]
        missing = [str(path) for path in files if not path.is_file()]
        if missing:
            raise BenchmarkError(f"Missing benchmark files: {', '.join(missing)}")
        return files

    discovered = discover_dataset_files(root)
    if args.datasets:
        wanted = {item.strip() for item in args.datasets.split(",") if item.strip()}
        discovered = [path for path in discovered if dataset_name_from_file(path) in wanted]
    return discovered


def print_dataset_listing(paths: list[Path]) -> None:
    payload = [
        {
            "dataset": dataset_name_from_file(path),
            "path": str(path),
        }
        for path in paths
    ]
    print(json.dumps(payload, indent=2, ensure_ascii=False))



def print_selected_records(records: list[BenchmarkRecord]) -> None:
    payload = [
        {
            "record_id": record.record_id,
            "subset": classify_subset(record),
            "dataset": record.dataset,
            "eval_kind": record.eval_kind,
            "source_file": record.source_file,
            "prompt_preview": normalize_space(record.prompt)[:200],
        }
        for record in records
    ]
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def parse_retry_backoff_seconds(raw: str, *, max_retries: int) -> tuple[float, ...]:
    if max_retries <= 0:
        return ()
    values: list[float] = []
    for item in str(raw or "").split(","):
        stripped = item.strip()
        if not stripped:
            continue
        try:
            value = float(stripped)
        except ValueError as exc:
            raise BenchmarkError(f"Invalid --single-timeout-retry-backoff-seconds value: {stripped}") from exc
        if value < 0:
            raise BenchmarkError("--single-timeout-retry-backoff-seconds values must be non-negative")
        values.append(value)
    if not values:
        raise BenchmarkError("--single-timeout-retry-backoff-seconds must include at least one value")
    while len(values) < max_retries:
        values.append(values[-1])
    return tuple(values[:max_retries])


def filter_records_by_subsets(records: list[BenchmarkRecord], raw_subsets: str | None) -> list[BenchmarkRecord]:
    wanted = {item.strip() for item in str(raw_subsets or "").split(",") if item.strip()}
    if not wanted:
        return list(records)

    available = {classify_subset(record) for record in records}
    unknown = sorted(wanted - available)
    if unknown:
        known = ", ".join(sorted(available)) or "<none>"
        raise BenchmarkError(f"Unknown subset(s): {', '.join(unknown)}. Available subsets: {known}")

    return [record for record in records if classify_subset(record) in wanted]


def filter_records_by_ids(records: list[BenchmarkRecord], raw_record_ids: str | None) -> list[BenchmarkRecord]:
    requested = [item.strip() for item in str(raw_record_ids or "").split(",") if item.strip()]
    if not requested:
        return list(records)
    if len(requested) != len(set(requested)):
        raise BenchmarkError("--record-ids must not contain duplicate ids")

    records_by_id: dict[str, BenchmarkRecord] = {}
    duplicate_available_ids: set[str] = set()
    for record in records:
        if record.record_id in records_by_id:
            duplicate_available_ids.add(record.record_id)
        records_by_id[record.record_id] = record
    ambiguous = sorted(set(requested) & duplicate_available_ids)
    if ambiguous:
        raise BenchmarkError(f"Ambiguous record id(s) across selected datasets: {', '.join(ambiguous)}")

    unknown = [record_id for record_id in requested if record_id not in records_by_id]
    if unknown:
        raise BenchmarkError(f"Unknown record id(s): {', '.join(unknown)}")
    return [records_by_id[record_id] for record_id in requested]



def build_group_waves(group_ids: list[str], *, max_concurrent_groups: int) -> list[list[str]]:
    if max_concurrent_groups <= 0:
        raise BenchmarkError("--max-concurrent-groups 必须是正整数")
    waves: list[list[str]] = []
    for index in range(0, len(group_ids), max_concurrent_groups):
        waves.append(group_ids[index : index + max_concurrent_groups])
    return waves



def count_per_record_outputs(output_root: Path, *, group_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    per_record_root = output_root / "per-record"
    for group_id in group_ids:
        group_dir = per_record_root / group_id
        counts[group_id] = len(list(group_dir.glob("*.json"))) if group_dir.is_dir() else 0
    return counts


def pending_records_for_group(
    records: list[BenchmarkRecord],
    *,
    output_root: Path,
    group_id: str,
    merge_existing_per_record: bool,
) -> list[BenchmarkRecord]:
    if not merge_existing_per_record:
        return list(records)
    group_root = output_root / "per-record" / group_id
    return [record for record in records if not (group_root / f"{slugify(record.record_id)}.json").is_file()]



def load_group_record_result(path: Path) -> GroupRecordResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "schema_version" not in payload:
        runner_meta = payload.get("runner_meta") or {}
        raw = payload.get("raw") or {}
        evaluation = payload.get("evaluation") or {}
        primary_metric = str(evaluation.get("primary_metric") or "")
        fallback_used = bool(runner_meta.get("fallback_used"))
        fallback_source = str(runner_meta.get("fallback_source") or "")
        run_status_present = isinstance(raw.get("run_status"), dict)
        scored = bool(runner_meta.get("scored", primary_metric != "execution_error"))
        explicit_evaluable = runner_meta.get("evaluable")
        explicit_reliability = str(runner_meta.get("answer_reliability") or "").strip()
        explicit_recovery_mode = str(runner_meta.get("recovery_mode") or "").strip()
        explicit_degraded = runner_meta.get("degraded_execution")
        evaluable = bool(explicit_evaluable) if explicit_evaluable is not None else scored
        if fallback_used:
            run_lifecycle_status = "completed" if scored else "failed"
            protocol_completion_status = "failed" if run_status_present else "missing"
            recovery_mode = explicit_recovery_mode or fallback_source or "none"
            if recovery_mode == "run-status-final-answer-preview":
                answer_availability = "preview_only"
                default_reliability = "low_confidence_recovered"
            else:
                answer_availability = "recovered_candidate"
                default_reliability = "high_confidence_recovered"
            answer_reliability = explicit_reliability or default_reliability
            degraded_execution = bool(explicit_degraded) if explicit_degraded is not None else True
        elif scored:
            run_lifecycle_status = "completed"
            protocol_completion_status = "completed"
            answer_availability = "native_final"
            answer_reliability = explicit_reliability or "native"
            recovery_mode = explicit_recovery_mode or "none"
            degraded_execution = bool(explicit_degraded) if explicit_degraded is not None else False
        else:
            run_lifecycle_status = "failed"
            protocol_completion_status = "failed" if run_status_present else "missing"
            answer_availability = "missing"
            answer_reliability = explicit_reliability or "none"
            evaluable = False if explicit_evaluable is None else bool(explicit_evaluable)
            recovery_mode = explicit_recovery_mode or "none"
            degraded_execution = bool(explicit_degraded) if explicit_degraded is not None else True
        payload = {
            **payload,
            # Upconvert schema-v1 per-record payloads so historical outputs remain loadable.
            "schema_version": 2,
            "run_lifecycle_status": run_lifecycle_status,
            "protocol_completion_status": protocol_completion_status,
            "protocol_acceptance_status": None,
            "answer_availability": answer_availability,
            "answer_reliability": answer_reliability,
            "evaluable": evaluable,
            "scored": scored,
            "recovery_mode": recovery_mode,
            "degraded_execution": degraded_execution,
            "execution_error_kind": None if scored else "execution_error",
        }
    if "skills_enabled" not in payload:
        group = EXPERIMENT_GROUPS.get(str(payload.get("group_id") or ""))
        payload["skills_enabled"] = bool(getattr(group, "skills_enabled", False))
    return GroupRecordResult(**payload)



def resolve_aggregate_group_ids(
    selected_group_ids: list[str],
    *,
    output_root: Path,
    merge_existing_per_record: bool,
) -> list[str]:
    if not merge_existing_per_record:
        return list(selected_group_ids)
    present = set(selected_group_ids)
    per_record_root = output_root / "per-record"
    for group_id in EXPERIMENT_GROUPS:
        group_dir = per_record_root / group_id
        if group_dir.is_dir() and any(group_dir.glob("*.json")):
            present.add(group_id)
    return [group_id for group_id in EXPERIMENT_GROUPS if group_id in present]



def load_results_from_output_root(output_root: Path, *, group_ids: list[str]) -> list[GroupRecordResult]:
    results: list[GroupRecordResult] = []
    for group_id in group_ids:
        group_dir = output_root / "per-record" / group_id
        if not group_dir.is_dir():
            continue
        for path in sorted(group_dir.glob("*.json")):
            results.append(load_group_record_result(path))
    return results


def apply_verifier_grounded_reporting_references(
    results: list[GroupRecordResult],
) -> list[GroupRecordResult]:
    dataset = "verifier_grounded_property_calculation"
    property_results = [item for item in results if str(getattr(item, "dataset", "")) == dataset]
    if not property_results:
        return results
    try:
        samples = load_public_sample_answers("property_calculation")
    except VerifierGroundedRuntimeError as exc:
        raise BenchmarkError(f"Unable to load public property-calculation gold: {exc}") from exc

    references: dict[str, str] = {}
    for sample in samples:
        task_id = str(sample.get("task_id") or "").strip()
        answer = {key: value for key, value in sample.items() if key != "task_id"}
        if task_id and answer:
            references[task_id] = json.dumps(
                answer,
                ensure_ascii=False,
                separators=(",", ":"),
            )
    missing = sorted(
        {
            str(getattr(item, "record_id", "") or "")
            for item in property_results
            if str(getattr(item, "record_id", "") or "") not in references
        }
    )
    if missing:
        raise BenchmarkError(
            "Verifier-grounded property-calculation results are missing public gold for: "
            + ", ".join(missing)
        )
    for item in property_results:
        item.reference_answer = references[str(getattr(item, "record_id", "") or "")]
    return results


def write_wave_status(
    output_root: Path,
    *,
    wave_index: int,
    wave_group_ids: list[str],
    status: str,
    started_at: str,
    completed_at: str | None = None,
    per_record_counts: dict[str, int] | None = None,
    inter_wave_delay_seconds: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "wave_index": wave_index,
        "groups": wave_group_ids,
        "status": status,
        "started_at": started_at,
    }
    if completed_at is not None:
        payload["completed_at"] = completed_at
    if per_record_counts is not None:
        payload["per_record_counts"] = per_record_counts
    if inter_wave_delay_seconds is not None:
        payload["inter_wave_delay_seconds"] = inter_wave_delay_seconds
    save_json(output_root / "waves" / f"wave-{wave_index:02d}.json", payload)



def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def automated_evaluation_launch_failed(output_root: Path, exc: Exception) -> dict[str, Any]:
    analysis_dir = output_root / "analysis"
    status_path = analysis_dir / "status.json"
    return {
        "status": "launch_failed",
        "error": f"{type(exc).__name__}: {exc}",
        "analysis_dir": str(analysis_dir),
        "status_path": str(status_path),
        "input_bundle_path": str(analysis_dir / "input-bundle.json"),
        "events_path": str(analysis_dir / "codex-events.jsonl"),
        "report_path": str(analysis_dir / "report.json"),
        "markdown_report_path": str(analysis_dir / "report.md"),
    }


def automated_evaluation_skipped(output_root: Path) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": "disabled_by_cli",
        "output_root": str(output_root),
        **analysis_paths(output_root),
    }



def build_execution_error_evaluation(record: BenchmarkRecord, *, error_message: str) -> EvaluationResult:
    return _shared_build_execution_error_evaluation(record, error_message=error_message)


def build_error_group_record_result(
    *,
    group: ExperimentGroup,
    record: BenchmarkRecord,
    error_message: str,
    elapsed_seconds: float = 0.0,
    answer_text: str = "",
    short_answer_text: str = "",
    full_response_text: str = "",
    runner_meta: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
) -> GroupRecordResult:
    entry = _shared_build_error_group_record_result(
        group=group,
        record=record,
        error_message=error_message,
        elapsed_seconds=elapsed_seconds,
        answer_text=answer_text,
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
        runner_meta=runner_meta,
        raw=raw,
        classify_subset_fn=classify_subset,
        normalize_answer_tracks_fn=normalize_answer_tracks,
        build_execution_error_evaluation_fn=build_execution_error_evaluation,
        deep_copy_jsonish_fn=deep_copy_jsonish,
    )
    return GroupRecordResult(**asdict(entry))


def materialize_group_failure_results(
    *,
    group: ExperimentGroup,
    records: list[BenchmarkRecord],
    output_root: Path,
    error_message: str,
) -> list[GroupRecordResult]:
    entries = _shared_materialize_group_failure_results(
        group=group,
        records=records,
        output_root=output_root,
        error_message=error_message,
        save_json_fn=save_json,
        slugify_fn=slugify,
        classify_subset_fn=classify_subset,
        normalize_answer_tracks_fn=normalize_answer_tracks,
        build_execution_error_evaluation_fn=build_execution_error_evaluation,
        deep_copy_jsonish_fn=deep_copy_jsonish,
    )
    return [GroupRecordResult(**asdict(entry)) for entry in entries]


def record_group_progress_failure(
    progress_writer: ProgressWriter,
    *,
    group_id: str,
    records: list[BenchmarkRecord],
    error_message: str,
) -> None:
    progress_writer.group_started(group_id)
    progress_writer.error(group_id=group_id, message=error_message)
    for index, record in enumerate(records, start=1):
        progress_writer.record_started(group_id, record.record_id, index=index)
        progress_writer.record_completed(group_id, record.record_id, status="failed", score=0.0)
    progress_writer.group_completed(group_id, status="failed")


def run_group(
    *,
    group: ExperimentGroup,
    records: list[BenchmarkRecord],
    output_root: Path,
    single_timeout: int,
    chemqa_timeout: int,
    judge: JudgeClient,
    config_path: Path,
    single_agent: str,
    chemqa_root: Path,
    chemqa_model_profile: str,
    review_rounds: int | None,
    rebuttal_rounds: int | None,
    single_convergence_policy: ConvergencePolicy | None = None,
    chemqa_convergence_policy: ConvergencePolicy | None = None,
    experiment_specs: dict[str, ExperimentSpec] | None = None,
    skill_health_summary: dict[str, Any] | None = None,
    single_timeout_retries: int = 3,
    single_timeout_retry_backoff_seconds: tuple[int | float, ...] | list[int | float] = (5, 15, 45),
    single_agent_thinking: str = DEFAULT_SINGLE_AGENT_THINKING,
    no_timeout: bool = False,
    workspace_manager: AttemptWorkspaceManager | None = None,
    progress_writer: Any | None = None,
) -> list[GroupRecordResult]:
    try:
        return _orchestration.run_group(
            group=group,
            records=records,
            output_root=output_root,
            single_timeout=single_timeout,
            chemqa_timeout=chemqa_timeout,
            judge=judge,
            config_path=config_path,
            single_agent=single_agent,
            chemqa_root=chemqa_root,
            chemqa_model_profile=chemqa_model_profile,
            review_rounds=review_rounds,
            rebuttal_rounds=rebuttal_rounds,
            single_convergence_policy=single_convergence_policy,
            chemqa_convergence_policy=chemqa_convergence_policy,
            chemqa_slot_sets=CHEMQA_SLOT_SETS,
            experiment_specs=experiment_specs or EXPERIMENT_SPECS,
            skill_health_summary=skill_health_summary,
            single_timeout_retries=single_timeout_retries,
            single_timeout_retry_backoff_seconds=single_timeout_retry_backoff_seconds,
            single_agent_thinking=single_agent_thinking,
            no_timeout=no_timeout,
            workspace_manager=workspace_manager,
            progress_writer=progress_writer,
            build_runner_fn=build_runner,
            evaluate_answer_fn=evaluate_answer,
            build_error_group_record_result_fn=build_error_group_record_result,
            classify_subset_fn=classify_subset,
            save_json_fn=save_json,
            slugify_fn=slugify,
        )
    except _orchestration.OrchestrationError as exc:
        raise BenchmarkError(str(exc)) from exc


def run_benchmark_web_search_preflight(
    *,
    group_ids: list[str],
    config_pool: ConfigPool,
    args: argparse.Namespace,
) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    for group_id in group_ids:
        group = EXPERIMENT_GROUPS[group_id]
        if not group.websearch:
            continue
        spec = EXPERIMENT_SPECS.get(group_id)
        agent_id = (
            spec.resolve_single_agent_id(args.single_agent_id_override)
            if spec is not None and group.runner == "single_llm"
            else JUDGE_AGENT_ID
        )
        config_path = config_pool.config_for_group(group)
        effective_agent_id = agent_id or JUDGE_AGENT_ID
        identity = AttemptIdentity(
            run_id=config_pool.workspace_manager.run_id,
            invocation_id=config_pool.workspace_manager.invocation_id,
            group_id=group.id,
            runner_kind="web_search_preflight",
            agent_id=effective_agent_id,
            record_id="web-search-preflight",
            attempt_index=0,
            session_id=f"web-search-preflight-{uuid.uuid4().hex[:12]}",
            template_id="single-llm-skills-on-v1"
            if bool(getattr(group, "skills_enabled", False))
            else "single-llm-skills-off-v1",
        )
        try:
            lease = config_pool.workspace_manager.prepare(identity)
        except WorkspaceIsolationError as exc:
            reports[group_id] = {
                "available": False,
                "error": exc.message,
                "workspace_isolation": {
                    "preflight_ok": False,
                    "archive_ok": False,
                    "execution_error": dict(exc.details),
                },
            }
            continue
        environment = os.environ.copy()
        environment.update(
            {
                "BENCHMARK_WORKSPACE_DIR": str(lease.active_workspace),
                "BENCHMARK_SKILL_SCRATCH_DIR": str(lease.scratch_dir),
                "BENCHMARK_SKILL_REQUEST_DIR": str(lease.request_dir),
                "BENCHMARK_SKILL_OUTPUT_DIR": str(lease.output_dir),
                "BENCHMARK_SKILL_NOTES_DIR": str(lease.notes_dir),
            }
        )
        report = run_web_search_preflight(
            agent_id=effective_agent_id,
            config_path=config_path,
            current_python_path=current_python(),
            run_subprocess=run_subprocess,
            base_env=environment,
        )
        transcript_path = str(report.get("transcript_path") or "").strip()
        contamination_audit = config_pool.workspace_manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": transcript_path}},
            environment=environment,
        )
        isolation_meta = lease.to_meta()
        isolation_meta.update(
            {
                "contaminated": contamination_audit.status == "contaminated",
                "audit_status": contamination_audit.status,
                "findings": [dict(finding) for finding in contamination_audit.findings],
            }
        )
        if contamination_audit.status != "clean":
            report["available"] = False
            report["error"] = "web_search preflight workspace contamination audit failed"
        try:
            archive = config_pool.workspace_manager.seal(
                lease,
                AttemptOutcome(
                    runner_status="completed" if report.get("available") is True else "failed",
                    archive_reason="attempt_terminal",
                    contamination_audit=contamination_audit,
                ),
            )
            isolation_meta.update(archive.to_meta())
        except WorkspaceIsolationError as exc:
            isolation_meta["archive_ok"] = False
            isolation_meta["archive_error"] = dict(exc.details)
            report["available"] = False
            report["error"] = exc.message
        report["workspace_isolation"] = isolation_meta
        reports[group_id] = report
    return {
        "enabled": True,
        "provider": "duckduckgo",
        "reports": reports,
        "available": all(bool(report.get("available")) for report in reports.values()) if reports else True,
        "proxy_env": proxy_environment_report(build_openclaw_subprocess_env(base_env=os.environ.copy())),
    }



def main() -> int:
    args = parse_args()
    single_timeout_retries = max(0, int(getattr(args, "single_timeout_retries", 3)))
    single_timeout_retry_backoff_seconds = parse_retry_backoff_seconds(
        str(getattr(args, "single_timeout_retry_backoff_seconds", "5,15,45")),
        max_retries=single_timeout_retries,
    )
    timeout_mode = "no_timeout" if bool(getattr(args, "no_timeout", False)) else "bounded"
    single_convergence_policy = ConvergencePolicy(
        timeout_seconds=args.single_timeout,
        max_unchanged_status_polls=args.max_unchanged_status_polls,
        max_recovery_attempts=args.max_recovery_attempts,
    )
    chemqa_convergence_policy = ConvergencePolicy(
        timeout_seconds=args.chemqa_timeout,
        max_unchanged_status_polls=args.max_unchanged_status_polls,
        max_recovery_attempts=args.max_recovery_attempts,
    )
    convergence_policy_meta = {
        "single_llm": single_convergence_policy.to_meta(),
        "chemqa": chemqa_convergence_policy.to_meta(),
    }
    group_ids = select_group_ids(args.groups)
    dataset_files = select_dataset_files(args)
    if args.list_datasets:
        print_dataset_listing(dataset_files)
        return 0
    if not dataset_files:
        raise BenchmarkError("No benchmark files discovered.")

    all_records = filter_records_by_ids(
        filter_records_by_subsets(load_records(dataset_files), args.subsets),
        getattr(args, "record_ids", None),
    )
    if args.random_count_per_subset is not None:
        selected_pool = sample_records_per_subset(
            all_records,
            per_subset_count=args.random_count_per_subset,
            seed=args.random_seed,
        )
    else:
        selected_pool = all_records

    records = apply_offset_limit(selected_pool, offset=args.offset, limit=args.limit)
    if not records:
        raise BenchmarkError("No benchmark records selected.")
    if args.print_selected_records:
        print_selected_records(records)
        return 0

    if args.exact_output_dir:
        output_root = Path(args.exact_output_dir).expanduser().resolve()
    else:
        output_root = Path(args.output_dir).expanduser().resolve() / f"benchmark-{now_stamp()}"
    ensure_dir(output_root)
    run_id = output_root.name
    invocation_id = str(uuid.uuid4())
    workspace_manager = AttemptWorkspaceManager(
        runtime_root=runtime_paths.benchmark_runtime_root / "runs",
        output_root=output_root,
        run_id=run_id,
        invocation_id=invocation_id,
        templates=default_workspace_templates(runtime_paths.project_root),
        protected_roots=build_production_protected_roots(
            runtime_root=runtime_paths.benchmark_runtime_root / "runs",
            output_root=output_root,
        ),
    )
    try:
        workspace_startup_recovery = workspace_manager.recover_all_incomplete()
    except WorkspaceIsolationError as exc:
        raise BenchmarkError(f"Benchmark workspace startup recovery failed: {exc.message}") from exc
    pending_records_by_group = {
        group_id: pending_records_for_group(
            records,
            output_root=output_root,
            group_id=group_id,
            merge_existing_per_record=bool(args.merge_existing_per_record),
        )
        for group_id in group_ids
    }

    skill_health_reports = check_all_skill_health(BENCHMARK_SKILLS_ALLOWLIST, workspace_root=runtime_paths.project_root)
    skill_health_summary = summarize_skill_health(skill_health_reports)
    save_json(output_root / "skill-health.json", {"summary": skill_health_summary, "skills": skill_health_reports})
    effective_experiment_specs = build_effective_experiment_specs(
        EXPERIMENT_SPECS,
        skill_health_reports=skill_health_reports,
    )

    config_pool = ConfigPool(
        base_config_path=Path(args.openclaw_config).expanduser().resolve(),
        output_root=output_root,
        run_id=run_id,
        invocation_id=invocation_id,
        workspace_manager=workspace_manager,
        single_agent_model=args.single_agent_model,
        judge_model=args.judge_model,
        single_agent_id_override=args.single_agent_id_override,
        experiment_specs=effective_experiment_specs,
    )
    web_search_preflight = run_benchmark_web_search_preflight(
        group_ids=[group_id for group_id in group_ids if pending_records_by_group[group_id]],
        config_pool=config_pool,
        args=args,
    )
    save_json(output_root / "web-search-preflight.json", web_search_preflight)
    judge = JudgeClient(
        judge_agent=args.judge_agent,
        timeout_seconds=args.judge_timeout,
        config_path=config_pool.judge_config_path(),
        thinking=args.judge_agent_thinking,
        workspace_manager=workspace_manager,
    )
    group_waves = build_group_waves(group_ids, max_concurrent_groups=args.max_concurrent_groups)
    progress_writer = ProgressWriter(
        output_root,
        total_records=sum(len(group_records) for group_records in pending_records_by_group.values()),
        groups=group_ids,
    )
    progress_writer.run_started()

    group_results: dict[str, list[GroupRecordResult]] = {}
    try:
        for wave_index, wave_group_ids in enumerate(group_waves, start=1):
            started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            write_wave_status(
                output_root,
                wave_index=wave_index,
                wave_group_ids=wave_group_ids,
                status="running",
                started_at=started_at,
                inter_wave_delay_seconds=args.inter_wave_delay_seconds,
            )
            with ThreadPoolExecutor(max_workers=max(1, len(wave_group_ids))) as executor:
                future_map = {}
                for group_id in wave_group_ids:
                    group = EXPERIMENT_GROUPS[group_id]
                    group_records = pending_records_by_group[group_id]
                    if not group_records:
                        group_results[group_id] = []
                        continue
                    preflight_report = dict((web_search_preflight.get("reports") or {}).get(group_id) or {})
                    if group.websearch and preflight_report.get("available") is not True:
                        error_message = (
                            "web_search preflight failed for group "
                            f"`{group_id}`: {preflight_report.get('error') or 'web_search unavailable'}"
                        )
                        group_results[group_id] = materialize_group_failure_results(
                            group=group,
                            records=group_records,
                            output_root=output_root,
                            error_message=error_message,
                        )
                        record_group_progress_failure(
                            progress_writer,
                            group_id=group_id,
                            records=group_records,
                            error_message=error_message,
                        )
                        continue
                    config_path = config_pool.config_for_group(group)
                    spec = effective_experiment_specs.get(group_id)
                    single_agent = (
                        spec.resolve_single_agent_id(args.single_agent_id_override)
                        if spec is not None
                        else DEFAULT_SINGLE_AGENT
                    )
                    future = executor.submit(
                        run_group,
                        group=group,
                        records=group_records,
                        output_root=output_root,
                        single_timeout=args.single_timeout,
                        chemqa_timeout=args.chemqa_timeout,
                        judge=judge,
                        config_path=config_path,
                        single_agent=single_agent,
                        chemqa_root=Path(args.chemqa_root).expanduser().resolve(),
                        chemqa_model_profile=args.chemqa_model_profile,
                        review_rounds=args.review_rounds,
                        rebuttal_rounds=args.rebuttal_rounds,
                        single_convergence_policy=single_convergence_policy,
                        chemqa_convergence_policy=chemqa_convergence_policy,
                        single_timeout_retries=single_timeout_retries,
                        single_timeout_retry_backoff_seconds=single_timeout_retry_backoff_seconds,
                        single_agent_thinking=args.single_agent_thinking,
                        no_timeout=bool(getattr(args, "no_timeout", False)),
                        workspace_manager=workspace_manager,
                        experiment_specs=effective_experiment_specs,
                        skill_health_summary=skill_health_summary,
                        progress_writer=progress_writer,
                    )
                    future_map[future] = group_id

                for future in as_completed(future_map):
                    group_id = future_map[future]
                    try:
                        group_results[group_id] = future.result()
                    except Exception as exc:
                        group = EXPERIMENT_GROUPS[group_id]
                        error_message = f"Group `{group_id}` failed before returning results: {exc}"
                        group_results[group_id] = materialize_group_failure_results(
                            group=group,
                            records=pending_records_by_group[group_id],
                            output_root=output_root,
                            error_message=error_message,
                        )
                        record_group_progress_failure(
                            progress_writer,
                            group_id=group_id,
                            records=pending_records_by_group[group_id],
                            error_message=error_message,
                        )
            gc.collect()
            completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            write_wave_status(
                output_root,
                wave_index=wave_index,
                wave_group_ids=wave_group_ids,
                status="completed",
                started_at=started_at,
                completed_at=completed_at,
                per_record_counts=count_per_record_outputs(output_root, group_ids=wave_group_ids),
                inter_wave_delay_seconds=args.inter_wave_delay_seconds,
            )
            if wave_index < len(group_waves) and args.inter_wave_delay_seconds > 0:
                time.sleep(args.inter_wave_delay_seconds)
    finally:
        run_pending_cleanroom_cleanup()

    aggregate_group_ids = resolve_aggregate_group_ids(
        group_ids,
        output_root=output_root,
        merge_existing_per_record=args.merge_existing_per_record,
    )
    if args.merge_existing_per_record:
        results = load_results_from_output_root(output_root, group_ids=aggregate_group_ids)
    else:
        results: list[GroupRecordResult] = []
        for group_id in group_ids:
            results.extend(group_results.get(group_id, []))

    apply_verifier_grounded_reporting_references(results)
    for item in results:
        save_json(
            output_root / "per-record" / item.group_id / f"{slugify(item.record_id)}.json",
            asdict(item),
        )
    summary = aggregate_results(results)
    payload = {
        "schema_version": 2,
        "status_axes_description": {
            "run_lifecycle_status": "completed|failed|cancelled",
            "protocol_completion_status": "completed|failed|missing|not_applicable",
            "answer_availability": "native_final|recovered_candidate|preview_only|missing",
            "answer_reliability": "native|high_confidence_recovered|low_confidence_recovered|none",
            "evaluable": "whether a record has a trustworthy scoreable answer",
            "scored": "whether evaluator execution occurred",
            "recovery_mode": "none|candidate_submission|run-status-final-answer-preview|archived_final_answer|protocol_reconstruction",
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark_root": str(Path(args.benchmark_root).expanduser().resolve()),
        "dataset_files": [str(path) for path in dataset_files],
        "groups": [asdict(EXPERIMENT_GROUPS[group_id]) for group_id in aggregate_group_ids],
        "run_groups": [asdict(EXPERIMENT_GROUPS[group_id]) for group_id in group_ids],
        "skill_health_summary": skill_health_summary,
        "web_search_preflight": web_search_preflight,
        "convergence_policy": convergence_policy_meta,
        "single_timeout_retry": {
            "max_retries": single_timeout_retries,
            "backoff_seconds": list(single_timeout_retry_backoff_seconds),
        },
        "timeout_mode": timeout_mode,
        "workspace_isolation": {
            "schema_version": 1,
            "run_id": run_id,
            "invocation_id": invocation_id,
            "forbidden_path_policy": workspace_manager.forbidden_path_policy_manifest(),
        },
        "merge_existing_per_record": args.merge_existing_per_record,
        "random_sampling": {
            "enabled": args.random_count_per_subset is not None,
            "count_per_subset": args.random_count_per_subset,
            "seed": args.random_seed,
        },
        "records": len(records),
        "execution_plan": {
            "mode": "wave-batched",
            "max_concurrent_groups": args.max_concurrent_groups,
            "inter_wave_delay_seconds": args.inter_wave_delay_seconds,
            "waves": group_waves,
        },
        "results": [asdict(item) for item in results],
        "summary": summary,
        "errors": [
            {
                "group_id": item.group_id,
                "record_id": item.record_id,
                "error": item.error,
            }
            for item in results
            if item.error
        ],
    }
    save_json(output_root / "results.json", payload)
    remove_legacy_summary_csvs(output_root)
    runtime_manifest = {
        "execution_plan": {
            "mode": "wave-batched",
            "max_concurrent_groups": args.max_concurrent_groups,
            "inter_wave_delay_seconds": args.inter_wave_delay_seconds,
            "waves": group_waves,
        },
        "aggregate_groups": aggregate_group_ids,
        "run_groups": group_ids,
        "merge_existing_per_record": args.merge_existing_per_record,
        "skill_health": {
            "summary": skill_health_summary,
            "report_path": str(output_root / "skill-health.json"),
        },
        "web_search_preflight": {
            **web_search_preflight,
            "report_path": str(output_root / "web-search-preflight.json"),
        },
        "convergence_policy": convergence_policy_meta,
        "single_timeout_retry": {
            "max_retries": single_timeout_retries,
            "backoff_seconds": list(single_timeout_retry_backoff_seconds),
        },
        "timeout_mode": timeout_mode,
        "workspace_isolation": {
            "schema_version": 1,
            "run_id": run_id,
            "invocation_id": invocation_id,
            "runtime_runs_root": str(workspace_manager.runtime_root),
            "runtime_workspace_root": str(workspace_manager.invocation_runtime_root),
            "archive_root": str(workspace_manager.archive_root),
            "quarantine_root": str(workspace_manager.quarantine_root),
            "templates": workspace_manager.template_manifest(),
            "startup_recovery": workspace_startup_recovery,
            "forbidden_path_policy": workspace_manager.forbidden_path_policy_manifest(),
        },
        "groups": {
            group_id: {
                "group": asdict(EXPERIMENT_GROUPS[group_id]),
                "config_path": str(config_pool.config_for_group(EXPERIMENT_GROUPS[group_id])),
                "effective_skill_allowlist": list(effective_experiment_specs[group_id].skill_allowlist or ()),
                "slot_set": CHEMQA_SLOT_SETS.get(group_id),
                "single_agent": (
                    effective_experiment_specs[group_id].resolve_single_agent_id(args.single_agent_id_override)
                    if group_id in effective_experiment_specs and EXPERIMENT_GROUPS[group_id].runner == "single_llm"
                    else None
                ),
                "single_agent_model": args.single_agent_model,
                "single_agent_thinking": (
                    args.single_agent_thinking if EXPERIMENT_GROUPS[group_id].runner == "single_llm" else None
                ),
                "no_timeout": bool(getattr(args, "no_timeout", False)) if EXPERIMENT_GROUPS[group_id].runner == "single_llm" else None,
                "chemqa_model_profile": args.chemqa_model_profile if EXPERIMENT_GROUPS[group_id].runner == "chemqa" else None,
                "selected_record_count": len(records),
                "pending_record_count": len(pending_records_by_group[group_id]),
                "skipped_existing_record_count": len(records) - len(pending_records_by_group[group_id]),
            }
            for group_id in group_ids
        },
        "judge": {
            "agent": args.judge_agent,
            "model": args.judge_model,
            "thinking": args.judge_agent_thinking,
            "config_path": str(config_pool.judge_config_path()),
        },
    }
    save_json(output_root / "runtime-manifest.json", runtime_manifest)
    if bool(getattr(args, "no_analysis", False)):
        automated_evaluation_status = automated_evaluation_skipped(output_root)
        save_json(Path(automated_evaluation_status["status_path"]), automated_evaluation_status)
    else:
        try:
            automated_evaluation_status = launch_automated_evaluation(output_root)
        except Exception as exc:
            automated_evaluation_status = automated_evaluation_launch_failed(output_root, exc)
            save_json(Path(automated_evaluation_status["status_path"]), automated_evaluation_status)
    runtime_manifest["automated_evaluation"] = automated_evaluation_status
    save_json(output_root / "runtime-manifest.json", runtime_manifest)
    progress_writer.run_completed(status="completed")
    print(json.dumps({"output_dir": str(output_root), "summary": summary}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
