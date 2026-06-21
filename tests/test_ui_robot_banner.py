import io
import unittest

from rich import box
from rich.console import Console

from ui.banner import render_full_banner
from ui.robot import ROBOT_ROW_COUNT, RobotState, get_robot_row, get_status_dot, set_robot_state


class RobotBannerTests(unittest.TestCase):
    def tearDown(self):
        set_robot_state(RobotState.IDLE)

    def test_robot_idle_face_is_compact_and_open_eyed(self):
        set_robot_state(RobotState.IDLE)

        rows = ["".join(text for _, text in get_robot_row(2, row)) for row in range(ROBOT_ROW_COUNT)]

        self.assertEqual(rows, [
            "  ▄▀▀▀▀▀▀▀▀▀▀▀▀▄  ",
            " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ ",
            "▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀",
            " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀ ",
            "  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ",
            "  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ",
            "   ▀▀  ▀▀  ▀▀  ▀▀ ",
        ])

    def test_robot_uses_halfblock_styles_for_screen_and_accents(self):
        set_robot_state(RobotState.IDLE)

        styles = [style for row in range(ROBOT_ROW_COUNT) for style, _ in get_robot_row(2, row)]

        self.assertIn("#0d1117 on #0d1117", styles)
        self.assertIn("#0d1117 on #fffaf0", styles)
        self.assertIn("#0d1117 on #ffb35c", styles)
        self.assertIn("#ffb35c on #f2eadc", styles)

    def test_idle_status_dot_does_not_blink_to_dim_dot(self):
        set_robot_state(RobotState.IDLE)

        text = "".join(fragment for _, fragment in get_status_dot(0))

        self.assertEqual(text, "•")

    def test_full_banner_uses_pixel_robot_and_minimal_startup_info(self):
        console = Console(file=io.StringIO(), record=True, width=100, force_terminal=False)

        render_full_banner(
            version="4.1.0",
            rt_label="GPT-OSS 120B  cloud",
            cwd="~/Desktop/aria-code",
            control_status_rich="workspace-write · network on · privacy local-only",
            ollama_status_rich="Ollama online · 3 models",
            tool_count=71,
            skill_count=14,
            console=console,
            has_rich=True,
            rich_box=box,
            lang="en",
        )

        rendered = console.export_text()
        self.assertIn("~/Desktop/aria-code", rendered)
        self.assertNotIn("71 tools", rendered)
        self.assertNotIn("14 skills", rendered)
        self.assertNotIn("┌──┐", rendered)
        self.assertNotIn("╔══════════════╗", rendered)
        self.assertNotIn("╭────────╮", rendered)
        self.assertNotIn("┌────────────────", rendered)


if __name__ == "__main__":
    unittest.main()
