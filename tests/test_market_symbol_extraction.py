"""Regression tests for the symbol-extraction bugs found by the channels e2e
drill: substring alias matching turned "whether" into an ETH hit and quoted
trading action words (BUY) as tickers. Hermetic — the resolver takes an
injected universe, and no test path touches the network."""

import unittest

from aria_code.apps.cli.market_universe import MarketSymbol, resolve_market_mentions
from aria_code.apps.cli.utils.market_detect import (
    _COMPANY_TO_TICKER,
    _extract_market_symbols,
    _is_blocked_market_symbol_candidate,
)

FAKE_UNIVERSE = [
    MarketSymbol(name="ETH", symbol="ETH-USD", market="crypto"),
    MarketSymbol(name="GE", symbol="GE", market="us"),
    MarketSymbol(name="比特币", symbol="BTC-USD", market="crypto"),
]


def _resolve(text):
    return [item.symbol for _, item in
            resolve_market_mentions(text, load_universe=lambda: FAKE_UNIVERSE)]


class WordBoundaryTests(unittest.TestCase):
    def test_eth_not_matched_inside_whether(self):
        self.assertNotIn("ETH-USD", _resolve("summarize whether it warrants action"))

    def test_ge_not_matched_inside_gateway(self):
        self.assertNotIn("GE", _resolve("route the gateway analysis"))

    def test_standalone_ascii_names_still_match(self):
        self.assertIn("ETH-USD", _resolve("ETH price outlook"))
        self.assertIn("GE", _resolve("GE rallied after earnings"))

    def test_ascii_match_is_case_insensitive_on_word_boundary(self):
        self.assertIn("ETH-USD", _resolve("what is eth doing today"))

    def test_cjk_names_keep_substring_semantics(self):
        # Chinese has no word boundaries — 比特币 inside running text must hit.
        self.assertIn("BTC-USD", _resolve("请分析比特币走势"))

    def test_unverified_company_is_not_mapped_to_a_fabricated_ticker(self):
        for name in ("SpaceX", "太空探索技术", "Starlink"):
            self.assertNotIn(name, _COMPANY_TO_TICKER)


class ActionWordBlocklistTests(unittest.TestCase):
    def test_trading_action_words_are_blocked(self):
        for word in ("BUY", "SELL", "EXIT", "LONG", "SHORT", "ALERT", "SIGNAL"):
            self.assertTrue(
                _is_blocked_market_symbol_candidate(word), f"{word} should be blocked"
            )

    def test_real_tickers_stay_unblocked(self):
        for word in ("NVDA", "AAPL", "GE", "MA"):
            self.assertFalse(
                _is_blocked_market_symbol_candidate(word), f"{word} must stay extractable"
            )

    def test_drill_prompt_extracts_ticker_not_action(self):
        prompt = (
            "TradingView alert received: BUY signal for NVDA at 190.5. "
            'Alert message: "MA cross on 1h". Assess the signal against current '
            "market data and the portfolio, and summarize whether it warrants "
            "action. Do not place any orders."
        )
        symbols = _extract_market_symbols(prompt)
        self.assertIn("NVDA", symbols)
        self.assertNotIn("BUY", symbols)
        self.assertNotIn("ETH-USD", symbols)


if __name__ == "__main__":
    unittest.main()
