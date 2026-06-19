from pathlib import Path
import json

from backtest_report import BacktestConfig, generate_backtest_report, render_backtest_html, run_backtest_from_history


def _sample_history(n=90):
    rows = []
    for i in range(n):
        price = 100 + i * 0.8 + (i % 5) * 0.2
        rows.append(
            {
                "date": f"2025-01-{(i % 28) + 1:02d}" if i < 28 else f"2025-02-{((i - 28) % 28) + 1:02d}" if i < 56 else f"2025-03-{((i - 56) % 28) + 1:02d}",
                "open": price - 0.3,
                "high": price + 0.5,
                "low": price - 0.7,
                "close": price,
                "volume": 100000 + i,
            }
        )
    return rows


def test_run_backtest_from_history_generates_equity_curve():
    config = BacktestConfig(symbol="AAPL", strategy="momentum", momentum_period=5)
    result = run_backtest_from_history(_sample_history(), config)

    assert result["success"] is True
    assert result["symbol"] == "AAPL"
    assert result["bars"] >= 80
    assert len(result["equity_curve"]) == result["bars"]
    assert result["total_return"] > 0
    assert "benchmark_return" in result


def test_render_backtest_html_contains_svg_and_metrics(tmp_path: Path):
    result = run_backtest_from_history(
        _sample_history(),
        BacktestConfig(symbol="NVDA", strategy="sma_cross", fast_period=5, slow_period=20),
    )
    result["data_status"] = "complete"
    result["provider_chain"] = ["fake_history"]
    result["missing_fields"] = []
    out = render_backtest_html(result, tmp_path / "backtest.html")

    text = out.read_text(encoding="utf-8")
    assert "<svg" in text
    assert "NVDA" in text
    assert "策略回测" in text
    assert "Strategy equity" in text
    assert "Provider chain" in text
    assert "fake_history" in text


class _FakeMarketClient:
    def history(self, symbol, days=252, interval="1d"):
        return {
            "success": True,
            "symbol": symbol,
            "provider": "fake",
            "provider_chain": ["fake"],
            "data": _sample_history(100),
        }


def test_generate_backtest_report_uses_injected_market_client(tmp_path: Path):
    result = generate_backtest_report(
        BacktestConfig(symbol="MSFT", strategy="buy_hold"),
        output_dir=tmp_path,
        market_client=_FakeMarketClient(),
    )

    assert result["success"] is True
    assert result["data_provider"] == "fake"
    assert result["provider_chain"] == ["fake"]
    assert result["data_status"] == "complete"
    assert Path(result["report_path"]).exists()


def test_generate_backtest_report_writes_data_provenance_sidecars(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "project-artifacts"))
    monkeypatch.setenv("ARIA_USER_OUTPUT_ROOT", str(tmp_path / "user-output"))

    result = generate_backtest_report(
        BacktestConfig(symbol="MSFT", strategy="buy_hold"),
        market_client=_FakeMarketClient(),
    )

    report_path = Path(result["report_path"])
    assert str(tmp_path / "user-output" / "generated" / "strategies" / "backtests") in str(report_path)
    assert str(tmp_path / "project-artifacts") not in str(report_path)
    metadata_path = report_path.with_suffix(".metadata.json")
    raw_path = report_path.with_suffix(".raw_data.json")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    html = report_path.read_text(encoding="utf-8")

    assert metadata["data"]["provider_chain"] == ["fake"]
    assert metadata["data"]["status"] == "complete"
    assert raw["data"]["provider_chain"] == ["fake"]
    assert "Data status" in html
    assert "fake" in html


def test_backtest_returns_friendly_error_for_short_history():
    result = run_backtest_from_history(_sample_history(10), BacktestConfig(symbol="AAPL"))

    assert result["success"] is False
    assert "历史行情不足" in result["error"]
