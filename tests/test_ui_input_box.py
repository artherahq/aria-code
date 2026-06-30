import asyncio
import os
import unittest
from unittest.mock import patch

from prompt_toolkit.document import Document
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import TransformationInput

from ui.input_box import (
    INPUT_MAX_HEIGHT,
    PanelInputConfig,
    PlaceholderProcessor,
    detect_terminal_theme,
    run_panel_input,
    run_panel_input_async,
    _build_style,
    _input_rule,
    _status_bar,
)


class InputBoxTests(unittest.TestCase):
    def test_panel_input_config_defaults(self):
        config = PanelInputConfig().resolved()

        self.assertIn("›", config.prompt)
        self.assertIn("Aria", config.placeholder)

    def test_placeholder_follows_ui_language(self):
        english = PanelInputConfig(theme="dark", lang="en").resolved()
        chinese = PanelInputConfig(theme="dark", lang="zh").resolved()

        self.assertIn("Ask Aria", english.placeholder)
        self.assertIn("问 Aria", chinese.placeholder)
        self.assertNotEqual(english.placeholder, chinese.placeholder)

    def test_panel_style_builds_prompt_toolkit_style(self):
        style = _build_style(PanelInputConfig(theme="dark").resolved())

        self.assertIsNotNone(style)

    def test_panel_input_rule_is_lightweight_divider(self):
        rule = _input_rule(PanelInputConfig(theme="dark").resolved())
        text = "".join(fragment for _, fragment in rule)

        self.assertTrue(text)
        self.assertEqual(set(text), {"─"})

    def test_panel_config_resolves_dark_and_light_themes(self):
        dark = PanelInputConfig(theme="dark").resolved()
        light = PanelInputConfig(theme="light").resolved()

        self.assertTrue(dark.fg)
        self.assertTrue(dark.accent)
        self.assertTrue(light.fg)
        self.assertTrue(light.accent)
        self.assertNotEqual(dark.fg, light.fg)

    def test_detect_terminal_theme_returns_supported_value(self):
        self.assertIn(detect_terminal_theme(), {"dark", "light"})

    def test_detect_terminal_theme_respects_explicit_env(self):
        with patch.dict("os.environ", {"ARIA_INPUT_THEME": "light"}, clear=False):
            self.assertEqual(detect_terminal_theme(), "light")
        with patch.dict("os.environ", {"ARIA_INPUT_THEME": "dark"}, clear=False):
            self.assertEqual(detect_terminal_theme(), "dark")

    @patch("ui.input_box.shutil.get_terminal_size", return_value=os.terminal_size((80, 24)))
    def test_status_bar_prioritizes_runtime_state_without_repeating_full_path(self, _size):
        config = PanelInputConfig(
            theme="dark",
            model_label="gpt-oss:120b-cloud",
            cwd="/Users/mac/Desktop/aria-code",
            est_tokens=4096,
            max_tokens=16384,
            permission_mode="workspace-write",
            git_branch="main",
            git_dirty=True,
            mcp_running=1,
            mcp_total=1,
            mcp_tool_count=20,
        ).resolved()

        text = "".join(fragment for _, fragment in _status_bar(config))

        self.assertIn("main*", text)
        self.assertIn("MCP 20", text)
        self.assertIn("rw", text)
        self.assertIn("ctx 25%", text)
        self.assertNotIn("/Users/mac/Desktop", text)

    @patch("ui.input_box.shutil.get_terminal_size", return_value=os.terminal_size((80, 24)))
    def test_status_bar_does_not_round_nonzero_context_to_zero(self, _size):
        config = PanelInputConfig(
            theme="dark",
            model_label="gpt-oss:120b-cloud",
            cwd="/tmp/project",
            est_tokens=128,
            max_tokens=131072,
        ).resolved()

        text = "".join(fragment for _, fragment in _status_bar(config))

        self.assertIn("ctx <1%", text)
        self.assertNotIn("ctx 0%", text)

    def test_placeholder_processor_adds_prefix_and_placeholder_when_empty(self):
        prefix_frags = [("class:prompt", "> ")]
        processor = PlaceholderProcessor(lambda: prefix_frags, "hint", lambda: True)
        transformed = processor.apply_transformation(
            TransformationInput(
                buffer_control=None,
                document=Document(""),
                lineno=0,
                source_to_display=lambda i: i,
                fragments=[],
                width=80,
                height=1,
            )
        )

        self.assertEqual(transformed.fragments, [
            ("class:prompt", "> "),
            ("class:ph", "hint"),
        ])
        self.assertEqual(transformed.source_to_display(0), len("> "))
        self.assertEqual(transformed.display_to_source(len("> ") + len("hint")), 0)

    def test_placeholder_processor_omits_placeholder_when_not_empty(self):
        prefix_frags = [("class:prompt", "> ")]
        processor = PlaceholderProcessor(lambda: prefix_frags, "hint", lambda: False)
        transformed = processor.apply_transformation(
            TransformationInput(
                buffer_control=None,
                document=Document("x"),
                lineno=0,
                source_to_display=lambda i: i,
                fragments=[("", "x")],
                width=80,
                height=1,
            )
        )

        self.assertEqual(transformed.fragments, [("class:prompt", "> "), ("", "x")])
        self.assertEqual(transformed.source_to_display(1), len("> ") + 1)
        self.assertEqual(transformed.display_to_source(len("> ") + 1), 1)

    def test_placeholder_processor_mapping_does_not_call_upstream_mapping(self):
        prefix_frags = [("class:prompt", "> ")]
        processor = PlaceholderProcessor(lambda: prefix_frags, "hint", lambda: True)

        def recursive_mapping(_index):
            raise RecursionError("upstream mapping should not be called")

        transformed = processor.apply_transformation(
            TransformationInput(
                buffer_control=None,
                document=Document(""),
                lineno=0,
                source_to_display=recursive_mapping,
                fragments=[],
                width=80,
                height=1,
            )
        )

        self.assertEqual(transformed.source_to_display(0), len("> "))

    def test_panel_input_wraps_and_expands_within_max_height(self):
        captured = {}
        created_windows = []

        class FakeTextArea:
            def __init__(self, **kwargs):
                captured["textarea"] = kwargs
                self.text = "long prompt"
                self.buffer = object()
                self._window = Window(content=FormattedTextControl(""))

            def __pt_container__(self):
                return self._window

        class FakeApplication:
            def __init__(self, **kwargs):
                captured["application"] = kwargs

            def run(self):
                return "long prompt"

        original_window = Window

        def tracking_window(*args, **kwargs):
            window = original_window(*args, **kwargs)
            created_windows.append(window)
            return window

        with (
            patch("ui.input_box.TextArea", FakeTextArea),
            patch("ui.input_box.Application", FakeApplication),
            patch("ui.input_box.Window", tracking_window),
        ):
            self.assertEqual(run_panel_input(config=PanelInputConfig(theme="dark")), "long prompt")

        height = captured["textarea"]["height"]
        self.assertIsInstance(height, Dimension)
        self.assertEqual(height.min, 1)
        self.assertEqual(height.max, INPUT_MAX_HEIGHT)
        self.assertTrue(captured["textarea"]["multiline"])
        self.assertTrue(captured["textarea"]["wrap_lines"])
        self.assertTrue(captured["textarea"]["dont_extend_height"])

        rule_windows = [
            window for window in created_windows
            if getattr(window, "height", None) == 1 and "FormattedTextControl" in type(window.content).__name__
        ]
        self.assertGreaterEqual(len(rule_windows), 3)

    def test_panel_input_disables_cpr_probe_only_while_running(self):
        seen = []

        class FakeApplication:
            def run(self):
                seen.append(os.environ.get("PROMPT_TOOLKIT_NO_CPR"))
                return "ok"

        with (
            patch("ui.input_box._build_panel_input_application", return_value=FakeApplication()),
            patch.dict("os.environ", {}, clear=False),
        ):
            os.environ.pop("PROMPT_TOOLKIT_NO_CPR", None)
            self.assertEqual(run_panel_input(), "ok")
            self.assertNotIn("PROMPT_TOOLKIT_NO_CPR", os.environ)

        self.assertEqual(seen, ["1"])

    def test_async_panel_runs_on_active_event_loop_with_cpr_disabled(self):
        seen = []

        class FakeApplication:
            async def run_async(self):
                seen.append((
                    os.environ.get("PROMPT_TOOLKIT_NO_CPR"),
                    asyncio.get_running_loop(),
                ))
                return "中文输入"

        async def exercise():
            with patch(
                "ui.input_box._build_panel_input_application",
                return_value=FakeApplication(),
            ):
                return await run_panel_input_async()

        result = asyncio.run(exercise())

        self.assertEqual(result, "中文输入")
        self.assertEqual(seen[0][0], "1")


if __name__ == "__main__":
    unittest.main()
