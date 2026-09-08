"""Regression tests for the tool-result context budget.

Tool output is appended to the conversation *mid-turn*, after the pre-turn
compaction check has already run.  Individual branches of _format_tool_summary
applied ad-hoc caps and several applied none, and nothing bounded the total, so
a turn calling many tools could drive the context from comfortable to full with
nothing able to intervene — the 58% → 76% → 95% climb seen in practice.

Two limits are enforced:
  - every tool result is clipped to a per-result ceiling, whichever branch
    produced it, with an explicit marker so a partial result is not mistaken
    for a complete one;
  - a turn's cumulative tool-result spend is bounded by a fraction of the
    model's context window.

Hermetic — no model calls, no network.
"""

import unittest

from aria_code.apps.cli.tool_executor import (
    DEFAULT_TOOL_RESULT_CHAR_LIMIT,
    MIN_TOOL_RESULT_CHAR_LIMIT,
    _format_tool_summary,
    truncate_tool_summary,
)


class TruncationTests(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(truncate_tool_summary("hello"), "hello")

    def test_output_never_exceeds_the_limit(self):
        for size, limit in ((10_000, 4000), (100_000, 4000), (5_000, 400)):
            with self.subTest(size=size, limit=limit):
                self.assertLessEqual(len(truncate_tool_summary("A" * size, limit)), limit)

    def test_truncation_is_announced(self):
        out = truncate_tool_summary("A" * 10_000, 4000)
        self.assertIn("truncated", out)

    def test_head_and_tail_are_both_kept(self):
        text = "HEAD" + ("-" * 10_000) + "TAIL"
        out = truncate_tool_summary(text, 4000)
        self.assertTrue(out.startswith("HEAD"))
        self.assertTrue(out.endswith("TAIL"))

    def test_limit_is_floored_so_summaries_stay_usable(self):
        # A tiny remaining budget must not shrink a result to nothing.
        out = truncate_tool_summary("A" * 10_000, 5)
        self.assertGreaterEqual(len(out), MIN_TOOL_RESULT_CHAR_LIMIT // 2)

    def test_non_string_input_is_handled(self):
        self.assertEqual(truncate_tool_summary(None), "None")


class FormatToolSummaryTests(unittest.TestCase):
    def test_unbounded_branch_is_capped(self):
        # read_file previously capped content at 2000 chars; the generic JSON
        # branch and several others had no ceiling at all.
        result = {"success": True, "data": {"lines": 9999, "content": "X" * 500_000}}
        summary = _format_tool_summary("read_file", result)
        self.assertLessEqual(len(summary), DEFAULT_TOOL_RESULT_CHAR_LIMIT)

    def test_remote_json_branch_is_capped(self):
        result = {"success": True, "data": {"payload": ["item"] * 100_000}}
        summary = _format_tool_summary("some_remote_tool", result)
        self.assertLessEqual(len(summary), DEFAULT_TOOL_RESULT_CHAR_LIMIT)

    def test_explicit_char_limit_is_honoured(self):
        result = {"success": True, "data": {"lines": 10, "content": "X" * 50_000}}
        summary = _format_tool_summary("read_file", result, char_limit=800)
        self.assertLessEqual(len(summary), 800)

    def test_small_results_are_not_altered(self):
        result = {"success": True, "data": {"count": 2, "items": ["a.py", "b.py"]}}
        summary = _format_tool_summary("list_files", result)
        self.assertIn("a.py", summary)
        self.assertNotIn("truncated", summary)

    def test_error_results_still_summarise(self):
        summary = _format_tool_summary("run_command", {"success": False, "error": "boom"})
        self.assertIn("boom", summary)


class TurnBudgetTests(unittest.TestCase):
    """The allowance shrinks as a turn spends its budget, then floors."""

    @staticmethod
    def _limit_fn(num_ctx, chars_per_tok=1.5):
        budget = max(MIN_TOOL_RESULT_CHAR_LIMIT * 4, int(num_ctx * chars_per_tok * 0.25))
        used = [0]

        def next_limit():
            remaining = budget - used[0]
            return max(
                MIN_TOOL_RESULT_CHAR_LIMIT,
                min(DEFAULT_TOOL_RESULT_CHAR_LIMIT, remaining),
            )

        return budget, used, next_limit

    def test_budget_scales_with_the_context_window(self):
        small, _, _ = self._limit_fn(8_192)
        large, _, _ = self._limit_fn(1_048_576)
        self.assertGreater(large, small)

    def test_small_window_still_gets_a_workable_budget(self):
        budget, _, next_limit = self._limit_fn(4_096)
        self.assertGreaterEqual(budget, MIN_TOOL_RESULT_CHAR_LIMIT * 4)
        self.assertGreaterEqual(next_limit(), MIN_TOOL_RESULT_CHAR_LIMIT)

    def test_allowance_shrinks_as_the_budget_is_spent(self):
        _, used, next_limit = self._limit_fn(32_768)
        first = next_limit()
        used[0] += 10_000
        second = next_limit()
        self.assertLessEqual(second, first)

    def test_allowance_floors_instead_of_going_negative(self):
        _, used, next_limit = self._limit_fn(32_768)
        used[0] += 10_000_000
        self.assertEqual(next_limit(), MIN_TOOL_RESULT_CHAR_LIMIT)

    def test_a_long_tool_chain_stays_within_a_bounded_total(self):
        budget, used, next_limit = self._limit_fn(32_768)
        for _ in range(25):  # the coding-intent max_tool_rounds
            used[0] += len(truncate_tool_summary("X" * 200_000, next_limit()))
        # Each result floors at MIN rather than hard-stopping, so the ceiling is
        # the budget plus that floor per remaining round — still bounded, and
        # far below the unbounded growth this replaces.
        self.assertLess(used[0], budget + 25 * MIN_TOOL_RESULT_CHAR_LIMIT)


if __name__ == "__main__":
    unittest.main()
