from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from benchmarking.core.answer_processing import (
    extract_candidate_short_answer,
    normalize_answer_tracks,
)
from benchmarking.core.datasets import load_records
from benchmarking.core.reporting import GroupRecordResult, aggregate_results
from benchmarking.runtime.agent_workspace import (
    AttemptWorkspaceManager,
    WorkspaceTemplate,
)
from benchmarking.runtime.workspace_policy import ProtectedRoot
from benchmarking.scoring.registry import evaluate_record, register_default_evaluators


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _git_commit(project_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _protected_roots(runtime_manifest: dict[str, Any]) -> tuple[ProtectedRoot, ...]:
    isolation = runtime_manifest.get("workspace_isolation")
    isolation = isolation if isinstance(isolation, dict) else {}
    policy = isolation.get("forbidden_path_policy")
    policy = policy if isinstance(policy, dict) else {}
    roots = []
    for item in policy.get("protected_roots") or []:
        if not isinstance(item, dict):
            continue
        roots.append(
            ProtectedRoot(
                policy_id=str(item.get("policy_id") or ""),
                path=Path(str(item.get("path") or "")),
                source=str(item.get("source") or "historical.runtime_manifest"),
            )
        )
    return tuple(roots)


def _dry_run_manifest_from_record(run_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    runner_meta = payload.get("runner_meta")
    runner_meta = runner_meta if isinstance(runner_meta, dict) else {}
    isolation = runner_meta.get("workspace_isolation")
    isolation = isolation if isinstance(isolation, dict) else {}
    policy = isolation.get("policy")
    policy = policy if isinstance(policy, dict) else {}
    protected_roots = policy.get("protected_roots")
    if not isinstance(protected_roots, list) or not protected_roots:
        raise FileNotFoundError(
            "Historical dry-run requires runtime-manifest.json or a persisted per-record workspace policy."
        )
    return {
        "workspace_isolation": {
            "run_id": run_root.name,
            "invocation_id": str(isolation.get("invocation_id") or "historical-replay"),
            "runtime_runs_root": str(run_root / ".history-runtime"),
            "forbidden_path_policy": {"protected_roots": protected_roots},
        }
    }


def _historical_environment(payload: dict[str, Any]) -> dict[str, str]:
    runner_meta = payload.get("runner_meta")
    runner_meta = runner_meta if isinstance(runner_meta, dict) else {}
    scratch = runner_meta.get("workspace_scratch") or runner_meta.get("skill_scratch")
    scratch = scratch if isinstance(scratch, dict) else {}
    environment = {
        "BENCHMARK_WORKSPACE_DIR": str(scratch.get("workspace_dir") or ""),
        "BENCHMARK_SKILL_SCRATCH_DIR": str(scratch.get("scratch_dir") or ""),
        "BENCHMARK_SKILL_REQUEST_DIR": str(scratch.get("request_dir") or ""),
        "BENCHMARK_SKILL_OUTPUT_DIR": str(scratch.get("output_dir") or ""),
        "BENCHMARK_SKILL_NOTES_DIR": str(scratch.get("notes_dir") or ""),
    }
    return {key: value for key, value in environment.items() if value}


def _record_path(run_root: Path, group_id: str, record_id: str) -> Path:
    group_root = run_root / "per-record" / group_id
    direct = group_root / f"{record_id.replace('_', '-')}.json"
    if direct.is_file():
        return direct
    for candidate in group_root.glob("*.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(payload.get("record_id") or "") == record_id:
            return candidate
    raise FileNotFoundError(f"No per-record payload for {group_id}/{record_id}")


def _transcript_answer_text(path: Path) -> str:
    candidates: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw_line in lines:
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict) or str(message.get("role") or "").lower() != "assistant":
            continue
        text = "\n".join(
            str(item.get("text") or "")
            for item in message.get("content") or []
            if isinstance(item, dict) and item.get("type") == "text"
        )
        if "FINAL ANSWER:" in text:
            candidates.append(text)
    return candidates[-1] if candidates else ""


def _historical_answer_tracks(payload: dict[str, Any]) -> tuple[str, str, str]:
    short_text, full_text = normalize_answer_tracks(
        short_answer_text=str(payload.get("short_answer_text") or ""),
        full_response_text=str(payload.get("full_response_text") or payload.get("answer_text") or ""),
    )
    if short_text or full_text:
        return short_text, full_text, "record"

    runner_meta = payload.get("runner_meta")
    runner_meta = runner_meta if isinstance(runner_meta, dict) else {}
    for key in ("finalAssistantVisibleText", "finalAssistantRawText"):
        candidate = str(runner_meta.get(key) or "").strip()
        if candidate:
            short_text, full_text = normalize_answer_tracks(full_response_text=candidate)
            if short_text:
                return short_text, full_text, f"runner_meta.{key}"

    session_isolation = runner_meta.get("session_isolation")
    session_isolation = session_isolation if isinstance(session_isolation, dict) else {}
    transcript_path = Path(str(session_isolation.get("postflight_entry_session_file") or "")).expanduser()
    transcript_text = _transcript_answer_text(transcript_path) if transcript_path.is_file() else ""
    if transcript_text:
        short_text = extract_candidate_short_answer(transcript_text)
        return short_text, transcript_text, "session_transcript"
    return "", "", "none"


def _all_per_record_payloads(run_root: Path) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in sorted((run_root / "per-record").glob("*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not payload.get("record_id"):
            continue
        if not payload.get("group_id"):
            payload = {**payload, "group_id": path.parent.name}
        payloads.append(payload)
    return payloads


def _historical_review(audit_payload: dict[str, Any]) -> dict[str, Any]:
    findings = audit_payload.get("findings") or []
    path_findings = [item for item in findings if item.get("rule_id") == "protected_path_access"]
    write_only = bool(path_findings) and all(
        item.get("access_mode") in {"write", "mutate"} for item in path_findings
    )
    no_external_read = all(item.get("information_exposure") == "none" for item in path_findings)
    successful_writes = [
        item
        for item in path_findings
        if item.get("access_mode") in {"write", "mutate"}
        and item.get("operation_outcome") == "succeeded"
    ]
    return {
        "write_only": write_only,
        "no_external_read_evidence": no_external_read,
        "successful_write_count": len(successful_writes),
        "ownership_conclusion": (
            "candidate_current_attempt_generated_manual_review_required"
            if write_only and no_external_read and successful_writes
            else "not_established"
        ),
        "apply_eligible_with_explicit_approval": bool(write_only and no_external_read and successful_writes),
    }


def _score_record(
    payload: dict[str, Any],
    *,
    evaluator: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if evaluator is not None:
        evaluation = evaluator(payload)
        return evaluation, {"kind": "injected_test_evaluator"}
    source_file = Path(str(payload.get("source_file") or "")).expanduser()
    records = {record.record_id: record for record in load_records([source_file])}
    record_id = str(payload.get("record_id") or "")
    record = records[record_id]
    register_default_evaluators()
    evaluation = evaluate_record(
        record,
        short_answer_text=str(payload.get("short_answer_text") or ""),
        full_response_text=str(payload.get("full_response_text") or payload.get("answer_text") or ""),
        answer_text=str(payload.get("answer_text") or ""),
        judge=None,
    )
    result = asdict(evaluation)
    details = result.get("details") or {}
    return result, {
        "kind": "project_evaluator_registry",
        "eval_kind": record.eval_kind,
        "method": details.get("method"),
        "versions": details.get("versions") or {},
        "task_id": details.get("task_id"),
    }


def replay_workspace_adjudication(
    *,
    run_root: Path,
    group_id: str,
    record_ids: tuple[str, ...] | list[str],
    apply: bool = False,
    rescore: bool = False,
    approve_historical_ownership: bool = False,
    manual_approve_record_ids: tuple[str, ...] | list[str] = (),
    manual_approval_reason: str = "",
    evaluator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    requested_record_ids = {str(record_id) for record_id in record_ids}
    manual_approval_ids = {str(record_id) for record_id in manual_approve_record_ids}
    unknown_manual_ids = manual_approval_ids - requested_record_ids
    if unknown_manual_ids:
        raise ValueError(
            "Manual approval record IDs must also be selected for replay: "
            + ", ".join(sorted(unknown_manual_ids))
        )
    manual_approval_reason = manual_approval_reason.strip()
    if manual_approval_ids and not manual_approval_reason:
        raise ValueError("Manual approval requires a non-empty reason.")
    operation_stamp = _timestamp()
    report_path = (
        run_root
        / "recovery"
        / f"workspace-adjudication-replay-{operation_stamp}-{uuid.uuid4().hex[:8]}.json"
    )
    project_root = Path(__file__).resolve().parents[2]
    runtime_manifest_path = run_root / "runtime-manifest.json"
    results_path = run_root / "results.json"
    progress_path = run_root / "progress" / "state.json"
    if apply and not runtime_manifest_path.is_file():
        raise FileNotFoundError("Historical apply requires runtime-manifest.json.")
    first_record_path = _record_path(run_root, group_id, str(record_ids[0]))
    first_record_payload = json.loads(first_record_path.read_text(encoding="utf-8"))
    runtime_manifest = (
        json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
        if runtime_manifest_path.is_file()
        else _dry_run_manifest_from_record(run_root, first_record_payload)
    )
    results_reconstructed = not results_path.is_file()
    results_payload = (
        json.loads(results_path.read_text(encoding="utf-8"))
        if results_path.is_file()
        else {"schema_version": 3, "results": _all_per_record_payloads(run_root), "summary": {}}
    )
    isolation_manifest = runtime_manifest.get("workspace_isolation") or {}
    protected_roots = _protected_roots(runtime_manifest)
    manager = AttemptWorkspaceManager(
        runtime_root=Path(str(isolation_manifest.get("runtime_runs_root") or run_root / ".history-runtime")),
        output_root=run_root,
        run_id=str(isolation_manifest.get("run_id") or run_root.name),
        invocation_id=str(isolation_manifest.get("invocation_id") or "historical-replay"),
        templates={"history": WorkspaceTemplate(template_id="history")},
        protected_roots=protected_roots,
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "apply" if apply else "dry_run",
        "run_root": str(run_root),
        "group_id": group_id,
        "record_ids": list(record_ids),
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "git_commit": _git_commit(project_root),
        "model_calls": 0,
        "results_reconstructed": results_reconstructed,
        "manual_approval_record_ids": sorted(manual_approval_ids),
        "records": [],
    }
    replacements: dict[str, dict[str, Any]] = {}
    for record_id in record_ids:
        record_path = _record_path(run_root, group_id, record_id)
        payload = json.loads(record_path.read_text(encoding="utf-8"))
        recovered_short_text, recovered_full_text, answer_source = _historical_answer_tracks(payload)
        scoring_payload = dict(payload)
        if recovered_short_text or recovered_full_text:
            scoring_payload.update(
                {
                    "answer_text": recovered_full_text,
                    "short_answer_text": recovered_short_text,
                    "full_response_text": recovered_full_text,
                }
            )
        runner_meta = payload.get("runner_meta")
        runner_meta = runner_meta if isinstance(runner_meta, dict) else {}
        old_isolation = runner_meta.get("workspace_isolation")
        old_isolation = old_isolation if isinstance(old_isolation, dict) else {}
        active_workspace = Path(str(old_isolation.get("active_workspace") or ""))
        bundle = runner_meta.get("runtime_bundle")
        bundle = bundle if isinstance(bundle, dict) else {}
        bundle_dir = str(bundle.get("bundle_dir") or "").strip()
        skills_enabled = bool(payload.get("skills_enabled"))
        historical_environment = _historical_environment(payload)
        historical_scratch_text = historical_environment.get("BENCHMARK_SKILL_SCRATCH_DIR", "")
        historical_scratch = [Path(historical_scratch_text)] if historical_scratch_text else []
        skill_scopes = (
            project_root / "skills",
            project_root / "scripts" / "run_skill.py",
        ) if skills_enabled else ()
        policy = manager.policy_for_lease(
            SimpleNamespace(active_workspace=active_workspace),
            role="single_llm",
            skills_enabled=skills_enabled,
            always_read_scopes=([Path(bundle_dir)] if bundle_dir else []),
            read_scopes=skill_scopes,
            write_scopes=historical_scratch,
            exec_workdir_scopes=historical_scratch,
        )
        audit = manager.audit_attempt(
            SimpleNamespace(active_workspace=active_workspace, identity=SimpleNamespace(runner_kind="single_llm")),
            runner_meta,
            environment=historical_environment,
            policy=policy,
        )
        audit_payload = audit.to_payload()
        review = _historical_review(audit_payload)
        transcript_path = Path(
            str((runner_meta.get("session_isolation") or {}).get("postflight_entry_session_file") or "")
        ).expanduser()
        hashes = {
            "per_record": _sha256(record_path),
        }
        if results_path.is_file():
            hashes["results"] = _sha256(results_path)
        if runtime_manifest_path.is_file():
            hashes["runtime_manifest"] = _sha256(runtime_manifest_path)
        if transcript_path.is_file():
            hashes["transcript"] = _sha256(transcript_path)
        scorer_identity: dict[str, Any] = {}
        new_evaluation = payload.get("evaluation") or {}
        manual_approval_requested = record_id in manual_approval_ids
        manual_approval_eligible = bool(
            manual_approval_requested
            and audit.contamination_status != "confirmed"
            and (recovered_short_text or recovered_full_text)
        )
        effective_scoreable = audit.adjudication in {"scoreable", "scoreable_degraded"} or manual_approval_eligible
        if rescore and effective_scoreable:
            if not recovered_short_text and not recovered_full_text:
                raise RuntimeError(f"Record `{record_id}` has no recoverable historical answer.")
            new_evaluation, scorer_identity = _score_record(scoring_payload, evaluator=evaluator)
        eligible = audit.adjudication in {"scoreable", "scoreable_degraded"}
        if audit.adjudication == "scoreable_degraded":
            eligible = eligible and review["apply_eligible_with_explicit_approval"]
        eligible = eligible or manual_approval_eligible
        manual_adjudication = {
            "requested": manual_approval_requested,
            "eligible": manual_approval_eligible,
            "approved_by": "user_explicit_instruction" if manual_approval_requested else None,
            "reason": manual_approval_reason if manual_approval_requested else None,
            "original_audit_execution_status": audit.audit_execution_status,
            "original_contamination_status": audit.contamination_status,
            "original_adjudication": audit.adjudication,
            "original_error": payload.get("error"),
            "original_execution_error_kind": payload.get("execution_error_kind"),
        }
        record_report = {
            "record_id": record_id,
            "source_hashes": hashes,
            "policy_digest": policy.digest,
            "audit": audit_payload,
            "historical_review": review,
            "answer_recovery": {
                "source": answer_source,
                "available": bool(recovered_short_text or recovered_full_text),
                "short_answer_text": recovered_short_text,
            },
            "apply_eligible": eligible,
            "manual_adjudication": manual_adjudication,
            "scorer_identity": scorer_identity,
        }
        report["records"].append(record_report)
        if apply:
            if not eligible:
                raise RuntimeError(f"Record `{record_id}` is not eligible for historical recovery.")
            if audit.adjudication == "scoreable_degraded" and not approve_historical_ownership:
                raise RuntimeError(
                    f"Record `{record_id}` requires explicit historical ownership approval before apply."
                )
            updated = dict(payload)
            updated["schema_version"] = 3
            if recovered_short_text or recovered_full_text:
                updated["answer_text"] = recovered_full_text
                updated["short_answer_text"] = recovered_short_text
                updated["full_response_text"] = recovered_full_text
                if answer_source != "record":
                    updated["answer_availability"] = "recovered_candidate"
                    updated["answer_reliability"] = "high_confidence_recovered"
                    updated["recovery_mode"] = "archived_final_answer"
            updated["evaluation"] = new_evaluation
            updated["evaluable"] = True
            updated["scored"] = True
            updated["degraded_execution"] = bool(payload.get("degraded_execution")) or (
                audit.adjudication == "scoreable_degraded" or manual_approval_eligible
            )
            updated["execution_error_kind"] = None
            updated["error"] = None
            if manual_approval_requested:
                updated["manual_adjudication"] = manual_adjudication
            updated_runner_meta = dict(runner_meta)
            updated_runner_meta["workspace_isolation"] = {
                **old_isolation,
                **audit_payload,
                "policy_digest": policy.digest,
                "policy": policy.to_payload(),
                "cleanup": {
                    "attempted_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "historical_replay": True,
                },
                "historical_review": review,
            }
            if manual_approval_requested:
                updated_runner_meta["workspace_isolation"]["manual_adjudication"] = manual_adjudication
            updated_runner_meta["degraded_execution"] = updated["degraded_execution"]
            updated["runner_meta"] = updated_runner_meta
            replacements[record_id] = updated

    if apply:
        stamp = operation_stamp
        snapshot = run_root / "recovery" / f"workspace-adjudication-snapshot-{stamp}"
        snapshot.mkdir(parents=True, exist_ok=False)
        if results_path.is_file():
            shutil.copy2(results_path, snapshot / "results.json")
        else:
            _atomic_json(snapshot / "results.json", results_payload)
        shutil.copy2(runtime_manifest_path, snapshot / "runtime-manifest.json")
        if progress_path.is_file():
            (snapshot / "progress").mkdir()
            shutil.copy2(progress_path, snapshot / "progress" / "state.json")
        for record_id in record_ids:
            source = _record_path(run_root, group_id, record_id)
            destination = snapshot / "per-record" / group_id / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for record_id, updated in replacements.items():
            _atomic_json(
                _record_path(run_root, group_id, record_id),
                updated,
            )
        result_entries = []
        for item in results_payload.get("results") or []:
            if item.get("group_id") == group_id and item.get("record_id") in replacements:
                result_entries.append(replacements[str(item["record_id"])])
            else:
                result_entries.append(item)
        existing_result_keys = {
            (str(item.get("group_id") or ""), str(item.get("record_id") or ""))
            for item in result_entries
            if isinstance(item, dict)
        }
        for record_id, replacement in replacements.items():
            result_key = (group_id, record_id)
            if result_key not in existing_result_keys:
                result_entries.append(replacement)
        results_payload["schema_version"] = 3
        results_payload["results"] = result_entries
        results_payload["errors"] = [
            item
            for item in (results_payload.get("errors") or [])
            if not isinstance(item, dict)
            or item.get("group_id") != group_id
            or item.get("record_id") not in record_ids
        ]
        field_names = {item.name for item in fields(GroupRecordResult)}
        normalized_results = [
            GroupRecordResult(**{key: value for key, value in item.items() if key in field_names})
            for item in result_entries
        ]
        results_payload["summary"] = aggregate_results(normalized_results)
        results_payload["workspace_adjudication_recovery"] = {
            "report": str(report_path.relative_to(run_root)),
            "snapshot": str(snapshot.relative_to(run_root)),
            "model_calls": 0,
            "results_reconstructed": results_reconstructed,
        }
        _atomic_json(results_path, results_payload)
        if progress_path.is_file():
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            progress["workspace_adjudication_recovery"] = results_payload["workspace_adjudication_recovery"]
            group_progress = progress.get("groups")
            group_progress = group_progress if isinstance(group_progress, dict) else {}
            selected_group = group_progress.get(group_id)
            if isinstance(selected_group, dict):
                completed_records = list(selected_group.get("completed_records") or [])
                for record_id in record_ids:
                    if record_id not in completed_records:
                        completed_records.append(record_id)
                selected_group["completed_records"] = completed_records
                selected_group["completed_count"] = len(completed_records)
                selected_group["errors"] = [
                    item
                    for item in (selected_group.get("errors") or [])
                    if not isinstance(item, dict) or item.get("record_id") not in record_ids
                ]
                selected_group["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            progress["completed"] = sum(
                int(item.get("completed_count") or 0)
                for item in group_progress.values()
                if isinstance(item, dict)
            )
            progress["errors"] = [
                item
                for item in (progress.get("errors") or [])
                if not isinstance(item, dict)
                or item.get("group_id") != group_id
                or item.get("record_id") not in record_ids
            ]
            progress["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            _atomic_json(progress_path, progress)
        report["snapshot"] = str(snapshot)

    report["report_path"] = str(report_path)
    _atomic_json(report_path, report)
    return report
