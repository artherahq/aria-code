"""A failure the user lands on routinely must say what to do about it.

Both cases here were reported from a real session: a bare
``No module named 'google.genai'`` with no remedy, and a model picker showing
``[ollama: <urlopen error [Errno 61] Connection ref]`` — a raw exception, cut
mid-word, naming neither cause nor fix.
"""

import inspect
import unittest

from aria_code.apps.cli.commands.model_cmds import _actionable, _pip_name


class ActionableProbeErrorTests(unittest.TestCase):
    def test_a_refused_connection_names_the_command_that_fixes_it(self):
        message = _actionable("ollama", "<urlopen error [Errno 61] Connection refused>")
        self.assertIn("ollama serve", message)
        self.assertNotIn("Errno", message)

    def test_other_local_runtimes_are_named_not_mislabelled(self):
        self.assertIn("lmstudio", _actionable("lmstudio", "[Errno 61] Connection refused"))
        self.assertNotIn("ollama serve", _actionable("lmstudio", "[Errno 61] Connection refused"))

    def test_a_timeout_is_distinguished_from_a_refusal(self):
        self.assertIn("超时", _actionable("ollama", "The read operation timed out"))

    def test_an_unresolvable_host_points_at_the_setting(self):
        self.assertIn("ollama_url", _actionable("ollama", "nodename nor servname provided"))

    def test_a_missing_module_becomes_an_install_command(self):
        self.assertEqual(
            _actionable("vertexai", "No module named 'google.genai'"),
            "缺少 google.genai · pip install google-genai",
        )

    def test_an_unrecognised_error_is_still_shown_but_bounded(self):
        message = _actionable("ollama", "  weird   multiline\n  failure  " + "x" * 200)
        self.assertLessEqual(len(message), 48)
        self.assertNotIn("\n", message)

    def test_a_short_unknown_error_is_passed_through_intact(self):
        self.assertEqual(_actionable("ollama", "HTTP 500"), "HTTP 500")


class PipNameTests(unittest.TestCase):
    def test_namespace_packages_map_to_their_real_install_name(self):
        # Guessing from the module name gives `pip install google`, which
        # installs an unrelated stub and leaves the user exactly as stuck.
        self.assertEqual(_pip_name("google.genai"), "google-genai")
        self.assertEqual(_pip_name("google.genai.errors"), "google-genai")

    def test_an_ordinary_package_needs_no_table_entry(self):
        self.assertEqual(_pip_name("some_pkg"), "some-pkg")
        self.assertEqual(_pip_name("requests"), "requests")


class MissingSdkGuardTests(unittest.TestCase):
    """The friendly message has to be reachable, not just present."""

    def _stream_source(self) -> str:
        from aria_code.apps.cli.providers.vertexai_stream import VertexAIProvider

        return inspect.getsource(VertexAIProvider.stream)

    def test_the_sdk_import_is_inside_the_guarded_block(self):
        # It used to sit above the try, so it raised first and the ImportError
        # handler below it could never run.
        source = self._stream_source()
        import_at = source.index("from google.genai import types")
        try_at = source.index("try:")
        self.assertLess(try_at, import_at, "the SDK import must be inside the try")

    def test_the_message_names_both_ways_out(self):
        from aria_code.apps.cli.providers.vertexai_stream import _MISSING_SDK_MESSAGE

        self.assertIn("pip install google-genai", _MISSING_SDK_MESSAGE)
        self.assertIn("/model", _MISSING_SDK_MESSAGE)


if __name__ == "__main__":
    unittest.main()


class ShimImportTests(unittest.TestCase):
    """The command shims must import the package, not a bare top-level name."""

    def test_no_shim_imports_the_unnamespaced_module(self):
        # `from aria_cli import X` only resolves when something else has
        # already put that bare name on sys.path — true when the CLI is run
        # from the source tree, false in an installed package and false in a
        # fresh interpreter. It surfaced as a ModuleNotFoundError from a test
        # that happened to run first.
        import inspect

        from aria_code.apps.cli.commands import model_cmds

        source = inspect.getsource(model_cmds)
        self.assertNotIn("from aria_cli import", source)

    def test_the_shims_resolve_in_a_clean_interpreter(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c",
             "from aria_code.apps.cli.commands.model_cmds import _get_MODELS;"
             " assert _get_MODELS()"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-500:])


class DuplicateDeclarationTests(unittest.TestCase):
    """Vertex 400s on duplicate tool names where other backends shrug."""

    def _provider(self):
        from aria_code.apps.cli.providers.vertexai_stream import VertexAIProvider

        return VertexAIProvider(model="gemini-2.5-pro", config={})

    def _schema(self, name):
        return {"type": "function", "function": {
            "name": name, "description": "", "parameters": {"type": "object", "properties": {}}}}

    def test_a_duplicate_name_is_declared_once(self):
        tools = self._provider()._tools_to_genai(
            [self._schema("web_fetch"), self._schema("read_file"), self._schema("web_fetch")]
        )
        names = [fd.name for t in tools for fd in (t.function_declarations or [])]
        self.assertEqual(sorted(names), ["read_file", "web_fetch"])

    def test_an_unnamed_schema_is_dropped_rather_than_sent(self):
        tools = self._provider()._tools_to_genai([self._schema(""), self._schema("read_file")])
        names = [fd.name for t in tools for fd in (t.function_declarations or [])]
        self.assertEqual(names, ["read_file"])


class McpSchemaRegistrationTests(unittest.TestCase):
    """Re-registering MCP must not grow the schema list."""

    def _registry_with_one_tool(self):
        from aria_code.mcp_client import MCPToolRegistry

        registry = MCPToolRegistry.__new__(MCPToolRegistry)
        registry._servers = {}
        registry._tool_map = {}
        registry._event_loop = None

        class _Server:
            tools = [{"name": "search", "description": "d", "inputSchema": {"type": "object"}}]

        registry._servers = {"demo": _Server()}
        return registry

    def test_registering_twice_leaves_one_schema_per_tool(self):
        registry = self._registry_with_one_tool()
        tools, schemas = {}, []
        registry.register_into(tools, schemas, overwrite=True)
        registry.register_into(tools, schemas, overwrite=True)

        names = [(s.get("function") or s).get("name") for s in schemas]
        self.assertEqual(len(names), len(set(names)), f"duplicate schemas: {names}")
        self.assertEqual(names, ["mcp__demo__search"])
