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
    _build_style,
    _input_rule,
)


class InputBoxTests(unittest.TestCase):
    def test_panel_input_config_defaults(self):
        config = PanelInputConfig()

        self.assertIn("›", config.prompt)
        self.assertIn("Aria", config.placeholder)

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


if __name__ == "__main__":
    unittest.main()
