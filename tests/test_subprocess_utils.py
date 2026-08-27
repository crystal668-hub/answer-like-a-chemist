from __future__ import annotations

from benchmarking.runtime.subprocess_utils import summarize_payloads


def test_summarize_payloads_filters_marked_error_payloads() -> None:
    payloads = [
        {"text": "FINAL ANSWER: 0.055"},
        {"text": "tool failed", "isError": True},
    ]

    assert summarize_payloads(payloads) == "FINAL ANSWER: 0.055"


def test_summarize_payloads_filters_openclaw_auto_tool_error_without_marker() -> None:
    payloads = [
        {"text": "FINAL ANSWER: 0.055"},
        {
            "text": (
                "⚠️ 🛠️ `cd \"$BENCHMARK_SKILL_SCRATCH_DIR\" && command -v xtb && "
                "command -v obabel (in $BENCHMARK_SKILL_SCRATCH_DIR)` failed"
            )
        },
    ]

    assert summarize_payloads(payloads) == "FINAL ANSWER: 0.055"


def test_summarize_payloads_keeps_regular_failure_text() -> None:
    payloads = [
        {"text": "The calculation failed to converge, so I report no value."},
    ]

    assert summarize_payloads(payloads) == payloads[0]["text"]
