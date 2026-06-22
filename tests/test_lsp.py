"""Tests for runtime.lsp — minimal LSP diagnostics client.

Network/subprocess-free unit tests for the pure helpers, plus an integration
test that only runs when pylsp is actually installed.
"""

import shutil
import pytest
from pathlib import Path

from runtime.lsp import (
    server_for,
    available_servers,
    _encode,
    _format_diagnostics,
    _same_uri,
    tool_lsp_diagnostics,
    get_diagnostics,
    LSP_TOOLS,
    LSP_SCHEMAS,
)


class TestServerFor:
    def test_unknown_extension_returns_none(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c")
        assert server_for(f) is None

    def test_python_returns_pylsp_when_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/" + exe)
        # Clear the availability cache so the patched which() is consulted
        import runtime.lsp as _lsp
        _lsp._AVAILABILITY.clear()
        cmd, lang = server_for(tmp_path / "x.py")
        assert cmd[0] == "pylsp"
        assert lang == "python"

    def test_returns_none_when_server_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda exe: None)
        import runtime.lsp as _lsp
        _lsp._AVAILABILITY.clear()
        assert server_for(tmp_path / "x.py") is None

    def test_typescript_language_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/" + exe)
        import runtime.lsp as _lsp
        _lsp._AVAILABILITY.clear()
        cmd, lang = server_for(tmp_path / "x.tsx")
        assert lang == "typescriptreact"


class TestEncode:
    def test_includes_content_length_header(self):
        raw = _encode({"jsonrpc": "2.0", "method": "x"})
        assert raw.startswith(b"Content-Length: ")
        assert b"\r\n\r\n" in raw

    def test_body_is_valid_json(self):
        import json
        raw = _encode({"a": 1})
        _, body = raw.split(b"\r\n\r\n", 1)
        assert json.loads(body) == {"a": 1}


class TestFormatDiagnostics:
    def test_empty(self):
        assert _format_diagnostics([]) == []

    def test_converts_to_one_based_lines(self):
        raw = [{
            "range": {"start": {"line": 0, "character": 0}},
            "severity": 1, "message": "undefined name", "source": "pyflakes",
        }]
        out = _format_diagnostics(raw)
        assert out[0]["line"] == 1
        assert out[0]["col"] == 1
        assert out[0]["severity"] == "error"

    def test_severity_mapping(self):
        raw = [
            {"range": {"start": {"line": 2, "character": 1}}, "severity": 2, "message": "w"},
            {"range": {"start": {"line": 0, "character": 0}}, "severity": 1, "message": "e"},
        ]
        out = _format_diagnostics(raw)
        # sorted by line — error (line 1) before warning (line 3)
        assert out[0]["severity"] == "error"
        assert out[1]["severity"] == "warning"

    def test_handles_missing_fields(self):
        out = _format_diagnostics([{"message": "bare"}])
        assert out[0]["line"] == 1
        assert out[0]["message"] == "bare"


class TestSameUri:
    def test_exact_match(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        uri = f.as_uri()
        assert _same_uri(uri, uri, f) is True

    def test_empty_is_false(self, tmp_path):
        f = tmp_path / "a.py"
        assert _same_uri("", "file:///other", f) is False


class TestToolWrapper:
    def test_missing_path(self):
        result = tool_lsp_diagnostics({})
        assert result["success"] is False

    def test_nonexistent_file(self):
        result = tool_lsp_diagnostics({"path": "/no/such/file.py"})
        assert result["success"] is False

    def test_unsupported_filetype_reports_unavailable(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b")
        result = tool_lsp_diagnostics({"path": str(f)})
        assert result["success"] is True
        assert result["data"]["available"] is False


class TestRegistry:
    def test_lsp_tools_has_handler(self):
        assert "lsp_diagnostics" in LSP_TOOLS
        handler, desc = LSP_TOOLS["lsp_diagnostics"]
        assert callable(handler)

    def test_schema_shape(self):
        assert len(LSP_SCHEMAS) == 1
        s = LSP_SCHEMAS[0]
        assert s["name"] == "lsp_diagnostics"
        assert "path" in s["parameters"]["properties"]


@pytest.mark.skipif(shutil.which("pylsp") is None, reason="pylsp not installed")
class TestPylspIntegration:
    def test_clean_file_has_no_errors(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("x = 1\nprint(x)\n")
        diags = get_diagnostics(f, timeout=10.0)
        errors = [d for d in diags if d["severity"] == "error"]
        assert errors == []

    def test_undefined_name_is_flagged(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("print(undefined_variable_xyz)\n")
        diags = get_diagnostics(f, timeout=10.0)
        msgs = " ".join(d["message"].lower() for d in diags)
        assert "undefined" in msgs or "undefined_variable_xyz" in msgs
