from dataclasses import dataclass

from data_service import DataService
from packages.aria_services.provider_health import ProviderHealthRegistry, classify_provider_error


class _MarketClient:
    def __init__(self):
        self.quote_calls = 0

    def quote(self, symbol):
        self.quote_calls += 1
        return {"success": False, "error": "primary quote down", "symbol": symbol}

    def history(self, symbol, days=370, interval="1d"):
        return {
            "success": True,
            "symbol": symbol,
            "provider": "primary_history",
            "provider_chain": ["primary_history"],
            "data": [
                {"date": "2026-06-10", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000},
                {"date": "2026-06-11", "open": 10, "high": 12, "low": 10, "close": 11, "volume": 1200},
            ],
        }

    def fundamentals(self, symbol):
        return {"success": True, "symbol": symbol, "provider": "primary_fundamentals", "pe_ratio": 18.5}

    def technical_indicators(self, symbol, days=120):
        return {"success": True, "symbol": symbol, "provider_chain": ["primary_history"], "rsi": 55, "ma20": 10.5}


@dataclass
class _RouterQuote:
    symbol: str
    price: float
    name: str
    source: str


class _Router:
    def quote(self, symbol):
        return _RouterQuote(symbol=symbol, price=12.3, name="Router Corp", source="router_quote")

    def history(self, symbol, days=370, interval="1d"):
        raise AssertionError("history should not fall back when primary succeeds")

    def fundamentals(self, symbol):
        raise AssertionError("fundamentals should not fall back when primary succeeds")


def test_quote_falls_back_to_router_and_caches_result():
    market = _MarketClient()
    health = ProviderHealthRegistry()
    service = DataService(market_client=market, router=_Router(), provider_health=health)

    first = service.quote("AAPL")
    second = service.quote("AAPL")

    assert first.success is True
    assert first.data["price"] == 12.3
    assert first.provider_chain == ["router_quote"]
    assert first.quality["status"] == "ok"
    assert first.quality["providers"] == ["router_quote"]
    assert any(row["provider"] == "market_data_client" for row in first.quality["provider_health"])
    assert first.stale is False
    assert market.quote_calls == 1
    assert second.data["price"] == 12.3


def test_bundle_reports_partial_status_and_missing_fields():
    service = DataService(market_client=_MarketClient(), router=_Router())

    bundle = service.bundle("AAPL")

    assert bundle.status == "partial"
    assert bundle.quote["price"] == 12.3
    assert bundle.history["data"]
    assert "primary_history" in bundle.provider_chain
    assert "router_quote" in bundle.provider_chain
    assert "macd" in bundle.missing_fields
    assert bundle.quality["status"] == "partial"
    assert bundle.quality["providers"] == bundle.provider_chain
    assert "fundamentals" not in bundle.missing_fields


def test_zero_price_is_not_accepted_as_real_quote():
    class _BadMarket(_MarketClient):
        def quote(self, symbol):
            return {"success": True, "symbol": symbol, "provider": "bad", "price": 0}

    class _NoRouter:
        def quote(self, symbol):
            return None

    service = DataService(market_client=_BadMarket(), router=_NoRouter())
    quote = service.quote("AAPL")

    assert quote.success is False
    assert quote.data["success"] is False
    assert quote.data["provider"] == "bad"
    assert "price" in quote.missing_fields
    assert quote.quality["status"] == "unavailable"


def test_invalid_payload_success_flag_is_normalized_false():
    class _BadMarket(_MarketClient):
        def quote(self, symbol):
            return {
                "success": True,
                "symbol": symbol,
                "provider": "edgar",
                "name": f"EDGAR:{symbol}",
                "price": 0,
            }

    service = DataService(market_client=_BadMarket(), router=False)
    quote = service.quote("^IXIC")

    assert quote.success is False
    assert quote.data["success"] is False
    assert quote.data["price"] == 0
    assert "price" in quote.missing_fields


def test_quote_marks_old_payload_as_stale():
    class _OldMarket:
        def quote(self, symbol):
            return {
                "success": True,
                "symbol": symbol,
                "provider": "old_feed",
                "price": 100,
                "timestamp": "2000-01-01T00:00:00Z",
            }

    service = DataService(market_client=_OldMarket(), router=None, max_quote_age_seconds=1)
    quote = service.quote("AAPL")

    assert quote.success is True
    assert quote.stale is True
    assert quote.quality["status"] == "stale"
    assert quote.quality["source"] == "old_feed"


def test_package_data_facade_exports_service_types():
    from packages.aria_services.data import DataBundle, DataService as PackageDataService, DataServiceResult

    assert PackageDataService is DataService
    assert DataBundle.__name__ == "DataBundle"
    assert DataServiceResult.__name__ == "DataServiceResult"


def test_provider_error_classifier_normalizes_rate_limit_and_timeout():
    rate = classify_provider_error("yfinance", "429 Too Many Requests rate limit")
    timeout = classify_provider_error("eastmoney", "Connection timed out after 30000ms")

    assert rate.category == "rate_limited"
    assert rate.cooldown_seconds >= 60
    assert rate.retryable is True
    assert timeout.category == "timeout"
    assert timeout.retryable is True


def test_provider_health_registry_tracks_cooldown():
    health = ProviderHealthRegistry()
    issue = classify_provider_error("finnhub", "429 too many requests")

    health.mark_issue(issue)
    snapshot = health.snapshot()

    assert snapshot[0]["provider"] == "finnhub"
    assert snapshot[0]["status"] == "rate_limited"
    assert snapshot[0]["cooldown_active"] is True
    assert health.provider_in_cooldown("finnhub") is True

    health.mark_success("finnhub")
    assert health.snapshot()[0]["status"] == "ok"
    assert health.provider_in_cooldown("finnhub") is False
