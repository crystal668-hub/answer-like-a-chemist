from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from .openclaw_env import build_openclaw_subprocess_env, proxy_environment_report


RunSubprocess = Callable[..., subprocess.CompletedProcess[str]]
PREFLIGHT_QUERY = "OpenAlex scholarly works API"


def _wrapper_path() -> Path:
    return Path(__file__).resolve().parent / "single_llm_openclaw_wrapper.py"


def _parse_jsonish(text: str) -> Any:
    stripped = str(text or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except Exception:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except Exception:
                return None
    return None


def _unwrap_agent_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result")
    return result if isinstance(result, dict) else payload


def _tool_details_from_message(message: dict[str, Any]) -> dict[str, Any]:
    details = message.get("details")
    if isinstance(details, dict):
        return details
    for item in message.get("content") or []:
        if not isinstance(item, dict):
            continue
        parsed = _parse_jsonish(str(item.get("text") or ""))
        if isinstance(parsed, dict):
            return parsed
    return {}


def _clean_external_text(text: str) -> str:
    cleaned = re.sub(r"<<<EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>", "", str(text or ""))
    cleaned = re.sub(r"<<<END_EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>", "", cleaned)
    lines = [line.strip() for line in cleaned.splitlines()]
    return " ".join(line for line in lines if line and line not in {"Source: Web Search", "---"}).strip()


def evaluate_web_search_transcript(transcript_path: Path) -> dict[str, Any]:
    path = Path(transcript_path).expanduser()
    if not path.is_file():
        return {"available": False, "error": f"transcript not found: {path}", "transcript_path": str(path)}

    latest: dict[str, Any] | None = None
    tool_is_error = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw_line)
        except Exception:
            continue
        message = event.get("message") if isinstance(event, dict) else None
        if not isinstance(message, dict):
            continue
        if message.get("role") != "toolResult" or message.get("toolName") != "web_search":
            continue
        latest = _tool_details_from_message(message)
        tool_is_error = bool(message.get("isError"))

    if latest is None:
        return {"available": False, "error": "web_search toolResult not found", "transcript_path": str(path)}

    status = str(latest.get("status") or "").strip().lower()
    explicit_error = str(latest.get("error") or "").strip()
    if tool_is_error or status == "error" or explicit_error:
        return {
            "available": False,
            "provider": str(latest.get("provider") or "duckduckgo"),
            "error": explicit_error or status or "web_search toolResult marked as error",
            "tool_is_error": tool_is_error,
            "transcript_path": str(path),
        }

    results = latest.get("results") if isinstance(latest.get("results"), list) else []
    count = int(latest.get("count") or len(results) or 0)
    if count <= 0 or not results:
        return {
            "available": False,
            "provider": str(latest.get("provider") or "duckduckgo"),
            "error": "web_search returned no results",
            "result_count": count,
            "tool_is_error": tool_is_error,
            "transcript_path": str(path),
        }

    first = results[0] if isinstance(results[0], dict) else {}
    return {
        "available": True,
        "provider": str(latest.get("provider") or "duckduckgo"),
        "result_count": count,
        "first_result_title": _clean_external_text(str(first.get("title") or "")),
        "first_result_url": str(first.get("url") or ""),
        "took_ms": latest.get("tookMs"),
        "tool_is_error": tool_is_error,
        "transcript_path": str(path),
    }


def run_web_search_preflight(
    *,
    agent_id: str,
    config_path: Path,
    current_python_path: str,
    run_subprocess: RunSubprocess,
    timeout_seconds: int = 120,
    base_env: Mapping[str, str] | None = None,
    system_proxy_text: str | None = None,
) -> dict[str, Any]:
    session_id = f"web-search-preflight-{uuid.uuid4().hex[:10]}"
    env = build_openclaw_subprocess_env(
        base_env=base_env,
        config_path=config_path,
        system_proxy_text=system_proxy_text,
    )
    command = [
        current_python_path,
        str(_wrapper_path()),
        "--agent",
        agent_id,
        "--config-file",
        str(config_path),
        "--session-id",
        session_id,
        "--message",
        (
            "Health check only. Use the web_search tool exactly once for query: "
            f"{PREFLIGHT_QUERY}. Then answer in one short paragraph stating whether web_search "
            "returned results or an error, and include one result title if available. Do not use shell commands."
        ),
        "--thinking",
        "minimal",
        "--timeout",
        str(timeout_seconds),
        "--finalization-grace-seconds",
        "10",
        "--json",
    ]
    try:
        try:
            completed = run_subprocess(
                command,
                env=env,
                timeout=timeout_seconds + 30,
                text=True,
                capture_output=True,
                check=False,
            )
        except TypeError:
            completed = run_subprocess(command, env=env, timeout=timeout_seconds + 30)
    except Exception as exc:
        return {
            "available": False,
            "agent_id": agent_id,
            "config_path": str(config_path),
            "session_id": session_id,
            "error": str(exc),
            "proxy_env": proxy_environment_report(env),
        }

    report: dict[str, Any] = {
        "agent_id": agent_id,
        "config_path": str(config_path),
        "session_id": session_id,
        "returncode": completed.returncode,
        "proxy_env": proxy_environment_report(env),
    }
    if completed.returncode != 0:
        report.update(
            {
                "available": False,
                "error": (completed.stderr or completed.stdout or "web_search preflight command failed").strip()[:2000],
            }
        )
        return report

    payload = _parse_jsonish(completed.stdout or completed.stderr)
    result_payload = _unwrap_agent_payload(payload)
    meta = result_payload.get("meta") if isinstance(result_payload, dict) else {}
    convergence = meta.get("convergence") if isinstance(meta, dict) else {}
    transcript_path = str(convergence.get("transcript_path") or "").strip() if isinstance(convergence, dict) else ""
    if transcript_path:
        report.update(evaluate_web_search_transcript(Path(transcript_path)))
    else:
        report.update({"available": False, "error": "web_search preflight transcript path missing"})
    tool_summary = meta.get("toolSummary") if isinstance(meta, dict) else None
    if isinstance(tool_summary, dict):
        report["tool_summary"] = tool_summary
    return report
