import unittest
from unittest.mock import patch

from prompt_toolkit.document import Document
from prompt_toolkit.layout.processors import TransformationInput

from ui.input_box import PanelInputConfig, PlaceholderProcessor, detect_terminal_theme, _build_style


class InputBoxTests(unittest.TestCase):
    def test_panel_input_config_defaults(self):
        config = PanelInputConfig()

        self.assertIn("›", config.prompt)
        self.assertIn("Aria", config.placeholder)

    def test_panel_style_builds_prompt_toolkit_style(self):
        style = _build_style(PanelInputConfig(theme="dark").resolved())

        self.assertIsNotNone(style)

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


if __name__ == "__main__":
    unittest.main()
