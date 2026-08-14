from dataclasses import dataclass

from packages.adk_bridge.market_tools import MarketResearchTools
from packages.aria_services.provider_health import GLOBAL_PROVIDER_HEALTH


@dataclass
class _Snapshot:
    success: bool
    quote: dict
    fundamentals: dict
    technical: dict
    quality: dict
    warnings: list
    errors: list
    status: str


class _FakeDataService:
    def bundle(self, symbol):
        assert symbol == "000300.SS"
        return _Snapshot(
            success=True,
            quote={"price": 4000.0, "provider": "fixture"},
            fundamentals={"pe": 14.2},
            technical={"rsi_14": 52.1},
            quality={"status": "fresh", "providers": ["fixture"]},
            warnings=[],
            errors=[],
            status="complete",
        )


def test_market_snapshot_is_read_only_and_compact():
    tools = MarketResearchTools(service_factory=_FakeDataService)

    result = tools.get_market_snapshot(" 000300.ss ")

    assert result["success"] is True
    assert result["symbol"] == "000300.SS"
    assert result["quote"]["price"] == 4000.0
    assert result["quality"]["status"] == "fresh"
    assert "investment advice" not in result["disclaimer"].lower()


def test_market_snapshot_rejects_invalid_symbols_before_network_io():
    called = False

    def factory():
        nonlocal called
        called = True
        return _FakeDataService()

    result = MarketResearchTools(service_factory=factory).get_market_snapshot("")

    assert result["success"] is False
    assert called is False


def test_market_snapshot_does_not_expose_provider_exception_details():
    class BrokenDataService:
        def bundle(self, symbol):
            raise RuntimeError("https://provider.example/quotes?token=secret-value")

    result = MarketResearchTools(service_factory=BrokenDataService).get_market_snapshot("AAPL")

    assert result["success"] is False
    assert result["retryable"] is True
    assert "secret-value" not in result["error"]
    assert result["error"] == "Market snapshot is temporarily unavailable."


def test_market_health_includes_a_product_safe_status():
    GLOBAL_PROVIDER_HEALTH._states.clear()

    result = MarketResearchTools().get_market_data_health()

    assert result["success"] is True
    assert result["status"]["service"] == "market_data"
    assert result["status"]["state"] == "unknown"
    assert "provider" not in result["status"]["message"].lower()
    assert result["health"] == {
        "total": 0,
        "available": 0,
        "degraded": 0,
        "unavailable": 0,
        "cooldown": 0,
    }
    assert "providers" not in result
