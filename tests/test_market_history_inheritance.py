"""Regression tests for symbol inheritance from conversation history.

Background: asking a coding assistant "根据以上分析和建议开始完善" produced a
MongoDB Inc. (MDB) stock quote.  Two defects combined:

  - `_extract_symbol_from_history` scanned *assistant* replies for tickers, so a
    previous answer mentioning "Cassandra/MongoDB" donated the symbol MDB;
  - `_is_market_snapshot_request` treated the generic word "分析" as market
    intent, which is ordinary Chinese and appears constantly in engineering chat.

Market intent must come from the user, and inheriting a symbol across turns
requires unambiguous market wording — not a generic verb.

Hermetic — ARIA_OFFLINE pins the resolver to its static tables and a temporary
cache directory keeps the developer's real symbol cache out of the assertions.
"""

import os
import tempfile
import unittest

from aria_code.apps.cli.utils.market_detect import (
    _extract_symbol_from_history,
    _is_market_snapshot_request,
)

# An engineering conversation: the assistant's reply names datastores, none of
# which is a stock the user asked about.
TECH_HISTORY = [
    {"role": "user", "content": "你觉得这个项目有哪些问题需要完善和提升"},
    {"role": "assistant", "content": "架构建议：存储及持久化层 (Cassandra/MongoDB)，"
                                     "实时数据分析引擎 (Kafka/Kinesis 订阅者)，"
                                     "能量管理系统 EMS。"},
]

# A genuine market conversation, where a follow-up should inherit AAPL.
MARKET_HISTORY = [
    {"role": "user", "content": "分析一下 AAPL"},
    {"role": "assistant", "content": "AAPL 当前价格 ..."},
]


class _OfflineResolverCase(unittest.TestCase):
    """Pin the symbol resolver offline so no test path can reach the network."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._saved = {k: os.environ.get(k) for k in ("ARIA_OFFLINE", "ARIA_CACHE_DIR")}
        os.environ["ARIA_OFFLINE"] = "1"
        os.environ["ARIA_CACHE_DIR"] = self._tmpdir.name
        from aria_code.apps.cli.market_universe import reset_universe_refresh_guard

        reset_universe_refresh_guard()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmpdir.cleanup()


class HistorySymbolExtractionTests(_OfflineResolverCase):
    def test_assistant_reply_never_donates_a_symbol(self):
        # "MongoDB" in the assistant's own architecture advice must not become
        # a ticker the next user message inherits.
        self.assertEqual(_extract_symbol_from_history(TECH_HISTORY), "")

    def test_user_message_still_donates_a_symbol(self):
        self.assertEqual(_extract_symbol_from_history(MARKET_HISTORY), "AAPL")

    def test_empty_history_is_safe(self):
        self.assertEqual(_extract_symbol_from_history([]), "")
        self.assertEqual(_extract_symbol_from_history(None), "")

    def test_malformed_history_entries_are_skipped(self):
        self.assertEqual(_extract_symbol_from_history(["not a dict", None]), "")

    def test_multimodal_user_content_is_read(self):
        history = [{"role": "user", "content": [{"type": "text", "text": "看看 AAPL"}]}]
        self.assertEqual(_extract_symbol_from_history(history), "AAPL")

    def test_lookback_is_bounded_to_recent_turns(self):
        stale = [{"role": "user", "content": "分析一下 AAPL"}]
        padding = [{"role": "user", "content": "继续"} for _ in range(8)]
        self.assertEqual(_extract_symbol_from_history(stale + padding), "")


class SnapshotRoutingTests(_OfflineResolverCase):
    def test_coding_followup_is_not_a_quote_request(self):
        # The exact message that produced the MDB quote.
        self.assertFalse(
            _is_market_snapshot_request("根据以上分析和建议开始完善", TECH_HISTORY)
        )

    def test_project_review_question_is_not_a_quote_request(self):
        self.assertFalse(
            _is_market_snapshot_request("你觉得这个项目有哪些问题需要完善和提升", TECH_HISTORY)
        )

    def test_generic_analysis_word_cannot_inherit_a_symbol(self):
        # Even with a real market conversation behind it, "分析" alone is too
        # weak to re-fetch a quote; the user must say something market-specific.
        self.assertFalse(
            _is_market_snapshot_request("根据以上分析和建议开始完善", MARKET_HISTORY)
        )

    def test_explicit_market_followup_still_inherits(self):
        for message in ("现在的行情呢", "股价涨跌怎么样"):
            with self.subTest(message=message):
                self.assertTrue(_is_market_snapshot_request(message, MARKET_HISTORY))

    def test_followup_does_not_inherit_from_a_technical_conversation(self):
        self.assertFalse(_is_market_snapshot_request("现在的行情呢", TECH_HISTORY))

    def test_symbol_in_message_needs_no_history(self):
        self.assertTrue(_is_market_snapshot_request("AAPL 现在多少钱", []))


class UnresolvedCompanyGuardTests(_OfflineResolverCase):
    """Time and scope words in front of a market term name no company."""

    def test_pronoun_followups_are_not_unknown_companies(self):
        from aria_code.apps.cli.market_universe import looks_like_unresolved_market_name

        for message in ("现在的行情呢", "现在的股票和趋势呢", "股价涨跌怎么样"):
            with self.subTest(message=message):
                self.assertFalse(looks_like_unresolved_market_name(message))

    def test_real_unknown_company_is_still_flagged(self):
        from aria_code.apps.cli.market_universe import looks_like_unresolved_market_name

        self.assertTrue(looks_like_unresolved_market_name("某某科技的股价"))


if __name__ == "__main__":
    unittest.main()
