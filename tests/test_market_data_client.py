import builtins
import pathlib
import sys

import pytest


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


@pytest.fixture(autouse=True)
def _isolate_market_data(monkeypatch):
    """Isolate the A-share data-client tests:
    - clear the process-global quote/history cache (no cross-test leakage);
    - block the no-proxy fallback session so a reachable Eastmoney host can't
      turn a 'should fail' case into a flaky pass via a real network call.
    """
    import market_data_client

    class _NoNetSession:
        def get(self, *_a, **_k):
            raise OSError("network disabled in tests")

    market_data_client._cache._store.clear()
    monkeypatch.setattr(market_data_client, "_session_no_proxy",
                        lambda *_a, **_k: _NoNetSession(), raising=False)

    # Tushare is now tried first for A-shares when a token is configured.
    # Keep the HTTP-chain tests deterministic regardless of any local
    # TUSHARE_TOKEN: default to "no tushare" unless a test explicitly injects
    # a fake via ``client._ts_source = ...``.
    def _isolated_tushare(self):
        src = getattr(self, "_ts_source", market_data_client._UNSET)
        return None if src is market_data_client._UNSET else src
    monkeypatch.setattr(market_data_client.MarketDataClient, "_tushare",
                        _isolated_tushare, raising=False)

    yield
    market_data_client._cache._store.clear()


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _EastmoneySession:
    def get(self, *_args, **_kwargs):
        return _FakeResponse({
            "data": {
                "f43": 68.5,
                "f44": 70.2,
                "f45": 67.9,
                "f46": 68.0,
                "f47": 123456,
                "f48": 8888888,
                "f57": "300124",
                "f58": "汇川技术",
                "f169": 1.2,
                "f170": 1.78,
                "f116": 12345,
            }
        })


class _FailingSession:
    def get(self, *_args, **_kwargs):
        raise TimeoutError("curl: (28) Connection timed out")


class _EastmoneyHistorySession:
    def get(self, *_args, **_kwargs):
        return _FakeResponse({
            "data": {
                "name": "汇川技术",
                "klines": [
                    "2026-06-10,68.00,68.50,70.20,67.90,123456",
                    "2026-06-11,68.50,69.10,69.80,68.10,223456",
                ],
            }
        })


class _SinaTextResponse:
    status_code = 200

    def __init__(self, text):
        self.text = text


class _SinaHistorySession:
    def __init__(self):
        self.calls = 0

    def get(self, url, *_args, **_kwargs):
        self.calls += 1
        if "eastmoney" in url:
            raise TimeoutError("eastmoney timeout")
        return _SinaTextResponse(
            '[{"day":"2026-06-10","open":"68","high":"70","low":"67","close":"69","volume":"123"}]'
        )


class _StooqHistorySession:
    def get(self, url, *_args, **_kwargs):
        rows = ["Date,Open,High,Low,Close,Volume"]
        for i in range(80):
            day = (i % 28) + 1
            price = 100 + i * 0.5
            rows.append(
                f"2026-01-{day:02d},{price:.2f},{price + 1:.2f},{price - 1:.2f},{price + 0.25:.2f},{100000 + i}"
            )
        return _SinaTextResponse("\n".join(rows))


class _YahooChartSession:
    def get(self, url, *_args, **_kwargs):
        if "query1.finance.yahoo.com" in url:
            return _FakeResponse({
                "chart": {
                    "result": [{
                        "timestamp": [1781222400 + i * 86400 for i in range(80)],
                        "indicators": {
                            "quote": [{
                                "open": [100 + i * 0.5 for i in range(80)],
                                "high": [101 + i * 0.5 for i in range(80)],
                                "low": [99 + i * 0.5 for i in range(80)],
                                "close": [100.25 + i * 0.5 for i in range(80)],
                                "volume": [100000 + i for i in range(80)],
                            }]
                        },
                    }]
                }
            })
        raise TimeoutError("other provider unavailable")


def test_ashare_quote_prefers_eastmoney():
    from market_data_client import MarketDataClient

    client = MarketDataClient()
    client._sess = _EastmoneySession()

    quote = client._quote_ashare("300124.SZ")

    assert quote["success"] is True
    assert quote["symbol"] == "300124"
    assert quote["name"] == "汇川技术"
    assert quote["provider"] == "eastmoney"
    assert quote["provider_chain"] == ["eastmoney"]
    assert quote["volume"] == 12_345_600
    assert quote["market_cap"] == 12_345
    assert quote["volume_unit"] == "shares"
    assert quote["market_cap_unit"] == "CNY"


def test_ashare_quote_failure_returns_friendly_error(monkeypatch):
    from market_data_client import MarketDataClient

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"akshare", "yfinance"}:
            raise ImportError(f"{name} unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = MarketDataClient()
    client._sess = _FailingSession()

    quote = client._quote_ashare("300124")

    assert quote["success"] is False
    assert quote["provider_chain"] == ["eastmoney", "tencent", "sina", "akshare", "yfinance"]
    assert "已尝试 Eastmoney -> 腾讯 -> 新浪 -> AKShare -> Yahoo Finance" in quote["error"]
    assert "curl" not in quote["error"].lower()


def test_ashare_history_prefers_eastmoney():
    from market_data_client import MarketDataClient

    client = MarketDataClient()
    client._sess = _EastmoneyHistorySession()

    hist = client._history_ashare("300124.SZ", days=30, interval="1d")

    assert hist["success"] is True
    assert hist["provider"] == "eastmoney"
    assert hist["provider_chain"] == ["eastmoney"]
    assert hist["data"][0]["close"] == 68.5


def test_ashare_history_falls_back_to_sina():
    from market_data_client import MarketDataClient

    client = MarketDataClient()
    client._sess = _SinaHistorySession()

    hist = client._history_ashare("300124", days=30, interval="1d")

    assert hist["success"] is True
    assert hist["provider"] == "sina"
    assert hist["provider_chain"] == ["eastmoney", "sina"]
    assert hist["data"][0]["close"] == 69.0


def test_ashare_history_failure_returns_friendly_error(monkeypatch):
    from market_data_client import MarketDataClient

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"akshare", "yfinance"}:
            raise ImportError(f"{name} unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = MarketDataClient()
    client._sess = _FailingSession()

    hist = client._history_ashare("300124", days=30, interval="1d")

    assert hist["success"] is False
    assert hist["provider_chain"] == ["eastmoney", "sina", "akshare", "yfinance"]
    assert "已尝试 Eastmoney -> 新浪 -> AKShare -> Yahoo Finance" in hist["error"]
    assert "curl" not in hist["error"].lower()


class _FakeQuoteResult:
    def __init__(self):
        self.symbol = "600519"
        self.name = "贵州茅台"
        self.price = 1500.0
        self.change = 10.0
        self.change_pct = 0.67
        self.volume = 12345
        self.timestamp = "20260623"


class _FakeHistoryResult:
    def __init__(self, df):
        self.symbol = "600519"
        self.data = df
        self.source = "tushare"
        self.interval = "1d"


class _FakeTushareSource:
    """Minimal stand-in for datasources TushareSource."""
    def quote(self, code):
        return _FakeQuoteResult()

    def history(self, code, days=90):
        import pandas as pd
        idx = pd.to_datetime(["2026-06-20", "2026-06-21", "2026-06-22"])
        df = pd.DataFrame(
            {"open": [1, 2, 3], "high": [2, 3, 4], "low": [0.5, 1.5, 2.5],
             "close": [1.5, 2.5, 3.5], "volume": [100, 200, 300]},
            index=idx,
        )
        return _FakeHistoryResult(df)


def test_ashare_quote_uses_tushare_when_configured():
    """A configured Tushare source is preferred over the HTTP chain."""
    from market_data_client import MarketDataClient

    client = MarketDataClient()
    client._sess = _EastmoneySession()      # would otherwise win
    client._ts_source = _FakeTushareSource()  # explicit injection (token present)

    quote = client._quote_ashare("600519")

    assert quote["success"] is True
    assert quote["provider"] == "tushare"
    assert quote["provider_chain"] == ["tushare"]
    assert quote["name"] == "贵州茅台"
    assert quote["price"] == 1500.0


def test_ashare_history_uses_tushare_when_configured():
    from market_data_client import MarketDataClient

    client = MarketDataClient()
    client._sess = _EastmoneyHistorySession()  # would otherwise win
    client._ts_source = _FakeTushareSource()

    hist = client._history_ashare("600519", days=30, interval="1d")

    assert hist["success"] is True
    assert hist["provider"] == "tushare"
    assert hist["provider_chain"] == ["tushare"]
    assert hist["data"][-1]["close"] == 3.5
    assert hist["data"][0]["date"] == "2026-06-20"


def test_global_history_falls_back_to_stooq(monkeypatch):
    from market_data_client import MarketDataClient

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("yfinance unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = MarketDataClient()
    client._fh_key = ""
    client._sess = _StooqHistorySession()

    hist = client._history_yfinance("MSFT", days=60, interval="1d")

    assert hist["success"] is True
    assert hist["provider"] == "stooq"
    assert hist["data"][-1]["close"] > 100


def test_global_quote_falls_back_to_stooq_when_yfinance_unavailable(monkeypatch):
    from market_data_client import MarketDataClient

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("yfinance unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = MarketDataClient()
    client._fh_key = ""
    client._sess = _StooqHistorySession()

    quote = client._quote_yfinance("MSFT")

    assert quote["success"] is True
    assert quote["provider"] == "stooq"
    assert quote["price"] > 100


def test_global_history_uses_yahoo_chart_before_stooq(monkeypatch):
    from market_data_client import MarketDataClient

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("yfinance unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = MarketDataClient()
    client._fh_key = ""
    client._sess = _YahooChartSession()

    hist = client._history_yfinance("MSFT", days=60, interval="1d")

    assert hist["success"] is True
    assert hist["provider"] == "yahoo_chart"
    assert hist["data"][-1]["close"] > 100


def test_global_quote_uses_yahoo_chart_for_index_when_yfinance_unavailable(monkeypatch):
    from market_data_client import MarketDataClient

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("yfinance unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = MarketDataClient()
    client._fh_key = ""
    client._sess = _YahooChartSession()

    quote = client._quote_yfinance("^IXIC")

    assert quote["success"] is True
    assert quote["provider"] == "yahoo_chart"
    assert quote["price"] > 100
    assert quote["change_pct"] > 0


def test_global_ta_uses_stooq_history_when_yfinance_unavailable(monkeypatch):
    from market_data_client import MarketDataClient

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("yfinance unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = MarketDataClient()
    client._fh_key = ""
    client._sess = _StooqHistorySession()

    ta = client.technical_indicators("MSFT", days=60)

    assert ta["success"] is True
    assert ta["data_provider"] == "stooq"
    assert ta["rsi"] is not None
    assert ta["macd_hist"] is not None


def test_crypto_yahoo_style_symbol_normalizes_to_ccxt_pair():
    from market_data_client import _norm_crypto

    assert _norm_crypto("BTC-USD") == "BTC/USDT"
    assert _norm_crypto("ETH-USD") == "ETH/USDT"


def test_sh_index_secid_not_confused_with_sz_stock():
    """上证指数族(000001.SS)与深市个股(000001.SZ 平安银行)共享 000 前缀,
    secid 必须以交易所后缀为准——否则"上证指数"会返回平安银行的价格。"""
    from market_data_client import _ashare_secid, _is_sh_index_family, _yf_ashare_symbol

    assert _ashare_secid("000001", "000001.SS") == "1.000001"   # 上证综指 → 沪
    assert _ashare_secid("000001", "000001.SZ") == "0.000001"   # 平安银行 → 深
    assert _ashare_secid("000300", "000300.SS") == "1.000300"   # 沪深300 → 沪
    assert _ashare_secid("600519", "") == "1.600519"            # 无后缀按前缀猜:茅台沪
    assert _ashare_secid("000858", "") == "0.000858"            # 五粮液深

    assert _is_sh_index_family("000001", "000001.SS")
    assert not _is_sh_index_family("000001", "000001.SZ")

    assert _yf_ashare_symbol("000001", "000001.SS") == "000001.SS"
    assert _yf_ashare_symbol("000001", "") == "000001.SZ"


def test_fallback_currency_follows_market_suffix():
    """stooq/yfinance_download 兜底不带货币字段;按市场后缀推断,
    不再硬编码 USD(NL 分析块的"货币单位:{currency}"直接消费该字段)。"""
    from market_data_client import _currency_for_symbol

    assert _currency_for_symbol("0700.HK") == "HKD"
    assert _currency_for_symbol("MC.PA") == "EUR"
    assert _currency_for_symbol("SAP.DE") == "EUR"
    assert _currency_for_symbol("7203.T") == "JPY"
    assert _currency_for_symbol("600519.SS") == "CNY"
    assert _currency_for_symbol("AAPL") == "USD"
    assert _currency_for_symbol("GC=F") == "USD"


def test_finnhub_guard_skips_non_us_style_symbols():
    """Finnhub 免费档只覆盖美股普通代码;指数/期货/外汇/带市场后缀符号
    直接跳过(省一次注定失败的往返),由 yfinance 承接。"""
    from market_data_client import _finnhub_style_symbol

    assert _finnhub_style_symbol("AAPL")
    assert _finnhub_style_symbol("NVDA")
    assert not _finnhub_style_symbol("^GSPC")
    assert not _finnhub_style_symbol("GC=F")
    assert not _finnhub_style_symbol("EURUSD=X")
    assert not _finnhub_style_symbol("0700.HK")
    assert not _finnhub_style_symbol("SAP.DE")
