import os

from apps.cli.bootstrap import default_config, disable_broken_proxy, load_aria_env


def test_load_aria_env_does_not_override_existing(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ARIA_TEST_KEY=from_file\n"
        "EXISTING_KEY=from_file\n"
        "# ignored\n"
        "BROKEN_LINE\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING_KEY", "already_set")
    monkeypatch.delenv("ARIA_TEST_KEY", raising=False)

    load_aria_env(env_file)

    assert os.environ["ARIA_TEST_KEY"] == "from_file"
    assert os.environ["EXISTING_KEY"] == "already_set"


def test_disable_broken_proxy_removes_unreachable_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")

    disable_broken_proxy(timeout=0.01)

    assert "HTTPS_PROXY" not in os.environ
    assert "HTTP_PROXY" not in os.environ


def test_default_config_uses_runtime_env(monkeypatch):
    monkeypatch.setenv("ARTHERA_API_URL", "http://127.0.0.1:8100")
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11435")

    cfg = default_config()

    assert cfg["api_url"] == "http://127.0.0.1:8100"
    assert cfg["ollama_url"] == "http://127.0.0.1:11435"
    assert cfg["permission_mode"] == "workspace-write"
