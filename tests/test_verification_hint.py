"""Write tools tell the caller which checks the change calls for.

The hint used to be three pasted copies of the same block, differing only in
what the result dict and the console were called in each enclosing function.
They papered over that with `_wdata if "_wdata" in locals() else _data` and
`locals().get("console", locals().get("_console2"))` — both names compiled
unconditionally, so whichever was absent in a given function was an undefined
reference. Three F821s, kept from raising only by a bare `except Exception:
pass` that would equally have hidden a real failure.
"""

import os
import pathlib
import tempfile
import unittest

from aria_code.apps.cli.tools.write_tools import _attach_verification_hint

SOURCE = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"


class HintTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self._cwd)

    def test_a_python_change_gets_a_command(self):
        path = self.root / "calc.py"
        path.write_text(SOURCE, encoding="utf-8")
        data = {}
        _attach_verification_hint(data, path)
        self.assertIn("suggested_verification", data)
        self.assertIn("py_compile", data["suggested_verification"])

    def test_it_prints_only_when_a_console_is_supplied(self):
        class _Console:
            def __init__(self):
                self.lines = []

            def print(self, text):
                self.lines.append(text)

        path = self.root / "calc.py"
        path.write_text(SOURCE, encoding="utf-8")

        quiet = _Console()
        _attach_verification_hint({}, path, quiet, has_rich=False)
        self.assertEqual(quiet.lines, [])

        loud = _Console()
        _attach_verification_hint({}, path, loud, has_rich=True)
        self.assertTrue(loud.lines)

    def test_a_console_that_raises_does_not_break_the_write(self):
        class _Broken:
            def print(self, text):
                raise RuntimeError("no terminal")

        path = self.root / "calc.py"
        path.write_text(SOURCE, encoding="utf-8")
        data = {}
        _attach_verification_hint(data, path, _Broken(), has_rich=True)
        self.assertIn("suggested_verification", data)   # still recorded

    def test_an_unplannable_file_adds_nothing(self):
        path = self.root / "notes.txt"
        path.write_text("just prose\n", encoding="utf-8")
        data = {}
        _attach_verification_hint(data, path)
        self.assertNotIn("suggested_verification", data)

    def test_a_planner_failure_is_survivable(self):
        import aria_code.workspace.verify as verify

        original = verify.VerificationPlanner
        verify.VerificationPlanner = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            data = {}
            _attach_verification_hint(data, self.root / "calc.py")
            self.assertEqual(data, {})
        finally:
            verify.VerificationPlanner = original


class CallSiteTests(unittest.TestCase):
    """All three write tools must attach it, and none may reach for locals()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self._cwd)

    def test_write_edit_and_multi_edit_all_attach_the_hint(self):
        from aria_code.aria_cli import _tool_edit_file, _tool_multi_edit, _tool_write_file

        path = str(self.root / "calc.py")

        written = _tool_write_file({"path": path, "content": SOURCE})
        self.assertIn("suggested_verification", written["data"])

        edited = _tool_edit_file({"path": path, "old_string": "a + b", "new_string": "a+b"})
        self.assertIn("suggested_verification", edited["data"])

        multi = _tool_multi_edit({
            "path": path,
            "edits": [{"old_string": "a * b", "new_string": "a*b"}],
        })
        self.assertIn("suggested_verification", multi["data"])

    def test_no_write_tool_guesses_variable_names_from_locals(self):
        import inspect

        from aria_code.apps.cli.tools import write_tools

        # Comments and docstrings quote the old pattern to explain it, so
        # strip them before matching or this passes forever.
        import io
        import tokenize

        source = inspect.getsource(write_tools)
        code = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type not in (tokenize.COMMENT, tokenize.STRING):
                code.append(token.string)
        body = " ".join(code)

        self.assertNotIn('"_wdata" in locals()', body)
        self.assertNotIn("locals ( ) . get", body)


if __name__ == "__main__":
    unittest.main()
