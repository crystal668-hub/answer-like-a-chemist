from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarking.core.convergence import (
    ConvergencePolicy,
    extract_latest_complete_answer_from_transcript,
    extract_latest_complete_answer_from_transcript_for_eval,
    is_complete_benchmark_answer,
    is_complete_rescue_answer,
    summarize_transcript_convergence,
)


class BenchmarkConvergenceTests(unittest.TestCase):
    def test_policy_serializes_to_metadata(self) -> None:
        policy = ConvergencePolicy(timeout_seconds=900, finalization_grace_seconds=60)

        self.assertEqual(
            {
                "timeout_seconds": 900,
                "stop_fraction": 0.2,
                "finalization_grace_seconds": 60,
                "max_unchanged_status_polls": 2,
                "max_recovery_attempts": 2,
            },
            policy.to_meta(),
        )

    def test_transcript_summary_counts_tool_calls_and_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session"}),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "user",
                                    "content": [{"type": "text", "text": "Q"}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "toolCall", "name": "read"}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "toolResult",
                                    "toolName": "read",
                                    "content": [{"type": "text", "text": "ok"}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "FINAL ANSWER: 42"}],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_transcript_convergence(transcript)

        self.assertEqual(1, summary["tool_call_count"])
        self.assertEqual(2, summary["assistant_turn_count"])
        self.assertEqual(["read"], summary["tool_names"])
        self.assertEqual(0, summary["prompt_error_count"])
        self.assertEqual("", summary["latest_prompt_error"])
        self.assertFalse(summary["latest_prompt_error_is_timeout"])
        self.assertEqual(0, summary["missing_skill_doc_read_count"])
        self.assertEqual(0, summary["tool_result_error_count"])
        self.assertEqual(0, summary["request_shape_error_count"])
        self.assertFalse(summary["coverage_checklist_present"])

    def test_transcript_summary_detects_tool_misuse_and_checklist_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "Coverage checklist:\n- todo: equation\n- done: units\n- blocked: source",
                                        },
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "toolResult",
                                    "toolName": "read",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "ENOENT: no such file or directory, access '/workspace/skills/benchmark-solving-protocol/SKILL.md'",
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "toolResult",
                                    "toolName": "exec",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "usage: canonicalize.py [-h] --request-json REQUEST_JSON --output-dir OUTPUT_DIR",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_transcript_convergence(transcript)

        self.assertTrue(summary["coverage_checklist_present"])
        self.assertEqual(1, summary["missing_skill_doc_read_count"])
        self.assertEqual(2, summary["tool_result_error_count"])
        self.assertEqual(1, summary["request_shape_error_count"])

    def test_transcript_summary_ignores_request_contract_text_from_read_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "toolResult",
                            "toolName": "read",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "# RDKit Skill Contracts\n"
                                        "Every script supports --request-json REQUEST_JSON "
                                        "--output-dir OUTPUT_DIR --json.\n"
                                        "Failure modes include malformed JSON request and invalid input."
                                    ),
                                }
                            ],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_transcript_convergence(transcript)

        self.assertEqual(0, summary["tool_result_error_count"])
        self.assertEqual(0, summary["request_shape_error_count"])

    def test_transcript_summary_classifies_real_tool_errors_by_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "toolResult",
                                    "toolName": "exec",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": (
                                                "usage: descriptors.py [-h] --request-json REQUEST_JSON "
                                                "--output-dir OUTPUT_DIR\n"
                                                "descriptors.py: error: the following arguments are required: "
                                                "--request-json, --output-dir"
                                            ),
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "toolResult",
                                    "toolName": "exec",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": json.dumps(
                                                {
                                                    "status": "error",
                                                    "tool": "exec",
                                                    "error": (
                                                        "exec preflight: complex interpreter invocation detected; "
                                                        "refusing to run without script preflight validation."
                                                    ),
                                                }
                                            ),
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "toolResult",
                                    "toolName": "web_fetch",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": json.dumps(
                                                {
                                                    "status": "error",
                                                    "tool": "web_fetch",
                                                    "error": "Web fetch failed (403): forbidden",
                                                }
                                            ),
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "toolResult",
                                    "toolName": "exec",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": json.dumps(
                                                {
                                                    "status": "error",
                                                    "errors": [
                                                        {
                                                            "code": "invalid_request",
                                                            "message": "`operation` is required",
                                                        }
                                                    ],
                                                }
                                            ),
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_transcript_convergence(transcript)

        self.assertEqual(4, summary["tool_result_error_count"])
        self.assertEqual(3, summary["request_shape_error_count"])

    def test_transcript_summary_detects_timeout_prompt_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "custom",
                                "customType": "openclaw:prompt-error",
                                "data": {"error": "context deadline exceeded while waiting for model response"},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "custom",
                                "customType": "openclaw:prompt-error",
                                "data": {"error": "HTTP 504 gateway timeout from provider"},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_transcript_convergence(transcript)

        self.assertEqual(2, summary["prompt_error_count"])
        self.assertEqual("HTTP 504 gateway timeout from provider", summary["latest_prompt_error"])
        self.assertTrue(summary["latest_prompt_error_is_timeout"])

    def test_transcript_summary_ignores_non_timeout_prompt_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "custom",
                        "customType": "openclaw:prompt-error",
                        "data": {"error": "invalid_request_error: role ordering is invalid"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_transcript_convergence(transcript)

        self.assertEqual(1, summary["prompt_error_count"])
        self.assertEqual("invalid_request_error: role ordering is invalid", summary["latest_prompt_error"])
        self.assertFalse(summary["latest_prompt_error_is_timeout"])

    def test_extract_latest_complete_answer_from_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "draft"}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "Explanation: short\nAnswer: 273\nConfidence: 55%",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            answer = extract_latest_complete_answer_from_transcript(transcript)

        self.assertEqual("Explanation: short\nAnswer: 273\nConfidence: 55%", answer)

    def test_extract_latest_complete_answer_accepts_markdown_final_answer_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "draft\n**FINAL ANSWER:** A"}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "text", "text": "Reasoning\n**FINAL ANSWER: B**"}],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            answer = extract_latest_complete_answer_from_transcript(transcript)

        self.assertEqual("Reasoning\n**FINAL ANSWER: B**", answer)

    def test_empty_markdown_final_answer_marker_is_not_complete(self) -> None:
        self.assertFalse(is_complete_benchmark_answer("Reasoning\n**FINAL ANSWER:**"))

    def test_rescue_accepts_next_line_final_answer_for_research_only(self) -> None:
        text = "Visible derivation.\n**FINAL ANSWER:**\nA=11.3, B=100.0, C=7.9"

        self.assertFalse(is_complete_benchmark_answer(text))
        self.assertTrue(is_complete_rescue_answer(text, eval_kind="frontierscience_research"))
        self.assertFalse(is_complete_rescue_answer(text, eval_kind="superchem_multiple_choice_rpf"))

    def test_rescue_accepts_research_final_answer_heading(self) -> None:
        text = "Visible derivation.\n\n## FINAL ANSWER\nThe supported research answer covers the protocol and SAR."

        self.assertFalse(is_complete_benchmark_answer(text))
        self.assertTrue(is_complete_rescue_answer(text, eval_kind="frontierscience_research"))

    def test_rescue_accepts_research_conclusion_section(self) -> None:
        text = (
            "## Visible Derivation\n"
            "Evidence and calculations are summarized above.\n\n"
            "## Conclusion\n"
            "The supported conclusion is that the nitro analogue is favored because the "
            "substituent electronics and binding-site complementarity both improve activity."
        )

        self.assertFalse(is_complete_benchmark_answer(text))
        self.assertTrue(is_complete_rescue_answer(text, eval_kind="frontierscience_research"))

    def test_research_rescue_accepts_final_slash_conclusion_heading(self) -> None:
        text = (
            "## Visible derivation and checks\n"
            "Evidence and calculations are summarized above.\n\n"
            "## FINAL / CONCLUSION\n"
            "Meso-nitrogen modification changes the macrocycle electron count, aromaticity, spectra, and reactivity."
        )

        self.assertFalse(is_complete_benchmark_answer(text))
        self.assertTrue(is_complete_rescue_answer(text, eval_kind="frontierscience_research"))

    def test_research_rescue_accepts_final_and_conclusion_heading(self) -> None:
        text = (
            "## Visible derivation and checks\n"
            "Evidence and calculations are summarized above.\n\n"
            "## FINAL AND CONCLUSION\n"
            "The answer resolves the requested synthesis, spectra, and reactivity criteria."
        )

        self.assertFalse(is_complete_benchmark_answer(text))
        self.assertTrue(is_complete_rescue_answer(text, eval_kind="frontierscience_research"))

    def test_research_transcript_recovery_accepts_supported_conclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            text = (
                "## 1. Coverage checklist and fact ledger\n"
                "- done: cover the requested source-specific claims.\n\n"
                "## 9. Supported conclusion\n"
                "The supported conclusion covers the synthetic method, electron count, spectra, and reactivity."
            )
            transcript.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": text}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            answer = extract_latest_complete_answer_from_transcript_for_eval(
                transcript,
                eval_kind="frontierscience_research",
            )
            generic_answer = extract_latest_complete_answer_from_transcript(transcript)

        self.assertEqual(text, answer)
        self.assertEqual("", generic_answer)

    def test_rescue_rejects_empty_or_process_only_research_markers(self) -> None:
        self.assertFalse(is_complete_rescue_answer("Reasoning\nFINAL ANSWER:\n\n", eval_kind="frontierscience_research"))
        self.assertFalse(
            is_complete_rescue_answer(
                "FINAL ANSWER:\n\n## References\n1. Source paper\n\n**Coverage checklist:**\n- done: evidence",
                eval_kind="frontierscience_research",
            )
        )


if __name__ == "__main__":
    unittest.main()
