"""Tests for report_generator.py's chart functions added this session:
generate_indicator_chart (candlestick+volume+RSI+MACD), generate_comparison_chart
(normalized multi-symbol), generate_allocation_chart (position pie chart).

Real matplotlib/mplfinance rendering against synthetic OHLCV data, not mocks —
these functions are pure image-generation with no network/API dependency, so
there's no reason to fake them out.
"""
from __future__ import annotations

import base64

import numpy as np
import pandas as pd
import pytest

from report_generator import (
    _macd_series,
    _rsi_series,
    generate_allocation_chart,
    generate_comparison_chart,
    generate_indicator_chart,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _synthetic_ohlcv(n: int = 150, seed: int = 42, start: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    close = start + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "Open": close + rng.normal(0, 0.5, n),
            "High": close + np.abs(rng.normal(0, 1, n)),
            "Low": close - np.abs(rng.normal(0, 1, n)),
            "Close": close,
            "Volume": rng.integers(1000, 5000, n),
        },
        index=idx,
    )


def _is_valid_png(b64_str: str) -> bool:
    return base64.b64decode(b64_str).startswith(PNG_MAGIC)


# ── RSI / MACD math ─────────────────────────────────────────────────────────

def test_rsi_series_bounded_between_0_and_100():
    df = _synthetic_ohlcv()
    rsi = _rsi_series(df["Close"]).dropna()
    assert (rsi >= 0).all()
    assert (rsi <= 100).all()


def test_rsi_series_high_for_monotonically_rising_prices():
    close = pd.Series(np.arange(1, 60, dtype=float))
    rsi = _rsi_series(close).dropna()
    assert rsi.iloc[-1] > 90  # pure uptrend, no losses at all → RSI near 100


def test_macd_histogram_equals_macd_minus_signal():
    df = _synthetic_ohlcv()
    macd_line, signal_line, hist = _macd_series(df["Close"])
    diff = (macd_line - signal_line - hist).dropna()
    assert (diff.abs() < 1e-9).all()


# ── generate_indicator_chart ─────────────────────────────────────────────────

def test_indicator_chart_returns_valid_png():
    df = _synthetic_ohlcv()
    b64 = generate_indicator_chart(df, "TEST")
    assert b64 is not None
    assert _is_valid_png(b64)


def test_indicator_chart_none_for_empty_df():
    assert generate_indicator_chart(pd.DataFrame(), "TEST") is None


def test_indicator_chart_none_for_missing_close_column():
    df = pd.DataFrame({"Open": [1, 2, 3]})
    assert generate_indicator_chart(df, "TEST") is None


def test_indicator_chart_works_without_volume_column():
    # OHLC present but no Volume (has_ohlcv=False) should still render via the
    # "line" plot_type path — matches generate_price_chart's own assumption
    # that Open/High/Low/Close are always present (from get_clean_prices) but
    # Volume specifically may be missing for some data sources.
    df = _synthetic_ohlcv().drop(columns=["Volume"])
    b64 = generate_indicator_chart(df, "TEST")
    assert b64 is not None
    assert _is_valid_png(b64)


# ── generate_comparison_chart ────────────────────────────────────────────────

def test_comparison_chart_returns_valid_png_for_multiple_symbols():
    price_data = {
        "AAA": _synthetic_ohlcv(seed=1, start=100),
        "BBB": _synthetic_ohlcv(seed=2, start=50),
    }
    b64 = generate_comparison_chart(price_data, "Test Comparison")
    assert b64 is not None
    assert _is_valid_png(b64)


def test_comparison_chart_none_for_empty_input():
    assert generate_comparison_chart({}, "") is None


def test_comparison_chart_skips_bad_symbols_but_still_renders():
    price_data = {
        "GOOD": _synthetic_ohlcv(seed=3),
        "EMPTY": pd.DataFrame(),
        "NOCLOSE": pd.DataFrame({"Open": [1, 2, 3]}),
        "TOOSHORT": _synthetic_ohlcv(n=1),
    }
    b64 = generate_comparison_chart(price_data, "")
    assert b64 is not None
    assert _is_valid_png(b64)


def test_comparison_chart_none_when_all_symbols_unusable():
    price_data = {"EMPTY": pd.DataFrame(), "NOCLOSE": pd.DataFrame({"Open": [1]})}
    assert generate_comparison_chart(price_data, "") is None


# ── generate_allocation_chart ────────────────────────────────────────────────

def test_allocation_chart_returns_valid_png():
    positions = [
        {"symbol": "AAPL", "market_value": 15000},
        {"symbol": "MSFT", "market_value": 9000},
    ]
    b64 = generate_allocation_chart(positions)
    assert b64 is not None
    assert _is_valid_png(b64)


def test_allocation_chart_computes_value_from_quantity_and_price():
    positions = [{"symbol": "GOOG", "quantity": 10, "current_price": 150}]
    b64 = generate_allocation_chart(positions)
    assert b64 is not None
    assert _is_valid_png(b64)


def test_allocation_chart_none_for_empty_positions():
    assert generate_allocation_chart([]) is None


def test_allocation_chart_none_when_no_usable_values():
    positions = [{"symbol": "AAPL"}, {"symbol": "MSFT", "market_value": 0}]
    assert generate_allocation_chart(positions) is None


def test_allocation_chart_excludes_zero_and_negative_positions():
    positions = [
        {"symbol": "AAPL", "market_value": 10000},
        {"symbol": "SHORT", "market_value": -500},
    ]
    b64 = generate_allocation_chart(positions)
    assert b64 is not None
    assert _is_valid_png(b64)
