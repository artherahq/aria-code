"""Which source answered, and what happens when one lies.

datasources is where "wrong data" becomes "wrong conclusion", and it had almost
no tests. The router tries sources in priority order and returns the first
usable answer, which means the interesting behaviour is entirely in the
failure and fallback paths: a source that raises, one that returns a zero
price, one that is not configured, one that does not cover the market.

Everything here uses fake sources. A test that reaches the network measures the
network.
"""

import unittest

from aria_code.datasources.base import QuoteResult, _detect_market
from aria_code.datasources.router import DataRouter, _SOURCE_REGISTRY


class _FakeSource:
    """A source whose behaviour each test dictates."""

    markets = ("a_share", "us", "hk", "crypto")
    requires_key = False

    name = "fake"
    configured = True
    supports_all = True
    price = 100.0
    raises = False
    calls: list = []

    def is_configured(self):
        return self.configured

    def supports(self, symbol):
        return self.supports_all

    def quote(self, symbol):
        type(self).calls.append(type(self).name)
        if self.raises:
            raise RuntimeError(f"{type(self).name} is down")
        if self.price is None:
            return None
        return QuoteResult(symbol=symbol, price=self.price, source=type(self).name)

    def history(self, symbol, days=90, interval="1d"):
        type(self).calls.append(type(self).name)
        if self.raises:
            raise RuntimeError("down")
        return None

    def fundamentals(self, symbol):
        return None


def _source(name, **attrs):
    return type(f"Fake_{name}", (_FakeSource,), {"name": name, "calls": [], **attrs})


class RouterBase(unittest.TestCase):
    def setUp(self):
        self._saved = dict(_SOURCE_REGISTRY)
        self.addCleanup(lambda: (_SOURCE_REGISTRY.clear(), _SOURCE_REGISTRY.update(self._saved)))

    def _router(self, chain, **sources):
        for name, cls in sources.items():
            _SOURCE_REGISTRY[name] = cls
        router = DataRouter()
        router._user_chains = {"us": list(chain), "a_share": list(chain)}
        return router


class FallbackOrderTests(RouterBase):
    def test_the_first_healthy_source_wins_and_the_rest_are_not_called(self):
        primary, backup = _source("primary"), _source("backup")
        router = self._router(["primary", "backup"], primary=primary, backup=backup)

        quote = router.quote("AAPL")
        self.assertEqual(quote.source, "primary")
        self.assertEqual(backup.calls, [])

    def test_a_source_that_raises_does_not_stop_the_chain(self):
        broken, backup = _source("broken", raises=True), _source("backup")
        router = self._router(["broken", "backup"], broken=broken, backup=backup)

        quote = router.quote("AAPL")
        self.assertEqual(quote.source, "backup")
        self.assertEqual(broken.calls, ["broken"])   # it was tried

    def test_a_zero_price_is_treated_as_no_answer(self):
        # A source that returns a QuoteResult with price 0 has failed, not
        # succeeded. Passing that on is how a chart or a valuation ends up
        # built on a zero.
        empty, backup = _source("empty", price=0.0), _source("backup", price=42.0)
        router = self._router(["empty", "backup"], empty=empty, backup=backup)

        quote = router.quote("AAPL")
        self.assertEqual(quote.source, "backup")
        self.assertEqual(quote.price, 42.0)

    def test_a_source_returning_none_falls_through(self):
        silent, backup = _source("silent", price=None), _source("backup")
        router = self._router(["silent", "backup"], silent=silent, backup=backup)
        self.assertEqual(router.quote("AAPL").source, "backup")

    def test_an_unconfigured_source_is_skipped(self):
        unkeyed, backup = _source("unkeyed", configured=False), _source("backup")
        router = self._router(["unkeyed", "backup"], unkeyed=unkeyed, backup=backup)

        self.assertEqual(router.quote("AAPL").source, "backup")
        self.assertEqual(unkeyed.calls, [])   # never even asked

    def test_a_source_that_does_not_cover_the_symbol_is_skipped(self):
        narrow, backup = _source("narrow", supports_all=False), _source("backup")
        router = self._router(["narrow", "backup"], narrow=narrow, backup=backup)

        self.assertEqual(router.quote("AAPL").source, "backup")
        self.assertEqual(narrow.calls, [])

    def test_every_source_failing_returns_none_rather_than_a_guess(self):
        a, b = _source("a", raises=True), _source("b", raises=True)
        router = self._router(["a", "b"], a=a, b=b)
        self.assertIsNone(router.quote("AAPL"))

    def test_an_unknown_source_name_in_the_chain_is_survivable(self):
        backup = _source("backup")
        router = self._router(["does_not_exist", "backup"], backup=backup)
        self.assertEqual(router.quote("AAPL").source, "backup")


class ProvenanceTests(RouterBase):
    """Which source answered must be knowable after the fact."""

    def test_the_answer_records_where_it_came_from(self):
        backup = _source("backup")
        router = self._router(["broken", "backup"],
                              broken=_source("broken", raises=True), backup=backup)
        self.assertEqual(router.quote("AAPL").source, "backup")

    def test_a_silent_switch_is_still_visible_in_the_result(self):
        # Sources differ in adjustment convention and timestamp, so "the
        # backup answered" is information the caller needs, not a detail.
        # Distinct names per scenario: _SOURCE_REGISTRY is process-global, so
        # reusing "primary" would have the second registration silently
        # redefine the first router's source too.
        healthy = self._router(["ok_primary", "ok_backup"],
                               ok_primary=_source("ok_primary"),
                               ok_backup=_source("ok_backup"))
        degraded = self._router(["bad_primary", "bad_backup"],
                                bad_primary=_source("bad_primary", raises=True),
                                bad_backup=_source("bad_backup"))

        self.assertEqual(healthy.quote("AAPL").source, "ok_primary")
        self.assertEqual(degraded.quote("AAPL").source, "bad_backup")


class ChainSelectionTests(RouterBase):
    def test_user_config_overrides_the_default_chain(self):
        router = self._router(["mine"], mine=_source("mine"))
        self.assertEqual(router._get_chain("us"), ["mine"])

    def test_an_unconfigured_market_falls_back_to_a_default(self):
        router = DataRouter()
        router._user_chains = {}
        self.assertTrue(router._get_chain("us"))
        self.assertTrue(router._get_chain("something_unknown"))


class MarketDetectionTests(unittest.TestCase):
    """Routing to the wrong chain means asking a source that cannot answer."""

    def test_known_shapes(self):
        for symbol, market in (
            ("600519", "a_share"),
            ("AAPL", "us"),
            ("BTC/USDT", "crypto"),
            ("BTC", "crypto"),
            ("USDCNY", "forex"),
            ("GOLD", "commodity"),
        ):
            with self.subTest(symbol=symbol):
                self.assertEqual(_detect_market(symbol), market)

    def test_detection_never_raises(self):
        for symbol in ("", "   ", "???", "a" * 200, "混合中文"):
            with self.subTest(symbol=symbol):
                self.assertIsInstance(_detect_market(symbol), str)


class SourceCatalogueTests(unittest.TestCase):
    def test_every_registered_source_reports_its_status(self):
        # /config shows this; a source that raises while being described makes
        # the whole listing unavailable.
        for entry in DataRouter().list_sources():
            with self.subTest(source=entry["name"]):
                self.assertIn("configured", entry)
                self.assertIsInstance(entry["needs_key"], bool)
                self.assertTrue(entry["markets"])


if __name__ == "__main__":
    unittest.main()


class SourceCachingTests(RouterBase):
    """A source that cannot serve is remembered, not rebuilt every call."""

    def _counting(self, name, **attrs):
        cls = _source(name, **attrs)
        cls.built = []
        original_init = cls.__init__ if "__init__" in cls.__dict__ else None

        def __init__(self):
            cls.built.append(1)
            if original_init:
                original_init(self)

        cls.__init__ = __init__
        return cls

    def test_an_unconfigured_source_is_constructed_once(self):
        # Only successes used to be cached, so an unkeyed provider ahead of a
        # working one was rebuilt on every quote — forever, and some sources
        # read files in __init__.
        unkeyed = self._counting("unkeyed", configured=False)
        backup = _source("backup")
        router = self._router(["unkeyed", "backup"], unkeyed=unkeyed, backup=backup)

        for _ in range(5):
            router.quote("AAPL")
        self.assertEqual(len(unkeyed.built), 1)

    def test_a_working_source_is_also_constructed_once(self):
        primary = self._counting("primary")
        router = self._router(["primary"], primary=primary)

        for _ in range(5):
            router.quote("AAPL")
        self.assertEqual(len(primary.built), 1)

    def test_a_constructor_that_raises_does_not_break_the_chain(self):
        class Exploding:
            markets = ("us",)
            requires_key = False

            def __init__(self):
                raise RuntimeError("bad credentials file")

        backup = _source("backup")
        router = self._router(["exploding", "backup"], exploding=Exploding, backup=backup)
        self.assertEqual(router.quote("AAPL").source, "backup")

    def test_invalidate_lets_new_credentials_take_effect(self):
        # Without it the negative cache would outlive an /apikey set for the
        # rest of the session.
        unkeyed = self._counting("unkeyed", configured=False)
        router = self._router(["unkeyed"], unkeyed=unkeyed)

        router.quote("AAPL")
        router.invalidate_sources()
        router.quote("AAPL")
        self.assertEqual(len(unkeyed.built), 2)
