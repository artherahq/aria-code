"""
tests/test_code_audit_tools.py — Unit tests for code audit and diff generation tools
"""

from aria_code.tools.code_audit_tools import (
    tool_audit_code_diagnostics,
    tool_generate_code_diff,
    register_code_audit_tools,
)


def test_tool_audit_code_diagnostics_clean():
    code = "def calculate_ma(df):\n    return df['close'].rolling(20).mean()\n"
    res = tool_audit_code_diagnostics({"code": code, "language": "python"})
    assert res["success"] is True
    assert res["health_score"] == 100
    assert res["issues_found"] == 0


def test_tool_audit_code_diagnostics_lookahead_bias():
    code = "def signal(df):\n    future = df['close'].shift(-1)\n    return future > df['close']\n"
    res = tool_audit_code_diagnostics({"code": code, "language": "python"})
    assert res["success"] is True
    assert res["issues_found"] >= 1
    assert any(i["category"] == "lookahead_bias" for i in res["issues"])


def test_tool_generate_code_diff():
    orig = "def foo():\n    return 1\n"
    mod = "def foo():\n    return 2\n"
    res = tool_generate_code_diff({"original": orig, "modified": mod, "file_path": "foo.py"})
    assert res["success"] is True
    assert res["file_path"] == "foo.py"
    assert res["additions"] >= 1
    assert res["deletions"] >= 1
    assert len(res["diff_lines"]) >= 2


def test_register_code_audit_tools():
    dummy = {}
    register_code_audit_tools(dummy)
    assert "audit_code_diagnostics" in dummy
    assert "generate_code_diff" in dummy
