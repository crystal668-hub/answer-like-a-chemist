from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarking.core.convergence import (
    ConvergencePolicy,
    extract_latest_complete_answer_from_transcript,
    is_complete_benchmark_answer,
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


if __name__ == "__main__":
    unittest.main()
