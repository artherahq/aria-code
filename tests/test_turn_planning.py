import unittest

from apps.cli.turn_planning import (
    DECOMP_THRESHOLD,
    is_complex_task,
    round_budget_for,
    should_decompose,
)


class TurnPlanningTests(unittest.TestCase):
    def test_short_plain_message_is_not_complex(self):
        self.assertFalse(is_complex_task("what's AAPL trading at"))

    def test_long_message_is_complex(self):
        self.assertTrue(is_complex_task("x" * 121))

    def test_length_threshold_boundary(self):
        self.assertFalse(is_complex_task("x" * 120))
        self.assertTrue(is_complex_task("x" * 121))

    def test_keyword_triggers_complexity_regardless_of_length(self):
        self.assertTrue(is_complex_task("然后帮我下单"))
        self.assertTrue(is_complex_task("and then check the portfolio"))
        self.assertTrue(is_complex_task("give me a comprehensive summary"))

    def test_round_budget_simple_vs_complex(self):
        self.assertEqual(round_budget_for(is_complex=False), (16, 26))
        self.assertEqual(round_budget_for(is_complex=True), (30, 50))

    def test_should_decompose_requires_length_and_complexity(self):
        long_complex = "then " * 40  # >150 chars, contains "then"... use explicit keyword
        long_complex = "and then " + ("x" * 150)
        self.assertGreater(len(long_complex), DECOMP_THRESHOLD)
        self.assertTrue(is_complex_task(long_complex))
        self.assertTrue(should_decompose(long_complex, is_complex=True))

    def test_should_decompose_false_below_threshold(self):
        short_complex = "and then check"
        self.assertLessEqual(len(short_complex), DECOMP_THRESHOLD)
        self.assertFalse(should_decompose(short_complex, is_complex=True))

    def test_should_decompose_false_when_not_complex(self):
        # should_decompose takes is_complex as an explicit input (not recomputed),
        # so a long message still needs is_complex=False to withhold decomposition.
        long_message = "x" * 200
        self.assertFalse(should_decompose(long_message, is_complex=False))

    def test_slash_and_bang_commands_never_decompose(self):
        long_slash = "/backtest " + ("and then " * 30)
        long_bang = "!" + ("and then " * 30)
        self.assertTrue(should_decompose(long_slash[1:], is_complex=True))  # sanity: body alone would decompose
        self.assertFalse(should_decompose(long_slash, is_complex=True))
        self.assertFalse(should_decompose(long_bang, is_complex=True))


if __name__ == "__main__":
    unittest.main()
