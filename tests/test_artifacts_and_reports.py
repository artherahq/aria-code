from pathlib import Path
import asyncio
import json
from datetime import datetime

import pandas as pd

from artifacts import (
    artifact_dir,
    artifact_root,
    create_artifact,
    recent_artifacts,
    slugify_topic,
    write_artifact_metadata,
)
from data_cleaner import CleanResult
from report_generator import _build_html, _fetch_report_data_sync, generate_price_chart, generate_report


def test_artifact_dir_uses_per_user_aria_code_root(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "aria-artifacts"))

    out = artifact_dir("reports/stock-charts", "AAPL 市场分析")

    assert artifact_root() == tmp_path / "aria-artifacts"
    assert out == tmp_path / "aria-artifacts" / "reports" / "stock-charts" / "AAPL-市场分析"
    assert out.exists()


def test_artifact_root_defaults_to_project_aria_output(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".ariarc").write_text('{"project": "demo"}', encoding="utf-8")
    monkeypatch.delenv("ARIA_ARTIFACT_ROOT", raising=False)
    monkeypatch.chdir(project)

    assert artifact_root() == project / "aria-output"


def test_slugify_topic_keeps_readable_project_names():
    assert slugify_topic("分析 AAPL / 回测策略") == "分析-AAPL-回测策略"
    assert slugify_topic("") == "general"


def test_create_artifact_uses_dated_bundle_and_metadata(monkeypatch, tmp_path: Path):
    from datetime import datetime

    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "aria-artifacts"))

    record = create_artifact(
        "reports/market",
        "600362",
        "600362_market_report",
        ".html",
        timestamp=datetime(2026, 6, 12, 9, 30, 5),
    )
    write_artifact_metadata(record, {"kind": "market_report", "status": "partial"})

    assert record.path == tmp_path / "aria-artifacts" / "reports" / "market" / "600362" / "2026-06-12" / "093005_600362_market_report.html"
    assert record.metadata_path.exists()
    payload = json.loads(record.metadata_path.read_text(encoding="utf-8"))
    assert payload["status"] == "partial"
    assert payload["artifact"]["path"] == str(record.path)


def test_recent_artifacts_reads_metadata(monkeypatch, tmp_path: Path):
    from datetime import datetime

    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "aria-artifacts"))
    record = create_artifact(
        "reports/market",
        "AAPL",
        "AAPL_market_report",
        ".html",
        timestamp=datetime(2026, 6, 12, 9, 30, 5),
    )
    record.path.write_text("<html></html>", encoding="utf-8")
    write_artifact_metadata(record, {"kind": "market_report", "status": "complete", "symbol": "AAPL"})

    items = recent_artifacts(limit=5)

    assert len(items) == 1
    assert items[0]["kind"] == "market_report"
    assert items[0]["status"] == "complete"
    assert items[0]["topic"] == "AAPL"


def test_report_html_renders_missing_data_without_zero_placeholders():
    html = _build_html(
        "600362",
        {"company_name": "600362", "currency": "CNY", "pe_ratio": 0, "pb_ratio": 0, "roe": 0},
        None,
        team_result=None,
        clean_result=None,
    )

    assert "PE=0" not in html
    assert "0.0%" not in html
    assert "图表暂不可用：历史价格数据不足" in html


def test_report_html_renders_technical_indicators():
    html = _build_html(
        "AAPL",
        {
            "company_name": "Apple",
            "currency": "USD",
            "price": 190.25,
            "rsi": 55.4,
            "macd": -0.123,
            "signal": 0.234,
            "ma20": 188.12,
            "ma60": 181.45,
            "bb_upper": 201.01,
            "bb_lower": 176.22,
        },
        None,
        team_result=None,
        clean_result=None,
    )

    assert "技术指标" in html
    assert "RSI(14)" in html
    assert "55.4" in html
    assert "-0.123" in html
    assert "$188.12" in html


def test_generate_price_chart_has_svg_fallback(monkeypatch):
    class _BadMatplotlib:
        @staticmethod
        def use(*args, **kwargs):
            raise RuntimeError("matplotlib unavailable")

    monkeypatch.setitem(__import__("sys").modules, "matplotlib", _BadMatplotlib)
    df = pd.DataFrame(
        {"Close": [10, 11, 12, 11.5, 13, 14]},
        index=pd.date_range("2025-01-01", periods=6),
    )

    chart = generate_price_chart(df, "AAPL", {})

    assert chart is not None
    assert chart.lstrip().startswith("<svg")


def test_report_data_fetch_falls_back_to_market_data_client(monkeypatch):
    import data_cleaner
    import market_data_client

    empty = pd.DataFrame()
    monkeypatch.setattr(data_cleaner, "get_clean_prices", lambda symbol, period="1y": (empty, CleanResult(empty, quality_score=0)))
    monkeypatch.setattr(data_cleaner, "get_fundamentals", lambda symbol: {"company_name": symbol, "symbol": symbol, "currency": "USD"})

    class _FakeMDC:
        def history(self, symbol, days=370, interval="1d"):
            return {
                "success": True,
                "provider": "fake_history",
                "provider_chain": ["fake_history"],
                "data": [
                    {"date": "2025-01-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000},
                    {"date": "2025-01-02", "open": 10.5, "high": 12, "low": 10, "close": 11, "volume": 1200},
                ],
            }

        def quote(self, symbol):
            return {
                "success": True,
                "provider": "fake_quote",
                "provider_chain": ["fake_quote"],
                "price": 11,
                "prev_close": 10,
                "market_cap": 123456,
            }

        def technical_indicators(self, symbol, days=120):
            return {
                "success": True,
                "provider_chain": ["fake_ta"],
                "rsi": 55,
                "ma20": 10.5,
            }

    monkeypatch.setattr(market_data_client, "MarketDataClient", _FakeMDC)

    df, clean_result, fundamentals = _fetch_report_data_sync("AAPL")

    assert not df.empty
    assert clean_result.quality_score > 0
    assert fundamentals["price"] == 11
    assert fundamentals["52w_high"] == 11
    assert fundamentals["52w_low"] == 10
    assert fundamentals["rsi"] == 55
    assert "fake_history" in fundamentals["data_provider_chain"]
    assert "fake_quote" in fundamentals["data_provider_chain"]
    assert "fake_ta" in fundamentals["data_provider_chain"]


def test_generate_report_writes_sidecar_metadata_and_raw_data(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "aria-artifacts"))

    df = pd.DataFrame(
        {
            "Open": [10, 11],
            "High": [11, 12],
            "Low": [9, 10],
            "Close": [10, 11],
            "Volume": [1000, 1200],
        },
        index=pd.date_range("2026-06-10", periods=2),
    )

    async def _run():
        return await generate_report("AAPL")

    monkeypatch.setattr(
        "report_generator._fetch_report_data_sync",
        lambda symbol: (
            df,
            CleanResult(df, quality_score=99),
            {
                "company_name": "Apple",
                "symbol": symbol,
                "currency": "USD",
                "price": 11,
                "rsi": 55,
                "data_provider_chain": ["fake"],
                "data_warnings": [],
            },
        ),
    )
    monkeypatch.setattr("report_generator.generate_price_chart", lambda *_args, **_kwargs: "<svg></svg>")

    path = asyncio.run(_run())

    assert path is not None
    assert path.exists()
    assert path.parent.name == datetime.now().strftime("%Y-%m-%d")
    metadata_path = path.with_suffix(".metadata.json")
    raw_path = path.with_suffix(".raw_data.json")
    assert metadata_path.exists()
    assert raw_path.exists()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["kind"] == "market_report"
    assert metadata["symbol"] == "AAPL"
    assert metadata["data"]["provider_chain"] == ["fake"]
