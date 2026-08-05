"""Tests for the chat-confirm opt-in gate (brokers/config.py) and the
/trade allow-chat-confirm terminal command (apps/cli/commands/broker_cmds.py).

This is Layer 1 of the two-layer safety gate for aria.broker.confirm_order:
the flag can only ever be flipped on by a human typing the exact broker id
back at the aria-code terminal — see tests/test_aria_mcp_server.py for
Layer 2 (the MCP handler itself).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import brokers.config as config_mod
from brokers.config import (
    add_broker_config,
    is_chat_confirm_enabled,
    set_chat_confirm_enabled,
)


def _patch_config_path(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config_mod, "BROKERS_CONFIG_PATH", tmp_path / "brokers.json")


def test_chat_confirm_disabled_by_default(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    add_broker_config({"id": "ths1", "type": "easytrader", "label": "同花顺"})
    assert is_chat_confirm_enabled("ths1") is False


def test_chat_confirm_unknown_broker_is_disabled(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    assert is_chat_confirm_enabled("does-not-exist") is False


def test_set_chat_confirm_enabled_true_then_query(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    add_broker_config({"id": "ths1", "type": "easytrader", "label": "同花顺"})
    found = set_chat_confirm_enabled("ths1", True)
    assert found is True
    assert is_chat_confirm_enabled("ths1") is True


def test_set_chat_confirm_enabled_false_after_true(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    add_broker_config({"id": "ths1", "type": "easytrader", "label": "同花顺"})
    set_chat_confirm_enabled("ths1", True)
    set_chat_confirm_enabled("ths1", False)
    assert is_chat_confirm_enabled("ths1") is False


def test_set_chat_confirm_enabled_unknown_broker_returns_false(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    assert set_chat_confirm_enabled("does-not-exist", True) is False


def test_set_chat_confirm_enabled_does_not_affect_other_brokers(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    add_broker_config({"id": "ths1", "type": "easytrader", "label": "同花顺"})
    add_broker_config({"id": "huatai1", "type": "easytrader", "label": "华泰"})
    set_chat_confirm_enabled("ths1", True)
    assert is_chat_confirm_enabled("ths1") is True
    assert is_chat_confirm_enabled("huatai1") is False


class _FakeConsole:
    """Minimal stand-in so cmd_trade's console.print/input calls don't need real rich."""

    def __init__(self, answer: str):
        self._answer = answer
        self.printed: list[str] = []

    def print(self, msg):
        self.printed.append(str(msg))

    def input(self, prompt=""):
        return self._answer


@pytest.mark.asyncio
async def test_allow_chat_confirm_requires_exact_broker_id_not_yes(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    add_broker_config({"id": "ths1", "type": "easytrader", "label": "同花顺"})

    import aria_cli
    from apps.cli.commands import broker_cmds

    fake_console = _FakeConsole(answer="yes")
    monkeypatch.setattr(aria_cli, "console", fake_console, raising=False)
    monkeypatch.setattr(aria_cli, "HAS_RICH", True, raising=False)

    class _Registry:
        def active(self):
            return None

        def connect_default(self):
            return None

    monkeypatch.setattr(aria_cli, "_get_broker_registry", lambda: _Registry(), raising=False)

    handler = broker_cmds.BrokerCommandsMixin()
    await handler.cmd_trade("allow-chat-confirm ths1")

    assert is_chat_confirm_enabled("ths1") is False


@pytest.mark.asyncio
async def test_allow_chat_confirm_succeeds_with_exact_broker_id(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    add_broker_config({"id": "ths1", "type": "easytrader", "label": "同花顺"})

    import aria_cli
    from apps.cli.commands import broker_cmds

    fake_console = _FakeConsole(answer="ths1")
    monkeypatch.setattr(aria_cli, "console", fake_console, raising=False)
    monkeypatch.setattr(aria_cli, "HAS_RICH", True, raising=False)

    class _Registry:
        def active(self):
            return None

        def connect_default(self):
            return None

    monkeypatch.setattr(aria_cli, "_get_broker_registry", lambda: _Registry(), raising=False)

    handler = broker_cmds.BrokerCommandsMixin()
    await handler.cmd_trade("allow-chat-confirm ths1")

    assert is_chat_confirm_enabled("ths1") is True


@pytest.mark.asyncio
async def test_disallow_chat_confirm_turns_it_back_off(monkeypatch, tmp_path):
    _patch_config_path(monkeypatch, tmp_path)
    add_broker_config({"id": "ths1", "type": "easytrader", "label": "同花顺"})
    set_chat_confirm_enabled("ths1", True)

    import aria_cli
    from apps.cli.commands import broker_cmds

    fake_console = _FakeConsole(answer="")
    monkeypatch.setattr(aria_cli, "console", fake_console, raising=False)
    monkeypatch.setattr(aria_cli, "HAS_RICH", True, raising=False)

    class _Registry:
        def active(self):
            return None

        def connect_default(self):
            return None

    monkeypatch.setattr(aria_cli, "_get_broker_registry", lambda: _Registry(), raising=False)

    handler = broker_cmds.BrokerCommandsMixin()
    await handler.cmd_trade("disallow-chat-confirm ths1")

    assert is_chat_confirm_enabled("ths1") is False
