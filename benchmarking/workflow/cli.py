#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from benchmarking.analysis.launcher import launch_automated_evaluation
from benchmarking.core.answer_processing import normalize_answer_tracks
from benchmarking.core.datasets import BenchmarkRecord as _BenchmarkRecord
from benchmarking.core.datasets import classify_subset
from benchmarking.core.reporting import GroupRecordResult as _GroupRecordResult
from benchmarking.core.reporting import (
    aggregate_results,
)
from benchmarking.core.reporting import (
    build_error_group_record_result as _build_error_group_record_result,
)
from benchmarking.core.reporting import (
    materialize_group_failure_results as _materialize_group_failure_results,
)
from benchmarking.dashboard.progress import ProgressWriter
from benchmarking.runtime import config_pool as runtime_config_pool
from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime import subprocess_utils
from benchmarking.runtime.attempt_admission import AttemptAdmissionController
from benchmarking.runtime.container_runtime import DockerContainerRuntime, ContainerRuntimeError
from benchmarking.runtime.agent_workspace import (
    AttemptIdentity,
    AttemptOutcome,
    AttemptWorkspaceManager,
    WorkspaceIsolationError,
)
from benchmarking.runtime.cancellation import (
    CancellationReason,
    CancellationToken,
    OwnedProcessRegistry,
)
from benchmarking.runtime.openclaw_env import (
    build_openclaw_subprocess_env,
    proxy_environment_report,
)
from benchmarking.runtime.atomic_io import atomic_write_json
from benchmarking.runtime.observability import (
    RuntimeMetrics,
    finish_runtime_metrics,
    start_runtime_metrics,
)
from benchmarking.runtime.provider_preflight import check_provider_connection
from benchmarking.runtime.container_network import resolve_container_network
from benchmarking.runtime.vgb_bridge import load_release_config, InvocationValidationCache, validate_runtime_files
from benchmarking.runtime.vgb_worker import VerifierWorker
from benchmarking.scoring.evaluators.verifier_grounded import (
    evaluate_verifier_grounded,
    run_verifier_grounded_evaluation,
    validate_verifier_grounded_release,
)
from benchmarking.scoring.registry import evaluate_record
from benchmarking.scoring.results import build_execution_error_evaluation
from benchmarking.skills.tree import benchmark_skill_routing_inventory
from benchmarking.workflow import (
    dataset_selection,
    experiments,
    run_state,
    runner_adapters,
    runtime_config,
)
from benchmarking.workflow import orchestration as _orchestration
from benchmarking.workflow.errors import BenchmarkError as _BenchmarkError
from benchmarking.workflow.orchestration import PersistedResultRef, persisted_result_ref


def _persisted_ref_for_entry(entry: _GroupRecordResult, output_root: Path) -> PersistedResultRef:
    return persisted_result_ref(
        entry,
        path=output_root / "per-record" / entry.group_id / f"{run_state.slugify(entry.record_id)}.json",
    )


def _cancelled_result_errors(entries: list[PersistedResultRef]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for entry in entries:
        if entry.run_lifecycle_status != "cancelled":
            continue
        if entry.archive_failed:
            errors.append(
                {
                    "stage": "workspace_seal",
                    "group_id": entry.group_id,
                    "record_id": entry.record_id,
                    "error": entry.archive_error or "workspace archive failed",
                }
            )
        if entry.cleanup_failed_count > 0:
            errors.append(
                {
                    "stage": "workspace_cleanup",
                    "group_id": entry.group_id,
                    "record_id": entry.record_id,
                    "failed_count": entry.cleanup_failed_count,
                }
            )
    return errors


def _result_paths_for_aggregation(
    *,
    output_root: Path,
    selected_group_ids: list[str],
    aggregate_group_ids: list[str],
    group_results: dict[str, list[PersistedResultRef]],
    records: list[Any],
    merge_existing_per_record: bool,
) -> list[Path]:
    if merge_existing_per_record:
        return [
            path
            for group_id in aggregate_group_ids
            for path in sorted((output_root / "per-record" / group_id).glob("*.json"))
        ]
    record_order = {record.record_id: index for index, record in enumerate(records)}
    paths: list[Path] = []
    for group_id in selected_group_ids:
        entries = sorted(
            group_results.get(group_id, []),
            key=lambda entry: record_order.get(entry.record_id, len(record_order)),
        )
        paths.extend(Path(entry.path) for entry in entries if entry.path)
    return paths

DEFAULT_BENCHMARK_ROOT = runtime_paths.benchmarks_root
DEFAULT_OPENCLAW_CONFIG = runtime_paths.openclaw_config
DEFAULT_OUTPUT_DIR = runtime_paths.project_state_root / "benchmark-runs"



# Kept as a test patch seam for legacy callers; the benchmark no longer invokes
# a standalone web-search preflight.
run_benchmark_web_search_preflight = None
run_web_search_preflight = None


def install_cancellation_signal_handlers(
    cancellation_token: CancellationToken,
) -> dict[signal.Signals, Any]:
    previous_handlers: dict[signal.Signals, Any] = {}

    def request_cancellation(signum: int, _frame: Any) -> None:
        signal_value = signal.Signals(signum)
        cancellation_token.cancel(
            CancellationReason(
                source="signal",
                signal_name=signal_value.name,
                message=f"Benchmark run cancelled by {signal_value.name}",
            )
        )

    for signal_value in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signal_value] = signal.getsignal(signal_value)
        signal.signal(signal_value, request_cancellation)
    return previous_handlers


def restore_signal_handlers(previous_handlers: dict[signal.Signals, Any]) -> None:
    for signal_value, previous_handler in previous_handlers.items():
        signal.signal(signal_value, previous_handler)



def parse_args(service=None) -> argparse.Namespace:
    if service is None:
        from benchmarking.service.single import execution as service
    parser = argparse.ArgumentParser(description=f"Run {service.NAME} benchmark experiments ({service.STATUS}).")
    parser.add_argument("--verifier-mode", choices=("isolated", "worker"), default="isolated",
                        help="Verifier scoring transport; worker is experimental and opt-in")
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT), help="formal-benchmarks/ 根目录")
    parser.add_argument("--openclaw-config", default=str(DEFAULT_OPENCLAW_CONFIG), help="基础 OpenClaw 配置文件")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="分类结果根目录；默认按用途、benchmark、模型和 run ID 分层",
    )
    parser.add_argument(
        "--exact-output-dir",
        help="若提供，则直接把该目录作为本次输出根目录，绕过默认分类层级",
    )
    parser.add_argument(
        "--merge-existing-per-record",
        action="store_true",
        help="聚合结果时合并输出目录中已存在的 per-record 结果，适合断点续跑/部分重跑",
    )
    parser.add_argument(
        "--groups",
        default=",".join(service.experiments.EXPERIMENT_GROUPS),
        help="要运行的实验组，逗号分隔。默认运行当前业务的实验组",
    )
    parser.add_argument(
        "--datasets",
        help="仅运行当前 service 支持的数据集，逗号分隔；默认 VGB 使用 pinned release inventory",
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
        default=experiments.DEFAULT_SINGLE_AGENT_MODEL,
        help="单一 LLM baseline runtime model，默认锁定为 qwen3.5-plus",
    )
    parser.add_argument(
        "--single-agent-thinking",
        default=experiments.DEFAULT_SINGLE_AGENT_THINKING,
        choices=experiments.THINKING_LEVEL_CHOICES,
        help="单一 LLM baseline OpenClaw thinking level，默认 high",
    )
    parser.add_argument("--single-timeout", type=int, default=7200, help="单一 LLM 每题超时秒数，默认 7200 秒（2 小时）")
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
    parser.add_argument("--execution-backend", choices=("host", "docker"), default="docker", help="single-LLM execution backend")
    parser.add_argument("--container-image", default="openclaw-benchmark-single-llm:latest", help="Docker image for single-LLM attempts")
    parser.add_argument("--container-cpus", type=float, help="CPU limit per single-LLM container")
    parser.add_argument("--container-memory-bytes", type=int, help="Memory limit per single-LLM container")
    parser.add_argument("--container-pids-limit", type=int, help="PID limit per single-LLM container")
    parser.add_argument("--max-concurrent-attempts", type=int, default=2, help="Maximum admitted single-LLM attempts (default: 2)")
    parser.add_argument("--list-datasets", action="store_true", help="列出可发现的数据集文件后退出")
    parser.add_argument(
        "--print-selected-records",
        action="store_true",
        help="打印本次实际选中的题目清单后退出",
    )
    service.add_arguments(parser)
    args = parser.parse_args()
    if args.max_concurrent_attempts < 1:
        parser.error("--max-concurrent-attempts must be positive")
    return args







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
            raise _BenchmarkError(f"Invalid --single-timeout-retry-backoff-seconds value: {stripped}") from exc
        if value < 0:
            raise _BenchmarkError("--single-timeout-retry-backoff-seconds values must be non-negative")
        values.append(value)
    if not values:
        raise _BenchmarkError("--single-timeout-retry-backoff-seconds must include at least one value")
    while len(values) < max_retries:
        values.append(values[-1])
    return tuple(values[:max_retries])



def build_group_waves(group_ids: list[str], *, max_concurrent_groups: int) -> list[list[str]]:
    if max_concurrent_groups <= 0:
        raise _BenchmarkError("--max-concurrent-groups 必须是正整数")
    waves: list[list[str]] = []
    for index in range(0, len(group_ids), max_concurrent_groups):
        waves.append(group_ids[index : index + max_concurrent_groups])
    return waves



def record_group_progress_failure(
    progress_writer: ProgressWriter,
    *,
    group_id: str,
    records: list[_BenchmarkRecord],
    error_message: str,
) -> None:
    progress_writer.group_started(group_id)
    progress_writer.error(group_id=group_id, message=error_message)
    for index, record in enumerate(records, start=1):
        progress_writer.record_started(group_id, record.record_id, index=index)
        progress_writer.record_completed(group_id, record.record_id, status="failed", score=0.0)
    progress_writer.group_completed(group_id, status="failed")




def _run_main(service=None, *, runtime_metrics: RuntimeMetrics, resources: ExitStack) -> int:
    args = parse_args() if service is None else parse_args(service)
    if service is None:
        from benchmarking.service.single import execution as service
    catalog = service.experiments
    single_timeout_retries = max(0, int(getattr(args, "single_timeout_retries", 3)))
    single_timeout_retry_backoff_seconds = parse_retry_backoff_seconds(
        str(getattr(args, "single_timeout_retry_backoff_seconds", "5,15,45")),
        max_retries=single_timeout_retries,
    )
    timeout_mode = "no_timeout" if bool(getattr(args, "no_timeout", False)) else "bounded"
    convergence_policy_meta = service.convergence_metadata(args)
    group_ids = experiments.select_group_ids(args.groups, groups=catalog.EXPERIMENT_GROUPS)
    dataset_files = service.select_dataset_files(args)
    if args.list_datasets:
        dataset_selection.print_dataset_listing(dataset_files)
        return 0
    if not dataset_files:
        raise _BenchmarkError("No benchmark files discovered.")

    selected_pool = service.select_records(dataset_files, args)

    records = dataset_selection.apply_offset_limit(selected_pool, offset=args.offset, limit=args.limit)
    if not records:
        raise _BenchmarkError("No benchmark records selected.")
    if args.print_selected_records:
        dataset_selection.print_selected_records(records)
        return 0

    verifier_release_config = (
        load_release_config()
        if any(record.grading.kind == "verifier_grounded" for record in records)
        else None
    )
    evaluator_overrides = None
    vgb_validation_cache = InvocationValidationCache() if verifier_release_config is not None else None
    if verifier_release_config is not None:
        validate_runtime_files(verifier_release_config, validation_cache=vgb_validation_cache)
        for record in records:
            if record.grading.kind == "verifier_grounded":
                validate_verifier_grounded_release(
                    record,
                    release_config=verifier_release_config,
                )
        verifier_runner = partial(
            run_verifier_grounded_evaluation,
            release_config=verifier_release_config,
            validation_cache=vgb_validation_cache,
        )
        evaluator_overrides = {
            "verifier_grounded": partial(
                evaluate_verifier_grounded,
                verifier_runner=verifier_runner,
            )
        }
    evaluate_invocation_record = partial(
        evaluate_record,
        evaluator_overrides=evaluator_overrides,
        evaluators=service.evaluator_registry(),
    )

    if args.exact_output_dir:
        output_root = Path(args.exact_output_dir).expanduser().resolve()
    else:
        output_root = dataset_selection.default_run_output_root(
            output_dir=args.output_dir,
            dataset_files=dataset_files,
            records=records,
            single_agent_model=args.single_agent_model,
            timestamp=run_state.now_stamp(),
        )
    run_state.ensure_dir(output_root)
    runtime_metrics.set_output_root(output_root)
    run_id = output_root.name
    invocation_id = runtime_metrics.invocation_id
    pypi_cutoff = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    workspace_manager = AttemptWorkspaceManager(
        runtime_root=runtime_paths.benchmark_runtime_root / "runs",
        output_root=output_root,
        run_id=run_id,
        invocation_id=invocation_id,
        templates=service.workspace_templates(runtime_paths.project_root),
        protected_roots=runtime_config.build_production_protected_roots(
            runtime_root=runtime_paths.benchmark_runtime_root / "runs",
            output_root=output_root,
        ),
    )
    docker_startup = {}
    if getattr(args, "execution_backend", "docker") == "docker" and any(
        catalog.EXPERIMENT_GROUPS[key].runner == "single_llm" for key in group_ids
    ):
        runtime = DockerContainerRuntime()
        try:
            args.container_network = resolve_container_network()
            docker_startup["network"] = args.container_network.to_meta()
            docker_startup["daemon"] = runtime.check_ready()
            docker_startup["requested_image"] = args.container_image
            args.container_image = runtime.resolve_image_digest(args.container_image)
            docker_startup["image_id"] = args.container_image
            docker_startup["recovery"] = runtime.recover_orphans(runtime_root=workspace_manager.runtime_root)
            if any(not item["removed"] and item.get("run_id") == run_id for item in docker_startup["recovery"]):
                raise ContainerRuntimeError("Existing run containers could not be safely recovered")
            docker_startup["provider_connectivity"] = check_provider_connection(
                runtime=runtime, image=args.container_image,
                config_path=Path(args.openclaw_config).expanduser().resolve(), model=args.single_agent_model,
                network=args.container_network,
            )
            if docker_startup["provider_connectivity"]["status"] == "failed":
                report = docker_startup["provider_connectivity"]
                raise ContainerRuntimeError(
                    f"Provider connection failed inside Docker: {report['hostname']} ({report.get('code', 'unknown')}). "
                    "Check the recorded container network and proxy before restarting the run.",
                    code="provider_connectivity_failed",
                )
            docker_startup["status"] = "ready"
        except ContainerRuntimeError as exc:
            docker_startup.update(status="failed", error=str(exc), error_code=exc.code)
            raise _BenchmarkError(f"Docker startup failed: {exc}") from exc
        finally:
            run_state.save_json(output_root / "docker-startup.json", docker_startup)
            run_state.save_json(output_root / "runtime-manifest.json", {"terminal_status": "starting" if docker_startup.get("status") == "ready" else "failed", "docker_startup": docker_startup})
    try:
        workspace_startup_recovery = workspace_manager.recover_all_incomplete()
    except WorkspaceIsolationError as exc:
        raise _BenchmarkError(f"Benchmark workspace startup recovery failed: {exc.message}") from exc
    cancellation_token = CancellationToken()
    process_registry = OwnedProcessRegistry(cancellation_token=cancellation_token)
    admission_controller = AttemptAdmissionController(
        max_attempts=getattr(args, "max_concurrent_attempts", 2),
        cancellation_token=cancellation_token,
    )
    pending_records_by_group = {
        group_id: run_state.pending_records_for_group(
            records,
            output_root=output_root,
            group_id=group_id,
            merge_existing_per_record=bool(args.merge_existing_per_record),
        )
        for group_id in group_ids
    }

    skill_routing_inventory = benchmark_skill_routing_inventory()
    run_state.save_json(output_root / "skill-routing-inventory.json", skill_routing_inventory)
    effective_experiment_specs = catalog.EXPERIMENT_SPECS

    config_pool = runtime_config_pool.ConfigPool(
        base_config_path=Path(args.openclaw_config).expanduser().resolve(),
        output_root=output_root,
        context=runtime_config.runtime_config_context(experiment_specs=effective_experiment_specs, runner_config_builder=service.build_runner_config),
        run_id=run_id,
        invocation_id=invocation_id,
        workspace_manager=workspace_manager,
        single_agent_model=args.single_agent_model,
        judge_model=getattr(args, "judge_model", None),
        single_agent_id_override=args.single_agent_id_override,
    )
    judge = None
    if service.USES_JUDGE:
        from benchmarking.runtime import judge as judge_runtime
        judge = judge_runtime.JudgeClient(
            judge_agent=args.judge_agent,
            timeout_seconds=args.judge_timeout,
            config_path=config_pool.judge_config_path(),
            thinking=args.judge_agent_thinking,
            workspace_manager=workspace_manager,
            cancellation_token=cancellation_token,
            process_registry=process_registry,
        )
    group_waves = service.group_waves(group_ids, args)
    progress_writer = ProgressWriter(
        output_root,
        total_records=sum(len(group_records) for group_records in pending_records_by_group.values()),
        groups=group_ids,
        checkpoint_interval_seconds=1.0,
    )
    result_sink = run_state.ResultSink(output_root)
    progress_writer.run_started()
    previous_signal_handlers = install_cancellation_signal_handlers(cancellation_token)
    verifier_worker = None
    if verifier_release_config is not None and getattr(args, "verifier_mode", "isolated") == "worker":
        verifier_worker = VerifierWorker(
            verifier_release_config,
            evidence_root=output_root / "verifier-worker" / invocation_id,
            cancellation_token=cancellation_token, process_registry=process_registry,
            validation_cache=vgb_validation_cache,
        )
        resources.callback(verifier_worker.close)
        verifier_runner.keywords["worker"] = verifier_worker

    build_error_result = partial(
        _build_error_group_record_result,
        classify_subset_fn=classify_subset,
        normalize_answer_tracks_fn=normalize_answer_tracks,
        build_execution_error_evaluation_fn=build_execution_error_evaluation,
        deep_copy_jsonish_fn=subprocess_utils.deep_copy_jsonish,
    )
    materialize_failure_results = partial(
        _materialize_group_failure_results,
        save_json_fn=result_sink.save_json,
        slugify_fn=run_state.slugify,
        classify_subset_fn=classify_subset,
        normalize_answer_tracks_fn=normalize_answer_tracks,
        build_execution_error_evaluation_fn=build_execution_error_evaluation,
        deep_copy_jsonish_fn=subprocess_utils.deep_copy_jsonish,
        result_reference_fn=lambda entry, path: persisted_result_ref(entry, path=path),
    )
    execute_group = partial(
        _orchestration.run_group,
        build_runner_fn=runner_adapters.build_runner,
        evaluate_answer_fn=evaluate_invocation_record,
        build_error_group_record_result_fn=build_error_result,
        classify_subset_fn=classify_subset,
        save_json_fn=result_sink.save_json,
        slugify_fn=run_state.slugify,
        retain_results=False,
    )

    group_results: dict[str, list[PersistedResultRef]] = {}
    cancellation_errors: list[dict[str, Any]] = []
    try:
        for wave_index, wave_group_ids in enumerate(group_waves, start=1):
            if cancellation_token.is_cancelled:
                break
            started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            run_state.write_wave_status(
                output_root,
                wave_index=wave_index,
                wave_group_ids=wave_group_ids,
                status="running",
                started_at=started_at,
                inter_wave_delay_seconds=args.inter_wave_delay_seconds,
            )
            attempt_limit = getattr(args, "max_concurrent_attempts", 2)
            single_queue = service.USES_ATTEMPT_QUEUE and getattr(_orchestration.run_group, "__module__", "") == "benchmarking.workflow.orchestration"
            from benchmarking.workflow.attempt_queue import AttemptQueueExecutor
            with (AttemptQueueExecutor(attempt_limit, cancellation_token) if single_queue else
                  ThreadPoolExecutor(max_workers=max(1, len(wave_group_ids)))) as executor:
                future_map = {}
                for group_id in wave_group_ids:
                    if cancellation_token.is_cancelled:
                        break
                    group = catalog.EXPERIMENT_GROUPS[group_id]
                    group_records = pending_records_by_group[group_id]
                    if not group_records:
                        group_results[group_id] = []
                        continue
                    config_path = config_pool.config_for_group(group)
                    if single_queue:
                        progress_writer.group_started(group_id)
                    spec = effective_experiment_specs.get(group_id)
                    single_agent = (
                        spec.resolve_single_agent_id(args.single_agent_id_override)
                        if spec is not None
                        else experiments.DEFAULT_SINGLE_AGENT
                    )
                    batches = (
                        [[record] for record in group_records]
                        if group.runner == "single_llm"
                        else [group_records]
                    )
                    for records_batch in batches:
                        future = executor.submit(
                            execute_group,
                            group=group,
                            records=records_batch,
                            output_root=output_root,
                            judge=judge,
                            progress_writer=progress_writer,
                            cancellation_token=cancellation_token,
                            manage_group_lifecycle=not single_queue,
                            runner_options_factory=partial(service.make_runner_options,
                                args=args, group=group, output_root=output_root,
                                config_path=config_path, single_agent=single_agent,
                                workspace_manager=workspace_manager, cancellation_token=cancellation_token,
                                process_registry=process_registry, pypi_cutoff=pypi_cutoff,
                                admission_controller=admission_controller,
                            ),
                        )
                        future_map[future] = (group_id, records_batch)

                for future in (executor.run() if single_queue else as_completed(future_map)):
                    group_id, records_batch = future_map.pop(future)
                    try:
                        completed_entries = future.result()
                        for completed_entry in completed_entries:
                            if isinstance(completed_entry, _GroupRecordResult):
                                result_sink.write(completed_entry)
                                completed_entry = _persisted_ref_for_entry(completed_entry, output_root)
                            group_results.setdefault(group_id, []).append(completed_entry)
                    except Exception as exc:
                        group = catalog.EXPERIMENT_GROUPS[group_id]
                        error_message = f"Group `{group_id}` failed before returning results: {exc}"
                        failure_results = materialize_failure_results(
                            group=group,
                            records=records_batch,
                            output_root=output_root,
                            error_message=error_message,
                        )
                        group_results.setdefault(group_id, []).extend(failure_results)
                        record_group_progress_failure(
                            progress_writer,
                            group_id=group_id,
                            records=records_batch,
                            error_message=error_message,
                        )
            completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            if single_queue:
                for group_id in wave_group_ids:
                    if cancellation_token.is_cancelled:
                        progress_writer.group_cancelled(group_id)
                    else:
                        entries = group_results.get(group_id, [])
                        status = "completed" if all(item.run_lifecycle_status == "completed" for item in entries) else "completed_with_errors"
                        progress_writer.group_completed(group_id, status=status)
            run_state.write_wave_status(
                output_root,
                wave_index=wave_index,
                wave_group_ids=wave_group_ids,
                status="cancelled" if cancellation_token.is_cancelled else "completed",
                started_at=started_at,
                completed_at=completed_at,
                per_record_counts=run_state.count_per_record_outputs(output_root, group_ids=wave_group_ids),
                inter_wave_delay_seconds=args.inter_wave_delay_seconds,
            )
            if cancellation_token.is_cancelled:
                break
            if wave_index < len(group_waves) and args.inter_wave_delay_seconds > 0:
                cancellation_token.wait(args.inter_wave_delay_seconds)
    finally:
        if verifier_worker is not None:
            verifier_worker.close()
        cancellation_errors.extend(cancellation_token.cleanup_errors)
        if cancellation_token.is_cancelled:
            reason = cancellation_token.reason
            progress_writer.run_cancelling(reason=reason.to_payload() if reason is not None else {})
            outcome = process_registry.terminate_all(force=cancellation_token.request_count > 1)
            cancellation_errors.extend(outcome.errors)
            if not outcome.completed:
                cancellation_errors.append(
                    {
                        "stage": "owned_process_cleanup",
                        "active_process_count": outcome.active_process_count,
                        "message": "Owned benchmark processes remained after cancellation cleanup.",
                    }
                )
        try:
            service.cleanup()
        except Exception as exc:
            if not cancellation_token.is_cancelled:
                raise
            cancellation_errors.append(
                {"stage": "cleanroom_cleanup", "error": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            restore_signal_handlers(previous_signal_handlers)

    if cancellation_token.is_cancelled:
        for wave_index, wave_group_ids in enumerate(group_waves, start=1):
            wave_path = output_root / "waves" / f"wave-{wave_index:02d}.json"
            if wave_path.exists():
                continue
            cancelled_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            run_state.write_wave_status(
                output_root,
                wave_index=wave_index,
                wave_group_ids=wave_group_ids,
                status="cancelled",
                started_at=cancelled_at,
                completed_at=cancelled_at,
                per_record_counts=run_state.count_per_record_outputs(output_root, group_ids=wave_group_ids),
                inter_wave_delay_seconds=args.inter_wave_delay_seconds,
            )
        for group_id in group_ids:
            existing = {item.record_id for item in group_results.get(group_id, [])}
            group = catalog.EXPERIMENT_GROUPS[group_id]
            for record in pending_records_by_group[group_id]:
                if record.record_id in existing:
                    continue
                entry = _orchestration.build_cancelled_group_record_result(
                    group=group,
                    record=record,
                    build_error_group_record_result_fn=build_error_result,
                )
                group_results.setdefault(group_id, []).append(
                    _persisted_ref_for_entry(entry, output_root)
                )
                result_sink.write(entry)
                progress_writer.record_cancelled(group_id, record.record_id)
            if any(item.run_lifecycle_status == "cancelled" for item in group_results.get(group_id, [])):
                progress_writer.group_cancelled(group_id)
            elif not pending_records_by_group[group_id]:
                progress_writer.group_completed(group_id, status="completed")
        cancellation_errors.extend(
            _cancelled_result_errors([entry for entries in group_results.values() for entry in entries])
        )

    aggregate_group_ids = run_state.resolve_aggregate_group_ids(
        group_ids,
        output_root=output_root,
        merge_existing_per_record=args.merge_existing_per_record,
    )
    result_paths = _result_paths_for_aggregation(
        output_root=output_root,
        selected_group_ids=group_ids,
        aggregate_group_ids=aggregate_group_ids,
        group_results=group_results,
        records=records,
        merge_existing_per_record=args.merge_existing_per_record,
    )

    has_property_results = False
    for path in result_paths:
        try:
            raw_payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(raw_payload.get("dataset") or "").startswith("verifier_grounded_property_calculation"):
            has_property_results = True
            break
    references = (run_state.verifier_grounded_reporting_reference_map(
                      release_config=verifier_release_config,
                      validation_cache=vgb_validation_cache,
                  )
                  if has_property_results else {})

    def iter_final_results():
        for path in result_paths:
            item = run_state.load_group_record_result(path)
            if references:
                run_state.apply_verifier_grounded_reporting_reference(item, references)
            result_sink.write(item)
            yield item

    result_iter = iter_final_results()
    summary = aggregate_results(result_iter)
    # Re-read canonical files for metadata and final array; only one detail
    # payload is decoded at a time.
    workspace_policies: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for path in result_paths:
        item = run_state.load_group_record_result(path)
        if item.error:
            errors.append({"group_id": item.group_id, "record_id": item.record_id, "error": item.error})
        isolation = (item.runner_meta or {}).get("workspace_isolation") or {}
        if not isinstance(isolation, dict):
            continue
        policy = isolation.get("policy")
        digest = str(isolation.get("policy_digest") or "")
        if isinstance(policy, dict) and digest:
            workspace_policies[digest] = policy
        slots = isolation.get("slots")
        if isinstance(slots, dict):
            for slot in slots.values():
                if isinstance(slot, dict) and isinstance(slot.get("policy"), dict) and str(slot.get("policy_digest") or ""):
                    workspace_policies[str(slot["policy_digest"])] = slot["policy"]
    group_descriptions = []
    for group_id in aggregate_group_ids:
        if group_id in catalog.EXPERIMENT_GROUPS:
            group_descriptions.append(asdict(catalog.EXPERIMENT_GROUPS[group_id]))
        else:
            first = next((run_state.load_group_record_result(path) for path in result_paths
                          if path.parent.name == group_id), None)
            if first is not None:
                group_descriptions.append({"id": group_id, "label": first.group_label,
                    "runner": first.runner, "websearch": first.websearch,
                    "skills_enabled": first.skills_enabled})
    payload = {
        "schema_version": 3,
        "status": (
            "cancelled_with_errors"
            if cancellation_token.is_cancelled and cancellation_errors
            else "cancelled"
            if cancellation_token.is_cancelled
            else "completed"
        ),
        "status_axes_description": {
            "run_lifecycle_status": "completed|failed|cancelled",
            "protocol_completion_status": "completed|failed|missing|not_applicable",
            "answer_availability": "native_final|recovered_candidate|preview_only|missing",
            "answer_reliability": "native|high_confidence_recovered|low_confidence_recovered|none",
            "evaluable": "whether a record has a trustworthy scoreable answer",
            "scored": "whether evaluator execution occurred",
            "recovery_mode": "none|candidate_submission|run-status-final-answer-preview|archived_final_answer|protocol_reconstruction",
            "workspace_isolation": {
                "audit_execution_status": "complete|unavailable",
                "boundary_status": "clean|warning|violated|unknown",
                "contamination_status": "clear|confirmed|indeterminate",
                "adjudication": "scoreable|scoreable_degraded|non_evaluable",
            },
        },
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark_root": str(Path(args.benchmark_root).expanduser().resolve()),
        "dataset_files": [str(path) for path in dataset_files],
        "verifier_grounded_release": (
            verifier_release_config.identity if verifier_release_config is not None else None
        ),
        "verifier_validation_cache": (
            vgb_validation_cache.to_meta() if vgb_validation_cache is not None else None
        ),
        "groups": group_descriptions,
        "run_groups": [asdict(catalog.EXPERIMENT_GROUPS[group_id]) for group_id in group_ids],
        "convergence_policy": convergence_policy_meta,
        "single_timeout_retry": {
            "max_retries": single_timeout_retries,
            "backoff_seconds": list(single_timeout_retry_backoff_seconds),
        },
        "timeout_mode": timeout_mode,
        "workspace_isolation": {
            "schema_version": 3,
            "run_id": run_id,
            "invocation_id": invocation_id,
            "scratch_contract_version": 2,
            "security_boundary": "runtime_guard_and_transcript_audit_not_os_sandbox",
            "forbidden_path_policy": workspace_manager.forbidden_path_policy_manifest(),
            "access_policies": [workspace_policies[key] for key in sorted(workspace_policies)],
        },
        "merge_existing_per_record": args.merge_existing_per_record,
        "random_sampling": service.sampling_metadata(args),
        "records": len(records),
        "execution_plan": {
            "mode": service.SCHEDULING_MODE,
            "attempt_queue": {"unit": "attempt", "group_order": "round_robin", "record_order": "fifo", "scoring_workers": 1},
            "max_concurrent_groups": args.max_concurrent_groups,
            "max_concurrent_attempts": getattr(args, "max_concurrent_attempts", 2),
            "inter_wave_delay_seconds": args.inter_wave_delay_seconds,
            "waves": group_waves,
        },
        "summary": summary,
        "errors": errors,
    }
    run_state.write_results_json_stream(output_root / "results.json", payload, result_paths)
    run_state.remove_legacy_summary_csvs(output_root)
    runtime_manifest = {
        "container_cleanup": cancellation_token.cleanup_reports,
        "docker_startup": docker_startup,
        "terminal_status": payload["status"],
        "runtime_metrics": {
            "schema_version": 1,
            "path": str(output_root / "runtime-metrics.json"),
        },
        "result_sink": result_sink.to_meta(),
        "verifier_transport": (verifier_worker.to_meta() if verifier_worker is not None
                               else {"mode": "isolated"}),
        "verifier_grounded_release": (
            verifier_release_config.identity if verifier_release_config is not None else None
        ),
        "verifier_validation_cache": (
            vgb_validation_cache.to_meta() if vgb_validation_cache is not None else None
        ),
        "execution_plan": {
            "mode": service.SCHEDULING_MODE,
            "attempt_queue": {"unit": "attempt", "group_order": "round_robin", "record_order": "fifo", "scoring_workers": 1},
            "max_concurrent_groups": args.max_concurrent_groups,
            "max_concurrent_attempts": getattr(args, "max_concurrent_attempts", 2),
            "inter_wave_delay_seconds": args.inter_wave_delay_seconds,
            "waves": group_waves,
        },
        "aggregate_groups": aggregate_group_ids,
        "run_groups": group_ids,
        "merge_existing_per_record": args.merge_existing_per_record,
        "skill_routing_inventory": {
            "path": str(output_root / "skill-routing-inventory.json"),
            "health_check_applied": False,
            "sha256": skill_routing_inventory.get("inventory_sha256", ""),
        },
        "convergence_policy": convergence_policy_meta,
        "single_timeout_retry": {
            "max_retries": single_timeout_retries,
            "backoff_seconds": list(single_timeout_retry_backoff_seconds),
        },
        "timeout_mode": timeout_mode,
        "container_runtime": {
            "backend": getattr(args, "execution_backend", "docker"),
            "image": getattr(args, "container_image", "openclaw-benchmark-single-llm:latest"),
            "network_mode": args.container_network.network_mode if getattr(args, "container_network", None) else "host",
            "network": args.container_network.to_meta() if getattr(args, "container_network", None) else {},
            "resource_limits": {
                "cpus": getattr(args, "container_cpus", None),
                "memory_bytes": getattr(args, "container_memory_bytes", None),
                "pids": getattr(args, "container_pids_limit", None),
            },
            "spool_path": "<attempt-workspace>/scratch/outputs/container-spool",
        },
        "workspace_isolation": {
            "schema_version": 3,
            "run_id": run_id,
            "invocation_id": invocation_id,
            "runtime_runs_root": str(workspace_manager.runtime_root),
            "runtime_workspace_root": str(workspace_manager.invocation_runtime_root),
            "archive_root": str(workspace_manager.archive_root),
            "quarantine_root": str(workspace_manager.quarantine_root),
            "templates": workspace_manager.template_manifest(),
            "startup_recovery": workspace_startup_recovery,
            "scratch_contract_version": 2,
            "security_boundary": "runtime_guard_and_transcript_audit_not_os_sandbox",
            "forbidden_path_policy": workspace_manager.forbidden_path_policy_manifest(),
            "access_policies": [workspace_policies[key] for key in sorted(workspace_policies)],
        },
        "groups": {
            group_id: {
                "group": asdict(catalog.EXPERIMENT_GROUPS[group_id]),
                "config_path": str(config_pool.config_for_group(catalog.EXPERIMENT_GROUPS[group_id])),
                "effective_skill_allowlist": list(effective_experiment_specs[group_id].skill_allowlist or ()),
                **service.group_metadata(group_id, args),
                "single_agent": (
                    effective_experiment_specs[group_id].resolve_single_agent_id(args.single_agent_id_override)
                    if group_id in effective_experiment_specs and catalog.EXPERIMENT_GROUPS[group_id].runner == "single_llm"
                    else None
                ),
                "single_agent_model": args.single_agent_model,
                "single_agent_thinking": (
                    args.single_agent_thinking if catalog.EXPERIMENT_GROUPS[group_id].runner == "single_llm" else None
                ),
                "no_timeout": bool(getattr(args, "no_timeout", False)) if catalog.EXPERIMENT_GROUPS[group_id].runner == "single_llm" else None,
                "selected_record_count": len(records),
                "pending_record_count": len(pending_records_by_group[group_id]),
                "skipped_existing_record_count": len(records) - len(pending_records_by_group[group_id]),
            }
            for group_id in group_ids
        },
        "judge": ({
            "agent": args.judge_agent,
            "model": args.judge_model,
            "thinking": args.judge_agent_thinking,
            "config_path": str(config_pool.judge_config_path()),
        } if service.USES_JUDGE else None),
    }
    run_state.save_json(output_root / "runtime-manifest.json", runtime_manifest)
    if cancellation_token.is_cancelled:
        automated_evaluation_status = run_state.automated_evaluation_skipped(output_root)
        automated_evaluation_status["reason"] = "run_cancelled"
        run_state.save_json(Path(automated_evaluation_status["status_path"]), automated_evaluation_status)
        runtime_manifest["cancellation"] = {
            **(cancellation_token.reason.to_payload() if cancellation_token.reason is not None else {}),
            "request_count": cancellation_token.request_count,
            "errors": cancellation_errors,
        }
    elif bool(getattr(args, "no_analysis", False)):
        automated_evaluation_status = run_state.automated_evaluation_skipped(output_root)
        run_state.save_json(Path(automated_evaluation_status["status_path"]), automated_evaluation_status)
    else:
        try:
            automated_evaluation_status = launch_automated_evaluation(output_root)
        except Exception as exc:
            automated_evaluation_status = run_state.automated_evaluation_launch_failed(output_root, exc)
            run_state.save_json(Path(automated_evaluation_status["status_path"]), automated_evaluation_status)
    runtime_manifest["automated_evaluation"] = automated_evaluation_status
    run_state.save_json(output_root / "runtime-manifest.json", runtime_manifest)
    if cancellation_token.is_cancelled:
        progress_writer.run_cancelled(errors=cancellation_errors)
    else:
        progress_writer.run_completed(status="completed")
    print(json.dumps({"output_dir": str(output_root), "summary": summary}, indent=2, ensure_ascii=False))
    return 130 if cancellation_token.is_cancelled else 0


def main(service=None) -> int:
    runtime_metrics = start_runtime_metrics()
    try:
        with ExitStack() as resources:
            return _run_main(service, runtime_metrics=runtime_metrics, resources=resources)
    finally:
        snapshot = finish_runtime_metrics(runtime_metrics)
        if runtime_metrics.output_root is not None:
            # Metrics are finalized before this write so the artifact never
            # counts itself or creates a recursive byte total.
            atomic_write_json(runtime_metrics.output_root / "runtime-metrics.json", snapshot)


if __name__ == "__main__":
    raise SystemExit(main())
