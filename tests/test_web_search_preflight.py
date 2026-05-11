from __future__ import annotations

import json
import subprocess
from pathlib import Path

from benchmarking.web_search_preflight import (
    evaluate_web_search_transcript,
    run_web_search_preflight,
)


def write_transcript(path: Path, tool_details: dict[str, object], *, is_error: bool = False) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "toolResult",
                            "toolName": "web_search",
                            "content": [{"type": "text", "text": json.dumps(tool_details)}],
                            "details": tool_details,
                            "isError": is_error,
                        },
                    }
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_evaluate_web_search_transcript_accepts_result_payload(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    write_transcript(
        transcript,
        {
            "query": "OpenAlex scholarly works API",
            "provider": "duckduckgo",
            "count": 1,
            "results": [
                {
                    "title": (
                        '<<<EXTERNAL_UNTRUSTED_CONTENT id="abc">>>\n'
                        "Source: Web Search\n---\nAPI Overview - OpenAlex Developers\n"
                        '<<<END_EXTERNAL_UNTRUSTED_CONTENT id="abc">>>'
                    )
                }
            ],
        },
    )

    report = evaluate_web_search_transcript(transcript)

    assert report["available"] is True
    assert report["provider"] == "duckduckgo"
    assert report["result_count"] == 1
    assert report["first_result_title"] == "API Overview - OpenAlex Developers"


def test_evaluate_web_search_transcript_rejects_status_error_even_when_is_error_false(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    write_transcript(
        transcript,
        {
            "status": "error",
            "error": "fetch failed",
        },
        is_error=False,
    )

    report = evaluate_web_search_transcript(transcript)

    assert report["available"] is False
    assert report["error"] == "fetch failed"
    assert report["tool_is_error"] is False


def test_run_web_search_preflight_passes_proxy_env_and_reads_transcript(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    write_transcript(
        transcript,
        {
            "provider": "duckduckgo",
            "count": 1,
            "results": [{"title": "API Overview - OpenAlex Developers"}],
        },
    )
    seen: dict[str, object] = {}

    def fake_run(command, *, env=None, cwd=None, timeout=None, text=None, capture_output=None, check=None):
        seen["command"] = command
        seen["env"] = dict(env or {})
        seen["capture_output"] = capture_output
        seen["text"] = text
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "result": {
                        "payloads": [{"text": "web_search returned results"}],
                        "meta": {
                            "convergence": {
                                "transcript_path": str(transcript),
                            },
                            "toolSummary": {
                                "calls": 1,
                                "tools": ["web_search"],
                                "failures": 0,
                            },
                        },
                    }
                }
            ),
            stderr="",
        )

    report = run_web_search_preflight(
        agent_id="benchmark-single-skills-on",
        config_path=tmp_path / "openclaw.json",
        current_python_path="/venv/bin/python",
        run_subprocess=fake_run,
        timeout_seconds=30,
        base_env={},
        system_proxy_text="""
HTTPEnable : 1
HTTPProxy : 127.0.0.1
HTTPPort : 7892
HTTPSEnable : 1
HTTPSProxy : 127.0.0.1
HTTPSPort : 7892
""",
    )

    env = seen["env"]
    assert isinstance(env, dict)
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7892"
    assert env["OPENCLAW_CONFIG_PATH"] == str(tmp_path / "openclaw.json")
    assert seen["capture_output"] is True
    assert seen["text"] is True
    assert report["available"] is True
    assert report["result_count"] == 1
