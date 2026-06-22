"""Tests for shell completion script generators in OpsCommandsMixin."""

import pytest


def _import_builders():
    from apps.cli.commands.ops_cmds import _build_bash_completion, _build_zsh_completion, _detect_user_shell
    return _build_bash_completion, _build_zsh_completion, _detect_user_shell


SAMPLE_CMDS = ["/help", "/model", "/recall", "/permissions", "/deep", "/quote"]


class TestBashCompletion:
    def test_outputs_function_definition(self):
        build, _, _ = _import_builders()
        script = build(SAMPLE_CMDS)
        assert "_aria_code_complete()" in script

    def test_contains_all_commands(self):
        build, _, _ = _import_builders()
        script = build(SAMPLE_CMDS)
        for cmd in SAMPLE_CMDS:
            assert cmd in script

    def test_complete_line_present(self):
        build, _, _ = _import_builders()
        script = build(SAMPLE_CMDS)
        assert "complete -F _aria_code_complete aria-code" in script

    def test_returns_string(self):
        build, _, _ = _import_builders()
        script = build(SAMPLE_CMDS)
        assert isinstance(script, str)
        assert len(script) > 100


class TestZshCompletion:
    def test_outputs_compdef_header(self):
        _, build, _ = _import_builders()
        script = build(SAMPLE_CMDS)
        assert "#compdef aria-code" in script

    def test_contains_all_commands(self):
        _, build, _ = _import_builders()
        script = build(SAMPLE_CMDS)
        for cmd in SAMPLE_CMDS:
            assert cmd in script

    def test_has_arguments_directive(self):
        _, build, _ = _import_builders()
        script = build(SAMPLE_CMDS)
        assert "_arguments" in script

    def test_returns_string(self):
        _, build, _ = _import_builders()
        script = build(SAMPLE_CMDS)
        assert isinstance(script, str)


class TestDetectUserShell:
    def test_detects_zsh(self, monkeypatch):
        _, _, detect = _import_builders()
        monkeypatch.setenv("SHELL", "/bin/zsh")
        assert detect() == "zsh"

    def test_detects_bash(self, monkeypatch):
        _, _, detect = _import_builders()
        monkeypatch.setenv("SHELL", "/bin/bash")
        assert detect() == "bash"

    def test_defaults_to_bash_for_unknown(self, monkeypatch):
        _, _, detect = _import_builders()
        monkeypatch.delenv("SHELL", raising=False)
        assert detect() == "bash"
