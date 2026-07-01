import unittest

from apps.cli.prompt_assembly import (
    ANALYSIS_COMMENTARY_PROMPT_ZH,
    build_base_message,
    should_prepend_file_tool_hint,
    with_ml_signal_prefix,
)


class BuildBaseMessageTests(unittest.TestCase):
    def test_plain_message_when_nothing_else_applies(self):
        self.assertEqual(
            build_base_message("AAPL price", wants_analysis_commentary=False),
            "AAPL price",
        )

    def test_analysis_commentary_wins_outright(self):
        result = build_base_message(
            "AAPL price",
            wants_analysis_commentary=True,
            decomposition_plan="1. do a thing",
        )
        self.assertEqual(result, ANALYSIS_COMMENTARY_PROMPT_ZH)

    def test_decomposition_plan_prefixes_the_message_when_no_analysis(self):
        result = build_base_message(
            "build me a report",
            wants_analysis_commentary=False,
            decomposition_plan="1. gather data\n2. write report",
        )
        self.assertIn("[执行计划]", result)
        self.assertIn("1. gather data\n2. write report", result)
        self.assertIn("[用户请求]", result)
        self.assertIn("build me a report", result)

    def test_empty_decomposition_plan_falls_back_to_raw_message(self):
        self.assertEqual(
            build_base_message("AAPL price", wants_analysis_commentary=False, decomposition_plan=""),
            "AAPL price",
        )


class ShouldPrependFileToolHintTests(unittest.TestCase):
    def test_true_when_no_analysis_and_no_reference_context(self):
        self.assertTrue(should_prepend_file_tool_hint(False, ""))

    def test_false_in_analysis_commentary_mode(self):
        self.assertFalse(should_prepend_file_tool_hint(True, ""))

    def test_false_when_reference_context_present(self):
        self.assertFalse(should_prepend_file_tool_hint(False, "@src/foo.py contents..."))

    def test_false_when_both_analysis_and_reference_context(self):
        self.assertFalse(should_prepend_file_tool_hint(True, "@src/foo.py contents..."))


class WithMlSignalPrefixTests(unittest.TestCase):
    def test_no_signal_returns_message_unchanged(self):
        self.assertEqual(with_ml_signal_prefix("AAPL price", ""), "AAPL price")

    def test_signal_prepends_reference_block(self):
        result = with_ml_signal_prefix("AAPL price", "5-day forecast: +2.1%")
        self.assertTrue(result.startswith("[ML信号参考"))
        self.assertIn("5-day forecast: +2.1%", result)
        self.assertTrue(result.endswith("AAPL price"))


if __name__ == "__main__":
    unittest.main()
