"""Regression tests for the "intent without action" nudge.

Symptom: a Chinese project review ended with a stray "NOW" hanging off the last
sentence.  Two defects combined.

  1. The intent word list mixed first-person commitments ("let me", "我会") with
     ordinary Chinese discourse connectives ("下面", "接下来", "检查", "我需要").
     Any Chinese prose answer tripped it, so the loop injected
     "Output a <tool_call> NOW" as a user turn, and the follow-up round echoed
     the imperative into the visible answer.

  2. The nudge branch pushed the round's prose into the request payload but not
     into ``response_segments``, so the real answer was dropped from the
     returned text while the nudged fragment survived.  Only the streamed
     tokens made the terminal look correct.

This module covers (1); (2) is covered by the segment-retention test below,
which asserts on the predicate contract the loop depends on.
"""

import unittest

from aria_code.apps.cli.action_nudge import (
    MAX_NUDGES,
    looks_like_stated_intent,
    should_nudge_for_action,
)

# Shortened from the review that produced the bug.
CHINESE_PROSE_REVIEW = (
    "好的，《虚拟电厂》项目的文件中存在一些可能的问题与改进空间：\n"
    "1. 明确的目标市场及受众分析不足。在目标市场的定位上不够清晰。\n"
    "改进建议：确保产品介绍部分详细描述其核心价值主张。\n"
    "下面是架构和技术选型的建议。接下来需要检查安全措施。\n"
    "希望这些建议对您有所帮助！如果有更多细节需要讨论，请随时告知我。"
)


class StatedIntentTests(unittest.TestCase):
    def test_chinese_prose_review_is_not_an_intent(self):
        self.assertFalse(looks_like_stated_intent(CHINESE_PROSE_REVIEW))

    def test_discourse_connectives_are_not_intents(self):
        for text in ("下面是架构建议", "接下来是安全措施", "需要检查权限控制",
                     "我需要更多信息", "重新审视这个设计"):
            with self.subTest(text=text):
                self.assertFalse(looks_like_stated_intent(text))

    def test_real_commitments_are_still_detected(self):
        for text in ("Let me read the file first.", "I'll run the tests now.",
                     "让我先看一下这个文件", "我这就调用工具", "我会调用 read_file"):
            with self.subTest(text=text):
                self.assertTrue(looks_like_stated_intent(text))

    def test_empty_text_is_not_an_intent(self):
        self.assertFalse(looks_like_stated_intent(""))
        self.assertFalse(looks_like_stated_intent(None))


class NudgeDecisionTests(unittest.TestCase):
    def test_prose_answer_is_not_nudged(self):
        self.assertFalse(should_nudge_for_action(CHINESE_PROSE_REVIEW))

    def test_error_recovery_nudges_regardless_of_wording(self):
        self.assertTrue(should_nudge_for_action("done", in_error_recovery=True))

    def test_failed_tool_nudges_regardless_of_wording(self):
        self.assertTrue(should_nudge_for_action("done", last_tool_had_error=True))

    def test_stated_intent_nudges(self):
        self.assertTrue(should_nudge_for_action("Let me check the config."))

    def test_no_nudge_without_tools_to_call(self):
        # Nudging a model that has no tools yields an imperative it can only
        # echo — which is exactly how "NOW" reached the user.
        self.assertFalse(
            should_nudge_for_action("Let me check.", tools_available=False)
        )
        self.assertFalse(
            should_nudge_for_action(
                "done", in_error_recovery=True, tools_available=False
            )
        )

    def test_nudges_are_capped(self):
        self.assertTrue(
            should_nudge_for_action("Let me check.", nudge_count=MAX_NUDGES - 1)
        )
        self.assertFalse(
            should_nudge_for_action("Let me check.", nudge_count=MAX_NUDGES)
        )
        self.assertFalse(
            should_nudge_for_action(
                "done", in_error_recovery=True, nudge_count=MAX_NUDGES
            )
        )


class NudgePathIntegrationTests(unittest.TestCase):
    """The loop must keep prose produced before a nudge."""

    def test_nudge_branch_appends_to_response_segments(self):
        # Guards defect (2): the source must retain the round's prose before
        # continuing, the same way the length-continuation branch does.
        import inspect

        from aria_code.apps.cli.providers.llm import ollama_stream

        source = inspect.getsource(ollama_stream)
        nudge_branch = source.split('payload["messages"].append({"role": "user", "content": nudge})')[0]
        tail = nudge_branch[-800:]
        self.assertIn("response_segments.append(full_response.rstrip())", tail)


if __name__ == "__main__":
    unittest.main()
