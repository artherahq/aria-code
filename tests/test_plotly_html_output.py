from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def _sample_frame(rows: int = 30) -> pd.DataFrame:
    base = 100.0
    data = {
        "Open": [base + i * 0.5 for i in range(rows)],
        "High": [base + i * 0.5 + 1.0 for i in range(rows)],
        "Low": [base + i * 0.5 - 1.0 for i in range(rows)],
        "Close": [base + i * 0.5 + 0.4 for i in range(rows)],
        "Volume": [100000 + i * 100 for i in range(rows)],
    }
    return pd.DataFrame(data, index=pd.date_range("2026-01-01", periods=rows, freq="D"))


def test_stock_chart_html_uses_inline_plotly_js(monkeypatch, tmp_path):
    from apps.cli.handlers.chart_handlers import handle_stock_chart_analysis_direct

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period="1y", interval="1d", auto_adjust=False):
            return _sample_frame()

        def get_info(self):
            return {
                "longName": "Apple Inc.",
                "currency": "USD",
                "trailingPE": 28.1,
                "priceToBook": 7.2,
                "returnOnEquity": 0.18,
            }

    class FakeYF:
        Ticker = FakeTicker

    monkeypatch.setitem(sys.modules, "yfinance", FakeYF())
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "project-artifacts"))
    monkeypatch.setenv("ARIA_USER_OUTPUT_ROOT", str(tmp_path / "user-output"))

    result = handle_stock_chart_analysis_direct("AAPL", "1y")
    html = Path(result["chart_path"]).read_text(encoding="utf-8")

    assert '<script src="https://cdn.plot.ly' not in html
    assert "Plotly.newPlot" in html


def test_stat_arb_chart_html_uses_inline_plotly_js(monkeypatch, tmp_path):
    import aria_cli

    sym_a = "AAPL"
    sym_b = "MSFT"

    closes = pd.DataFrame(
        {
            sym_a: [100 + i * 0.4 for i in range(120)],
            sym_b: [99 + i * 0.35 for i in range(120)],
        },
        index=pd.date_range("2026-01-01", periods=120, freq="D"),
    )
    raw = pd.concat({"Close": closes}, axis=1)

    class FakeYF:
        @staticmethod
        def download(*_args, **_kwargs):
            return raw

    monkeypatch.setitem(sys.modules, "yfinance", FakeYF())
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "project-artifacts"))
    monkeypatch.setenv("ARIA_USER_OUTPUT_ROOT", str(tmp_path / "user-output"))
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: None)

    aria_cli._generate_stat_arb_chart(sym_a, sym_b, period="2y")

    generated = list((tmp_path / "user-output").rglob("*_zscore.html"))
    assert generated, "expected a z-score HTML artifact"
    html = generated[0].read_text(encoding="utf-8")

    assert '<script src="https://cdn.plot.ly' not in html
    assert "Plotly.newPlot" in html
