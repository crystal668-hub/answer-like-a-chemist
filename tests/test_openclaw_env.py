from __future__ import annotations

from pathlib import Path

from benchmarking.runtime.openclaw_env import (
    build_openclaw_subprocess_env,
    parse_scutil_proxy_output,
    proxy_environment_report,
)


def test_parse_scutil_proxy_output_extracts_http_and_https_proxy() -> None:
    payload = """
<dictionary> {
  HTTPEnable : 1
  HTTPPort : 7892
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : 7892
  HTTPSProxy : 127.0.0.1
}
"""

    proxies = parse_scutil_proxy_output(payload)

    assert proxies["HTTP_PROXY"] == "http://127.0.0.1:7892"
    assert proxies["HTTPS_PROXY"] == "http://127.0.0.1:7892"


def test_build_openclaw_subprocess_env_enables_node_proxy_from_system_proxy() -> None:
    env = build_openclaw_subprocess_env(
        base_env={},
        config_path=Path("/tmp/openclaw.json"),
        system_proxy_text="""
HTTPEnable : 1
HTTPProxy : 127.0.0.1
HTTPPort : 7892
HTTPSEnable : 1
HTTPSProxy : 127.0.0.1
HTTPSPort : 7892
""",
    )

    assert env["OPENCLAW_CONFIG_PATH"] == "/tmp/openclaw.json"
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7892"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7892"
    assert "127.0.0.1" in env["NO_PROXY"]


def test_build_openclaw_subprocess_env_does_not_override_explicit_proxy() -> None:
    env = build_openclaw_subprocess_env(
        base_env={
            "NODE_USE_ENV_PROXY": "0",
            "HTTP_PROXY": "http://proxy.example:8080",
            "HTTPS_PROXY": "http://proxy.example:8080",
        },
        system_proxy_text="""
HTTPEnable : 1
HTTPProxy : 127.0.0.1
HTTPPort : 7892
HTTPSEnable : 1
HTTPSProxy : 127.0.0.1
HTTPSPort : 7892
""",
    )

    assert env["NODE_USE_ENV_PROXY"] == "0"
    assert env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert env["HTTPS_PROXY"] == "http://proxy.example:8080"


def test_proxy_environment_report_redacts_proxy_credentials() -> None:
    report = proxy_environment_report(
        {
            "NODE_USE_ENV_PROXY": "1",
            "HTTPS_PROXY": "http://user:secret@proxy.example:8080",
        }
    )

    assert report["NODE_USE_ENV_PROXY"] == "1"
    assert report["HTTPS_PROXY"] == "http://***@proxy.example:8080"
