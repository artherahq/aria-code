"""Tests for /canvas (apps/cli/commands/canvas_cmds.py)."""
from __future__ import annotations

import pytest

from aria_code.apps.cli.commands.canvas_cmds import CanvasCommandsMixin


class _FakeConsole:
    def __init__(self):
        self.printed: list[str] = []

    def print(self, msg):
        self.printed.append(str(msg))


class _FakeSession:
    def __init__(self, url):
        self.url = url


async def test_cmd_canvas_starts_session_and_opens_browser(monkeypatch):
    from aria_code import aria_cli
    from aria_code import preview_server

    handler = CanvasCommandsMixin()
    fake_console = _FakeConsole()
    class FakeContext:
        console = fake_console
        has_rich = True
    handler.context = FakeContext()

    monkeypatch.setattr(preview_server, "get_active_session", lambda: None)

    started = {}

    async def fake_start_session():
        started["called"] = True
        return _FakeSession("http://127.0.0.1:8765/")

    monkeypatch.setattr(preview_server, "start_session", fake_start_session)

    opened = {}
    monkeypatch.setattr(preview_server, "open_in_browser", lambda url: opened.setdefault("url", url))

    
    await handler.cmd_canvas("")

    assert started.get("called") is True
    assert opened["url"] == "http://127.0.0.1:8765/"
    assert any("8765" in msg for msg in fake_console.printed)


async def test_cmd_canvas_already_running_does_not_restart(monkeypatch):
    from aria_code import aria_cli
    from aria_code import preview_server

    handler = CanvasCommandsMixin()
    fake_console = _FakeConsole()
    class FakeContext:
        console = fake_console
        has_rich = True
    handler.context = FakeContext()

    existing = _FakeSession("http://127.0.0.1:8765/")
    monkeypatch.setattr(preview_server, "get_active_session", lambda: existing)

    async def fail_start_session():
        raise AssertionError("start_session should not be called when already running")

    monkeypatch.setattr(preview_server, "start_session", fail_start_session)

    
    await handler.cmd_canvas("")

    assert any("8765" in msg for msg in fake_console.printed)


async def test_cmd_canvas_stop_with_no_session_running(monkeypatch):
    from aria_code import aria_cli
    from aria_code import preview_server

    handler = CanvasCommandsMixin()
    fake_console = _FakeConsole()
    class FakeContext:
        console = fake_console
        has_rich = True
    handler.context = FakeContext()
    monkeypatch.setattr(preview_server, "get_active_session", lambda: None)

    
    await handler.cmd_canvas("stop")

    assert any("未在运行" in msg for msg in fake_console.printed)


async def test_cmd_canvas_stop_tears_down_running_session(monkeypatch):
    from aria_code import aria_cli
    from aria_code import preview_server

    handler = CanvasCommandsMixin()
    fake_console = _FakeConsole()
    class FakeContext:
        console = fake_console
        has_rich = True
    handler.context = FakeContext()

    existing = _FakeSession("http://127.0.0.1:8765/")
    monkeypatch.setattr(preview_server, "get_active_session", lambda: existing)

    stopped = {}

    async def fake_stop_session():
        stopped["called"] = True

    monkeypatch.setattr(preview_server, "stop_session", fake_stop_session)

    
    await handler.cmd_canvas("stop")

    assert stopped.get("called") is True
    assert any("已停止" in msg for msg in fake_console.printed)


async def test_cmd_canvas_start_failure_reports_error(monkeypatch):
    from aria_code import aria_cli
    from aria_code import preview_server

    handler = CanvasCommandsMixin()
    fake_console = _FakeConsole()
    class FakeContext:
        console = fake_console
        has_rich = True
    handler.context = FakeContext()
    monkeypatch.setattr(preview_server, "get_active_session", lambda: None)

    errors = []
    monkeypatch.setattr(aria_cli, "_print_error", lambda msg, hint="": errors.append(msg), raising=False)

    async def failing_start_session():
        raise RuntimeError("no free port")

    monkeypatch.setattr(preview_server, "start_session", failing_start_session)

    
    await handler.cmd_canvas("")

    assert any("no free port" in e for e in errors)
