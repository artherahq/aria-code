import io
import unittest

from rich import box
from rich.console import Console

import ui.robot as robot
from ui.banner import render_full_banner
from ui.robot import ROBOT_ROW_COUNT, RobotState, get_robot_row, get_status_dot, set_robot_state


class RobotBannerTests(unittest.TestCase):
    def setUp(self):
        robot._theme_cache = "dark"  # deterministic palette for assertions

    def tearDown(self):
        set_robot_state(RobotState.IDLE)
        robot._theme_cache = None

    def test_robot_idle_face_is_compact_and_open_eyed(self):
        set_robot_state(RobotState.IDLE)

        rows = ["".join(text for _, text in get_robot_row(2, row)) for row in range(ROBOT_ROW_COUNT)]

        self.assertEqual(rows, [
            " ▄▄▄▄▄▄▄▄▄ ",
            "           ",
            "▪  ▀   ▬  ▪",
            " ▬▬▬▬▬▬▬▬▬ ",
            "  ▀ ▀ ▀ ▀  ",
        ])

    def test_robot_uses_distinct_styles_for_screen_and_accents(self):
        set_robot_state(RobotState.IDLE)

        styles = [style for row in range(ROBOT_ROW_COUNT) for style, _ in get_robot_row(2, row)]

        self.assertIn("on #0d1117", styles)          # dark screen
        self.assertIn("#f6f2ea on #0d1117", styles)  # light square eye
        self.assertIn("#C08050 on #0d1117", styles)  # copper dash
        self.assertIn("on #e8e2d4", styles)          # light shell body

    def test_robot_palette_follows_theme(self):
        robot._theme_cache = "light"
        light = [s for row in range(ROBOT_ROW_COUNT) for s, _ in get_robot_row(2, row)]
        robot._theme_cache = "dark"
        dark = [s for row in range(ROBOT_ROW_COUNT) for s, _ in get_robot_row(2, row)]

        self.assertNotEqual(light, dark)
        self.assertIn("#E7E1D3", light)  # warm top cap
        self.assertIn("on #0D1117", light)  # dark screen on light terminal too
        self.assertIn("#F6F2EA on #0D1117", light)  # light eye on dark screen
        self.assertIn("on #E7E1D3", light)  # warm shell on a light terminal
        self.assertIn("#9A6700 on #0D1117", light)  # copper face accent on dark screen
        self.assertIn("on #e8e2d4", dark)   # light shell on a dark terminal

    def test_idle_status_dot_does_not_blink_to_dim_dot(self):
        set_robot_state(RobotState.IDLE)

        text = "".join(fragment for _, fragment in get_status_dot(0))

        self.assertEqual(text, "•")

    def test_full_banner_uses_pixel_robot_and_runtime_dashboard(self):
        console = Console(file=io.StringIO(), record=True, width=120, force_terminal=False)

        render_full_banner(
            version="4.1.0",
            rt_label="GPT-OSS 120B  cloud",
            cwd="~/Desktop/aria-code",
            control_status_rich="workspace-write · network on · privacy local-only",
            ollama_status_rich="Ollama online · 3 models",
            tool_count=71,
            skill_count=14,
            first_run=True,
            console=console,
            has_rich=True,
            rich_box=box,
            lang="en",
        )

        rendered = console.export_text()
        self.assertIn("~/Desktop/aria-code", rendered)
        self.assertIn("71 tools", rendered)
        self.assertIn("Quick start", rendered)
        self.assertIn("workspace-write", rendered)
        self.assertNotIn("┌──┐", rendered)
        self.assertNotIn("╔══════════════╗", rendered)
        self.assertIn("╭", rendered)


if __name__ == "__main__":
    unittest.main()
