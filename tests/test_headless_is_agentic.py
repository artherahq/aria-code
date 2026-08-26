"""Headless `-p` must run the same agent loop the REPL runs.

It used to call the provider once, advertise tool schemas, and never execute
what came back — so every non-interactive user (CI, a pipe, the eval harness)
got a chat reply where the REPL would have done the work. A real eval run
scored 0/5 before this was found.
"""

import inspect
import unittest

from aria_code.apps.cli.intent_signals import classify


class HeadlessPathTests(unittest.TestCase):
    def _source(self) -> str:
        import aria_code.aria_cli as cli

        return inspect.getsource(cli.ArtheraTerminal.run_prompt)

    def test_run_prompt_goes_through_the_shared_runtime(self):
        self.assertIn("run_chat_via_runtime", self._source())

    def test_run_prompt_no_longer_calls_the_provider_directly(self):
        # A single provider round is exactly the bug: tools advertised,
        # nothing executed, no results fed back, no acceptance gate.
        # Comments are stripped first — the code comment that explains the old
        # behaviour names it, and matching that would pass forever.
        code = "\n".join(
            line for line in self._source().splitlines()
            if not line.lstrip().startswith("#")
        )
        self.assertNotIn("stream_provider_result", code)

    def test_headless_does_not_wait_on_an_approval_prompt(self):
        # No approval UI exists here; a populated confirm set would block every
        # write on a prompt nobody can answer.
        source = self._source()
        self.assertIn("confirm_tools", source)
        self.assertIn("_auto_approve_session", source)

    def test_a_failed_acceptance_check_is_surfaced(self):
        # Nobody watched the run, so a turn that changed files and failed its
        # checks must say so rather than exit quietly.
        self.assertIn("验收未通过", self._source())


class DefectVocabularyTests(unittest.TestCase):
    """Plain-language bug reports must reach the coding path."""

    def test_a_plainly_worded_defect_report_is_a_coding_task(self):
        for message in (
            "这个项目的测试挂了，找出原因并修好",
            "跑不通，帮我看看",
            "net_settlement() 会接受任何退款记录，按测试补上校验",
            "the failing test needs fixing",
            "there is an off-by-one somewhere",
        ):
            with self.subTest(message=message):
                self.assertEqual(classify(message), "coding")

    def test_the_finance_and_conceptual_routes_are_unchanged(self):
        # The new vocabulary must not steal messages from the other intents.
        self.assertEqual(classify("分析 $AAPL"), "analysis")
        self.assertEqual(classify("什么是市盈率"), "general")
        self.assertEqual(classify("最近有什么新闻"), "realtime")
        self.assertEqual(classify("杭州房价怎么样"), "general")

    def test_the_message_that_motivated_the_pack_contract_is_still_general(self):
        self.assertEqual(classify("根据以上分析和建议开始完善"), "general")


if __name__ == "__main__":
    unittest.main()
