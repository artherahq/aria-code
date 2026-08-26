"""Evaluation set for message intent classification.

The classifier had accumulated six layers of counter-patches with zero tests
behind any of them, so each fix was made blind and could silently undo an
earlier one.  This file is the regression net that was missing: every case is
one the classifier must keep getting right, and the four groups at the top are
the failure *classes* the rewrite addresses.

Run this before touching term lists or the precedence table.

Hermetic — pure string classification, no network, no model.
"""

import unittest

from aria_code.apps.cli.intent_signals import (
    INTENT_ANALYSIS,
    INTENT_CODING,
    INTENT_FINANCE,
    INTENT_GENERAL,
    INTENT_REALTIME,
    classify,
    extract_signals,
    is_visual_artifact_request,
)

# Labels that cause live market data to be fetched and the stock-analysis
# template to be used.  Misrouting an engineering question into one of these is
# the failure that started this work.
MARKET_INTENTS = {INTENT_ANALYSIS, INTENT_REALTIME, INTENT_FINANCE}


class SubstringCollisionTests(unittest.TestCase):
    """ASCII terms must match on word boundaries."""

    def test_report_filename_is_not_a_repo(self):
        # "repo" used to match inside "report_2024.docx".
        self.assertEqual(classify("分析 report_2024.docx 这个文档"), INTENT_GENERAL)

    def test_repo_as_its_own_word_still_counts(self):
        self.assertEqual(classify("帮我看下 aria-code 这个仓库"), INTENT_CODING)
        self.assertEqual(classify("review this repo"), INTENT_CODING)

    def test_api_inside_a_longer_word_is_not_a_code_signal(self):
        signals = extract_signals("rapid growth in capital markets")
        self.assertFalse(signals.code)


class SpacingVariantTests(unittest.TestCase):
    """CJK terms must match regardless of interleaved whitespace."""

    def test_spaced_k_line_is_a_chart_request(self):
        self.assertEqual(classify("画一张 TSLA 的 K 线图"), INTENT_CODING)

    def test_unspaced_k_line_is_the_same(self):
        self.assertEqual(classify("画一张TSLA的K线图"), INTENT_CODING)

    def test_visual_artifact_detection_ignores_spacing(self):
        self.assertTrue(is_visual_artifact_request("AAPL 的 K 线图"))
        self.assertTrue(is_visual_artifact_request("AAPL的K线图"))


class RuleInteractionTests(unittest.TestCase):
    """Signals combine by precedence; they must not cancel each other."""

    def test_question_word_does_not_demote_a_debugging_request(self):
        # "为什么" used to suppress the coding rule entirely.
        self.assertEqual(classify("这段代码为什么报错"), INTENT_CODING)

    def test_conceptual_question_is_still_general(self):
        self.assertEqual(classify("什么是夏普比率"), INTENT_GENERAL)
        self.assertEqual(classify("解释一下 DCF 估值模型"), INTENT_GENERAL)

    def test_build_request_about_a_ticker_is_coding_not_analysis(self):
        self.assertEqual(classify("写一个 AAPL 动量策略"), INTENT_CODING)

    def test_realtime_outranks_analysis(self):
        # Otherwise the model answers a price question from memory.
        self.assertEqual(classify("分析苹果今天的市场"), INTENT_REALTIME)


class GenericWordTests(unittest.TestCase):
    """Ordinary Chinese must not imply market intent."""

    def test_project_review_is_not_market_intent(self):
        for message in (
            "你觉得这个项目有哪些问题需要完善和提升",
            "根据以上分析和建议开始完善",
            "评估一下这个方案的可行性",
            "研究一下新能源行业",
            "给我讲讲这个产品的架构设计",
        ):
            with self.subTest(message=message):
                self.assertNotIn(classify(message), MARKET_INTENTS)

    def test_analysis_requires_a_concrete_entity(self):
        self.assertEqual(classify("分析一下 AAPL"), INTENT_ANALYSIS)
        self.assertEqual(classify("腾讯的基本面怎么样"), INTENT_ANALYSIS)
        self.assertNotIn(classify("分析一下这个设计"), MARKET_INTENTS)

    def test_macro_discussion_stays_general(self):
        self.assertEqual(classify("宏观角度分析一下当前经济"), INTENT_GENERAL)
        self.assertEqual(classify("为什么美股最近波动这么大"), INTENT_GENERAL)

    def test_real_estate_never_uses_the_stock_template(self):
        self.assertEqual(classify("北京房价走势分析"), INTENT_GENERAL)


class CoreLabelTests(unittest.TestCase):
    """The behaviour every other rule has to preserve."""

    CASES = (
        ("帮我重构一下这个模块", INTENT_CODING),
        ("帮我修复这个 bug", INTENT_CODING),
        ("审核一下这个项目的代码", INTENT_CODING),
        ("生成本周持仓看板", INTENT_CODING),
        ("AAPL 现在多少钱", INTENT_REALTIME),
        ("比特币现在什么价", INTENT_REALTIME),
        ("最近有什么新闻", INTENT_REALTIME),
        ("英伟达应该买入吗", INTENT_FINANCE),
    )

    def test_labels(self):
        for message, expected in self.CASES:
            with self.subTest(message=message):
                self.assertEqual(classify(message), expected)

    def test_empty_input_is_general(self):
        self.assertEqual(classify(""), INTENT_GENERAL)
        self.assertEqual(classify(None), INTENT_GENERAL)
        self.assertEqual(classify("   "), INTENT_GENERAL)


class LegacyEntryPointTests(unittest.TestCase):
    """The public classifier must route through the new implementation."""

    def test_classify_intent_sync_matches(self):
        from aria_code.intent_classifier import classify_intent_sync

        for message in (
            "这段代码为什么报错",
            "分析 report_2024.docx 这个文档",
            "画一张 TSLA 的 K 线图",
            "最近有什么新闻",
        ):
            with self.subTest(message=message):
                self.assertEqual(classify_intent_sync(message), classify(message))

    def test_visual_helper_matches(self):
        from aria_code.intent_classifier import is_visual_market_artifact_request

        for message in ("AAPL 的 K 线图", "写一份周报", "什么是夏普比率"):
            with self.subTest(message=message):
                self.assertEqual(
                    is_visual_market_artifact_request(message),
                    is_visual_artifact_request(message),
                )


if __name__ == "__main__":
    unittest.main()
