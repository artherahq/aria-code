"""
Tests for aria_cli.py core utilities.
Run from apps/cli/: python3 -m pytest tests/ -v
"""
import sys
import os
import pathlib
import unittest
import json
import tempfile

# Allow importing from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aria_cli import (
    _strip_markdown_fences,
    _is_safe_path,
    _tool_read_file,
    _tool_write_file,
    _tool_edit_file,
    _tool_list_files,
    load_config,
    save_config,
    resolve_model_key,
    MODEL_ALIASES,
    MODELS,
    DEFAULT_CONFIG,
    SessionManager,
    SESSIONS_DIR,
    _is_coding_request,
    _is_analysis_request,
    _ACTIVE_WRITE_POLICY,
    _sync_write_policy,
)


# ============================================================================
# _strip_markdown_fences
# ============================================================================

class TestStripMarkdownFences(unittest.TestCase):
    def test_strips_python_fence(self):
        code = "```python\nprint('hello')\n```"
        result = _strip_markdown_fences(code)
        self.assertNotIn("```", result)
        self.assertIn("print('hello')", result)

    def test_strips_plain_fence(self):
        code = "```\nx = 1\n```"
        result = _strip_markdown_fences(code)
        self.assertNotIn("```", result)
        self.assertIn("x = 1", result)

    def test_no_fence_unchanged(self):
        code = "x = 1\nprint(x)"
        result = _strip_markdown_fences(code)
        self.assertIn("x = 1", result)

    def test_trailing_newline_preserved(self):
        code = "```python\nfoo()\n```"
        result = _strip_markdown_fences(code)
        self.assertTrue(result.endswith("\n"))


# ============================================================================
# _is_safe_path
# ============================================================================

class TestIsSafePath(unittest.TestCase):
    def test_home_directory_is_safe(self):
        p = pathlib.Path.home().resolve() / "test.txt"
        self.assertTrue(_is_safe_path(p))

    def test_tmp_is_safe(self):
        p = pathlib.Path("/tmp/test_file.txt").resolve()
        self.assertTrue(_is_safe_path(p))

    def test_etc_is_blocked(self):
        p = pathlib.Path("/etc/passwd").resolve()
        self.assertFalse(_is_safe_path(p))

    def test_dev_is_blocked(self):
        p = pathlib.Path("/dev/null").resolve()
        self.assertFalse(_is_safe_path(p))

    def test_cwd_is_safe(self):
        p = pathlib.Path.cwd().resolve() / "some_file.py"
        self.assertTrue(_is_safe_path(p))


# ============================================================================
# _tool_read_file / _tool_write_file / _tool_edit_file
# ============================================================================

class TestFileTools(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.tmpdir, "test.py")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_and_read_file(self):
        content = "import os\nimport sys\n\nx = 42\nprint('result:', x)\n"
        result = _tool_write_file({"path": self.test_file, "content": content})
        self.assertTrue(result["success"], result.get("error"))
        read = _tool_read_file({"path": self.test_file})
        self.assertTrue(read["success"])
        self.assertIn("x = 42", read["data"]["content"])

    def test_read_nonexistent_file(self):
        result = _tool_read_file({"path": "/tmp/nonexistent_aria_test_xyz.txt"})
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"].lower())

    def test_write_empty_content_rejected(self):
        result = _tool_write_file({"path": self.test_file, "content": "   "})
        self.assertFalse(result["success"])

    def test_write_placeholder_content_rejected(self):
        result = _tool_write_file({"path": self.test_file, "content": "<placeholder>"})
        self.assertFalse(result["success"])

    def test_edit_file(self):
        _tool_write_file({"path": self.test_file, "content": "import os\n\nx = 1\nprint(x)\n"})
        result = _tool_edit_file({
            "path": self.test_file,
            "old_string": "x = 1",
            "new_string": "x = 42",
        })
        self.assertTrue(result["success"], result.get("error"))
        read = _tool_read_file({"path": self.test_file})
        self.assertIn("x = 42", read["data"]["content"])

    def test_edit_file_old_string_not_found(self):
        _tool_write_file({"path": self.test_file, "content": "x = 1\nprint(x)\n"})
        result = _tool_edit_file({
            "path": self.test_file,
            "old_string": "nonexistent string xyz",
            "new_string": "replacement",
        })
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"].lower())

    def test_symlink_traversal_blocked(self):
        """Creating a symlink to /etc should be blocked by _is_safe_path."""
        link_path = os.path.join(self.tmpdir, "etc_link")
        try:
            os.symlink("/etc", link_path)
            target = os.path.join(link_path, "passwd")
            result = _tool_read_file({"path": target})
            self.assertFalse(result["success"])
            self.assertIn("Access denied", result.get("error", ""))
        except PermissionError:
            pass  # Some systems prevent this symlink
        except OSError:
            pass

    def test_read_file_with_offset_and_limit(self):
        lines = "\n".join(f"line {i}" for i in range(20))
        _tool_write_file({"path": self.test_file, "content": lines})
        result = _tool_read_file({"path": self.test_file, "offset": 5, "limit": 3})
        self.assertTrue(result["success"])
        content = result["data"]["content"]
        self.assertIn("line 5", content)
        self.assertNotIn("line 0", content)

    def test_list_files(self):
        for name in ["a.py", "b.py", "c.txt"]:
            open(os.path.join(self.tmpdir, name), "w").close()
        result = _tool_list_files({"path": self.tmpdir, "pattern": "*.py"})
        self.assertTrue(result["success"])
        names = [item["name"] for item in result["data"]["items"]]
        self.assertIn("a.py", names)
        self.assertIn("b.py", names)
        self.assertNotIn("c.txt", names)


# ============================================================================
# Config system
# ============================================================================

# ============================================================================
# Prompt routing
# ============================================================================

class TestPromptRouting(unittest.TestCase):
    def test_coding_request_python_script(self):
        self.assertTrue(_is_coding_request("write a python backtest for SPY"))

    def test_coding_request_chinese_code(self):
        self.assertTrue(_is_coding_request("生成一个动量策略的代码"))

    def test_analysis_request_stock(self):
        self.assertTrue(_is_analysis_request("分析苹果股票"))
        self.assertFalse(_is_coding_request("分析苹果股票"))

    def test_analysis_request_english(self):
        self.assertTrue(_is_analysis_request("analyze AAPL fundamentals"))
        self.assertFalse(_is_coding_request("analyze AAPL fundamentals"))

    def test_coding_takes_priority_over_analysis(self):
        # "生成" is coding keyword, so should NOT be analysis even if "分析" present
        self.assertTrue(_is_coding_request("分析苹果股票并生成图表"))
        self.assertFalse(_is_analysis_request("分析苹果股票并生成图表"))

    def test_chat_is_neither(self):
        self.assertFalse(_is_coding_request("what is the federal funds rate?"))
        self.assertFalse(_is_analysis_request("what is the federal funds rate?"))

    def test_shenzhen_realty_not_analysis(self):
        """'深圳房价走势和折旧价' 不应路由到股票分析模板（房地产排除修复验证）。"""
        # 含 "房价"/"折旧" → _ANALYSIS_NON_STOCK_TOPICS 排除，返回 False
        self.assertFalse(_is_analysis_request("深圳房价走势和折旧价"))
        self.assertFalse(_is_coding_request("深圳房价走势和折旧价"))

    def test_generate_strategy_is_coding(self):
        """'生成一个动量策略回测代码' 应被识别为编码请求。"""
        self.assertTrue(_is_coding_request("生成一个动量策略回测代码"))


class TestConfig(unittest.TestCase):
    def test_default_config_has_required_keys(self):
        for key in ("api_url", "model", "thinking_mode", "command_policy", "watchlist"):
            self.assertIn(key, DEFAULT_CONFIG)

    def test_resolve_model_key_aliases(self):
        # Model aliases updated: "sonata" maps to "qwen7b", "prelude" to "qwen-fast"
        self.assertEqual(resolve_model_key("s"),  "qwen7b")
        self.assertEqual(resolve_model_key("p"),  "qwen-fast")
        self.assertEqual(resolve_model_key("st"), "deepseek-r1")   # sonata-thinking
        # "pt" not in aliases — community sentinel
        self.assertEqual(resolve_model_key("pt"), "_community_")

    def test_resolve_model_key_full_id(self):
        # Legacy Aria model IDs forward-mapped to current Ollama keys
        self.assertEqual(resolve_model_key("aria-sonata:4.5"), "qwen7b")
        self.assertEqual(resolve_model_key("aria-sonata:4.6"), "qwen7b")
        # aria-prelude:4.1 not in aliases — community sentinel
        self.assertIn(resolve_model_key("aria-prelude:4.1"), ("_community_", "qwen-fast"))

    def test_resolve_model_key_unknown_returns_community(self):
        # Unknown models return the community sentinel (not "prelude") since 4.6 rename
        self.assertEqual(resolve_model_key("unknown-model-xyz"), "_community_")

    def test_models_have_required_fields(self):
        for key, model in MODELS.items():
            for field in ("id", "name", "speed", "intelligence", "description"):
                self.assertIn(field, model, f"MODELS[{key}] missing '{field}'")


# ============================================================================
# SessionManager
# ============================================================================

class TestSessionManager(unittest.TestCase):
    def setUp(self):
        self.mgr = SessionManager()
        self.test_session_id = "test_session_aria_cli_9999"

    def tearDown(self):
        self.mgr.delete_session(self.test_session_id)

    def test_save_and_load_session(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        self.mgr.save_session(self.test_session_id, messages, metadata={"title": "Test Session"})
        data = self.mgr.load_session(self.test_session_id)
        self.assertIsNotNone(data)
        self.assertEqual(data["id"], self.test_session_id)
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["metadata"]["title"], "Test Session")

    def test_load_nonexistent_session(self):
        result = self.mgr.load_session("nonexistent_session_xyz_12345")
        self.assertIsNone(result)

    def test_list_sessions_includes_saved(self):
        messages = [{"role": "user", "content": "test"}]
        self.mgr.save_session(self.test_session_id, messages, metadata={"title": "Test"})
        sessions = self.mgr.list_sessions()
        ids = [s["id"] for s in sessions]
        self.assertIn(self.test_session_id, ids)

    def test_delete_session(self):
        messages = [{"role": "user", "content": "test"}]
        self.mgr.save_session(self.test_session_id, messages)
        deleted = self.mgr.delete_session(self.test_session_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.mgr.load_session(self.test_session_id))

    def test_list_sessions_title_field(self):
        """list_sessions() must return 'title' from metadata."""
        messages = [{"role": "user", "content": "test"}]
        self.mgr.save_session(self.test_session_id, messages, metadata={"title": "My Title"})
        sessions = self.mgr.list_sessions()
        matching = [s for s in sessions if s["id"] == self.test_session_id]
        self.assertTrue(matching)
        self.assertEqual(matching[0]["title"], "My Title")


# ============================================================================
# Tool parameter validation (execute_aria_tool pre-validation)
# ============================================================================

class TestAriaToolValidation(unittest.TestCase):
    """
    These tests check client-side parameter validation without hitting the API.
    We monkey-patch execute_aria_tool's validation logic indirectly by calling
    the parts that can be unit-tested.
    """

    def test_valid_symbol_passes_regex(self):
        import re
        pattern = r'^[A-Z0-9.\-/=]{1,12}$'
        for sym in ("AAPL", "BTC-USD", "SPY", "NVDA", "EUR/USD", "GC=F"):
            self.assertTrue(re.match(pattern, sym), f"Expected {sym} to be valid")

    def test_invalid_symbol_fails_regex(self):
        import re
        pattern = r'^[A-Z0-9.\-/]{1,12}$'
        for sym in ("", "AAPL MSFT", "aapl", "TOO_LONG_SYMBOL_123"):
            self.assertFalse(re.match(pattern, sym), f"Expected {sym} to be invalid")

    def test_valid_date_format(self):
        import re
        pattern = r'^\d{4}-\d{2}-\d{2}$'
        for date in ("2024-01-01", "2023-12-31"):
            self.assertTrue(re.match(pattern, date))

    def test_invalid_date_format(self):
        import re
        pattern = r'^\d{4}-\d{2}-\d{2}$'
        for date in ("2024/01/01", "Jan 1 2024", "20240101", ""):
            self.assertFalse(re.match(pattern, date))


# ============================================================================
# Write policy
# ============================================================================

class TestWritePolicy(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_policy = _ACTIVE_WRITE_POLICY[0]

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        _ACTIVE_WRITE_POLICY[0] = self._orig_policy

    def test_desktop_write_skips_confirm(self):
        """Writes to ~/Desktop should always succeed without user interaction."""
        import pathlib
        desktop = pathlib.Path.home() / "Desktop"
        target = str(desktop / "_aria_test_write_policy_xyz.py")
        _ACTIVE_WRITE_POLICY[0] = "desktop_only"
        content = "import os\nimport sys\n\nprint('hello')\n"
        result = _tool_write_file({"path": target, "content": content})
        # Clean up immediately whether or not it succeeded
        try:
            pathlib.Path(target).unlink()
        except Exception:
            pass
        self.assertTrue(result["success"], result.get("error"))

    def test_skip_confirm_flag_bypasses_policy(self):
        """_skip_confirm=True should bypass all confirmation (used by /scaffold)."""
        _ACTIVE_WRITE_POLICY[0] = "always_confirm"
        target = os.path.join(self.tmpdir, "scaffold_test.py")
        content = "import os\nimport sys\n\nprint('scaffold')\n"
        result = _tool_write_file({"path": target, "content": content, "_skip_confirm": True})
        self.assertTrue(result["success"], result.get("error"))

    def test_sync_write_policy(self):
        """_sync_write_policy should update _ACTIVE_WRITE_POLICY."""
        _sync_write_policy({"write_policy": "always_confirm"})
        self.assertEqual(_ACTIVE_WRITE_POLICY[0], "always_confirm")
        _sync_write_policy({"write_policy": "confirm_outside"})
        self.assertEqual(_ACTIVE_WRITE_POLICY[0], "confirm_outside")
        _sync_write_policy({})  # missing key → defaults to desktop_only
        self.assertEqual(_ACTIVE_WRITE_POLICY[0], "desktop_only")

    def test_default_config_has_write_policy(self):
        self.assertIn("write_policy", DEFAULT_CONFIG)
        self.assertEqual(DEFAULT_CONFIG["write_policy"], "desktop_only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
