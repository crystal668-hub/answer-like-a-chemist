import pytest

from benchmarking.runtime import container_network

SYSTEM_PROXY = "HTTPEnable : 1\nHTTPProxy : 127.0.0.1\nHTTPPort : 7892\nHTTPSEnable : 1\nHTTPSProxy : 127.0.0.1\nHTTPSPort : 7892"


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    monkeypatch.setattr(container_network, "dotenv_values", lambda path: {})


def test_macos_system_proxy_is_mapped_and_frozen_for_all_consumers():
    config = container_network.resolve_container_network(base_env={}, system_proxy_text=SYSTEM_PROXY, platform="darwin")
    env = config.apply({"HTTP_PROXY": "http://changed:9000", "HTTPS_PROXY": "", "KEEP": "value"})
    assert env["HTTP_PROXY"] == env["http_proxy"] == "http://host.docker.internal:7892"
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://host.docker.internal:7892"
    assert env["KEEP"] == "value"
    assert env["NODE_USE_ENV_PROXY"] == "1"
    assert env["NO_PROXY"] == env["no_proxy"]


@pytest.mark.parametrize("hostname", ["localhost", "127.0.0.1", "[::1]"])
def test_explicit_proxy_credentials_and_port_are_preserved_but_redacted(hostname):
    config = container_network.resolve_container_network(base_env={"HTTPS_PROXY": f"http://user:secret@{hostname}:7892"}, system_proxy_text="", platform="darwin")
    assert config.apply({})["HTTPS_PROXY"] == "http://user:secret@host.docker.internal:7892"
    assert "secret" not in str(config.to_meta())
    assert "secret" not in repr(config)


def test_lowercase_override_and_no_proxy_are_shared():
    config = container_network.resolve_container_network(base_env={"HTTPS_PROXY": "http://upper:123", "https_proxy": "http://lower:456", "no_proxy": ".example.org"}, system_proxy_text=SYSTEM_PROXY, platform="darwin")
    env = config.apply({})
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://lower:456"
    assert ".example.org" in env["NO_PROXY"]


def test_linux_host_network_keeps_loopback_proxy():
    config = container_network.resolve_container_network(base_env={"HTTPS_PROXY": "http://127.0.0.1:7892"}, system_proxy_text="", platform="linux")
    assert config.apply({})["HTTPS_PROXY"] == "http://127.0.0.1:7892"


def test_process_environment_overrides_dotenv(monkeypatch):
    monkeypatch.setattr(container_network, "dotenv_values", lambda path: {"HTTPS_PROXY": "http://fallback:7892", "QWEN_BASE_URL": "https://provider.example"})
    assert container_network.runtime_environment({"HTTPS_PROXY": "http://explicit:123"}) == {"HTTPS_PROXY": "http://explicit:123", "QWEN_BASE_URL": "https://provider.example"}
