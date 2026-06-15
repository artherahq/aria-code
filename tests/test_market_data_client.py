import builtins
import pathlib
import sys


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

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
