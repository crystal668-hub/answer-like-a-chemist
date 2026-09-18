from __future__ import annotations

from typing import Any


def normalize_run_status_value(status: Any) -> str:
    return str(getattr(status, "value", status) or "").strip()


def build_result_axes_from_runner(run_result: Any) -> dict[str, Any]:
    status = getattr(run_result, "status", None)
    normalized_status = normalize_run_status_value(status)
    runner_meta = getattr(run_result, "runner_meta", None) or {}
    raw = getattr(run_result, "raw", None) or {}
    recovery = getattr(run_result, "recovery", None)
    workspace_isolation = runner_meta.get("workspace_isolation")
    workspace_isolation = workspace_isolation if isinstance(workspace_isolation, dict) else {}
    boundary_degraded = workspace_isolation.get("adjudication") == "scoreable_degraded"
    scored = bool(run_result.should_score())
    run_lifecycle_status = "completed" if normalized_status in {"completed", "recovered"} else "failed"

    terminal_state = runner_meta.get("terminal_state")
    if terminal_state == "completed":
        protocol_completion_status = "completed"
    elif raw.get("run_status") is not None:
        protocol_completion_status = "failed"
    else:
        protocol_completion_status = "missing"

    axes: dict[str, Any] = {
        "schema_version": 5,
        "run_lifecycle_status": run_lifecycle_status,
        "protocol_completion_status": protocol_completion_status,
        "protocol_acceptance_status": runner_meta.get("acceptance_status"),
    }

    if recovery is not None:
        recovery_mode = str(getattr(recovery, "recovery_mode", "") or "none")
        axes.update(
            answer_availability=(
                "preview_only"
                if recovery_mode == "run-status-final-answer-preview"
                else "recovered_candidate"
            ),
            answer_reliability=str(getattr(recovery, "reliability", "") or "none"),
            evaluable=bool(getattr(recovery, "evaluable", False)),
            scored=scored,
            recovery_mode=recovery_mode,
            degraded_execution=True,
        )
    else:
        status_is_completed = normalized_status == "completed"
        if normalized_status == "recovered":
            fallback_source = str(runner_meta.get("fallback_source") or "")
            answer_availability = (
                "preview_only"
                if fallback_source == "run-status-final-answer-preview"
                else "recovered_candidate"
            )
            answer_reliability = str(runner_meta.get("answer_reliability") or "").strip() or (
                "low_confidence_recovered"
                if fallback_source == "run-status-final-answer-preview"
                else "high_confidence_recovered"
            )
            axes.update(
                answer_availability=answer_availability,
                answer_reliability=answer_reliability,
                evaluable=False,
                scored=False,
                recovery_mode=str(runner_meta.get("recovery_mode") or fallback_source or "none"),
                degraded_execution=True,
            )
        elif status_is_completed:
            axes.update(
                answer_availability="native_final",
                answer_reliability="native",
                evaluable=scored,
                scored=scored,
                recovery_mode="none",
                degraded_execution=boundary_degraded or bool(runner_meta.get("degraded_execution")),
            )
        else:
            axes.update(
                answer_availability="missing",
                answer_reliability="none",
                evaluable=False,
                scored=False,
                recovery_mode="none",
                degraded_execution=True,
            )

    axes["execution_error_kind"] = None if axes["scored"] else "execution_error"
    return axes
