from pathlib import Path

from aria_code import aria_feishu_bot


def test_swarm_orchestrator_requires_explicit_configuration(monkeypatch):
    monkeypatch.delenv("ARIA_SWARM_ORCHESTRATOR", raising=False)
    assert aria_feishu_bot._swarm_orchestrator_path() is None


def test_swarm_orchestrator_expands_configured_path(monkeypatch, tmp_path):
    script = tmp_path / "swarm.py"
    script.touch()
    monkeypatch.setenv("ARIA_SWARM_ORCHESTRATOR", str(script))
    assert aria_feishu_bot._swarm_orchestrator_path() == Path(script).resolve()
