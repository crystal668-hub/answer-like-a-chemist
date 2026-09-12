from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class AttemptTerminalStatus(StrEnum):
    COMPLETED = "completed"
    RECOVERED = "recovered"
    FAILED = "failed"


class AttemptAnswerSource(StrEnum):
    NATIVE = "native"
    TRANSCRIPT = "transcript"
    RESCUE = "rescue"
    NONE = "none"


class AttemptRetryDecision(StrEnum):
    NO_RETRY = "no_retry"
    RETRY = "retry"


@dataclass(frozen=True)
class AttemptEvidence:
    native_result_status: str
    native_payload_complete: bool
    transcript_complete_answer: bool
    finalization_rescue_complete: bool
    current_process_exit: int | None
    current_failure_code: str
    current_failure_retryable: bool
    historical_prompt_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttemptOutcome:
    terminal_status: AttemptTerminalStatus
    answer_source: AttemptAnswerSource
    retry_decision: AttemptRetryDecision
    retry_reason: str
    current_failure_code: str
    historical_prompt_errors: tuple[str, ...]

    def to_meta(self) -> dict[str, object]:
        return asdict(self)


def determine_attempt_outcome(evidence: AttemptEvidence) -> AttemptOutcome:
    if evidence.native_payload_complete:
        return _answered_outcome(evidence, AttemptTerminalStatus.COMPLETED, AttemptAnswerSource.NATIVE)
    if evidence.transcript_complete_answer:
        return _answered_outcome(evidence, AttemptTerminalStatus.RECOVERED, AttemptAnswerSource.TRANSCRIPT)
    if evidence.finalization_rescue_complete:
        return _answered_outcome(evidence, AttemptTerminalStatus.RECOVERED, AttemptAnswerSource.RESCUE)
    retry = evidence.current_failure_retryable and bool(evidence.current_failure_code)
    return AttemptOutcome(
        terminal_status=AttemptTerminalStatus.FAILED,
        answer_source=AttemptAnswerSource.NONE,
        retry_decision=AttemptRetryDecision.RETRY if retry else AttemptRetryDecision.NO_RETRY,
        retry_reason=evidence.current_failure_code if retry else "",
        current_failure_code=evidence.current_failure_code,
        historical_prompt_errors=evidence.historical_prompt_errors,
    )


def _answered_outcome(
    evidence: AttemptEvidence,
    status: AttemptTerminalStatus,
    source: AttemptAnswerSource,
) -> AttemptOutcome:
    return AttemptOutcome(
        terminal_status=status,
        answer_source=source,
        retry_decision=AttemptRetryDecision.NO_RETRY,
        retry_reason="",
        current_failure_code=evidence.current_failure_code,
        historical_prompt_errors=evidence.historical_prompt_errors,
    )
