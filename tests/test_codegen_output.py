from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


class _CodeTerminal:
    def __init__(self, response: str):
        self.config = {"model": "qwen2.5:7b"}
        self.conversation = []
        self._response = response

    async def send_message(self, _prompt):
        self.conversation.append({"role": "assistant", "content": self._response})


@pytest.mark.asyncio
async def test_cmd_code_saves_to_user_generated_dir_by_default(monkeypatch, tmp_path):
    import aria_cli

    monkeypatch.setenv("ARIA_USER_OUTPUT_ROOT", str(tmp_path))
    terminal = _CodeTerminal("```python\nprint('hello')\n```")
    commands = aria_cli.SlashCommands(terminal)

    await commands.cmd_code("build a simple strategy script")

    generated = tmp_path / "generated"
    files = list(generated.glob("*.py"))
    assert files, "expected cmd_code to save a generated script in the user workspace"
    assert "print('hello')" in files[0].read_text(encoding="utf-8")


def test_cmd_code_relative_save_paths_go_to_user_workspace(monkeypatch, tmp_path):
    from artifacts import user_generated_dir
    from apps.cli.codegen_paths import resolve_user_code_path

    monkeypatch.setenv("ARIA_USER_OUTPUT_ROOT", str(tmp_path))
    path = resolve_user_code_path("my strategy", "strategy.py", user_generated_dir=user_generated_dir())

    assert path == user_generated_dir() / "strategy.py"
