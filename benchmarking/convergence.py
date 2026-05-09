from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


FINAL_ANSWER_RE = re.compile(r"(?im)^\s*FINAL ANSWER\s*:\s*\S+")
HLE_ANSWER_RE = re.compile(r"(?ims)^\s*Explanation\s*:.+^\s*Answer\s*:.+^\s*Confidence\s*:\s*\S+")


@dataclass(frozen=True)
class ConvergencePolicy:
    timeout_seconds: int
    stop_fraction: float = 0.2
    finalization_grace_seconds: int = 90
    max_unchanged_status_polls: int = 2
    max_recovery_attempts: int = 2

    def to_meta(self) -> dict[str, Any]:
        return asdict(self)


def _iter_transcript_messages(transcript_path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if not transcript_path.is_file():
        return messages
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message")
        if isinstance(message, dict):
            messages.append(message)
    return messages


def _text_from_content(content: Any) -> str:
    parts: list[str] = []
    if not isinstance(content, list):
        return ""
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part).strip()


def summarize_transcript_convergence(transcript_path: Path) -> dict[str, Any]:
    assistant_turn_count = 0
    tool_call_count = 0
    tool_names: list[str] = []
    for message in _iter_transcript_messages(transcript_path):
        if message.get("role") == "assistant":
            assistant_turn_count += 1
            for item in message.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "toolCall":
                    tool_call_count += 1
                    name = str(item.get("name") or "")
                    if name:
                        tool_names.append(name)
    return {
        "transcript_path": str(transcript_path),
        "assistant_turn_count": assistant_turn_count,
        "tool_call_count": tool_call_count,
        "tool_names": tool_names,
    }


def is_complete_benchmark_answer(text: str) -> bool:
    candidate = str(text or "").strip()
    return bool(FINAL_ANSWER_RE.search(candidate) or HLE_ANSWER_RE.search(candidate))


def extract_latest_complete_answer_from_transcript(transcript_path: Path) -> str:
    for message in reversed(_iter_transcript_messages(transcript_path)):
        if message.get("role") != "assistant":
            continue
        text = _text_from_content(message.get("content"))
        if is_complete_benchmark_answer(text):
            return text
    return ""
