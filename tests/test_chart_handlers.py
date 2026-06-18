import pathlib
import sys


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


class _YahooChartResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "chart": {
                "result": [{
                    "timestamp": [1781222400, 1781308800],
                    "meta": {"currency": "USD"},
                    "indicators": {
                        "quote": [{
                            "open": [186.0, 190.0],
                            "high": [192.0, 195.0],
                            "low": [184.0, 188.0],
                            "close": [191.8, 193.4],
                            "volume": [1200000, 1350000],
                        }]
                    },
                }]
            }
        }


def test_yahoo_chart_fallback_returns_clean_ohlcv(monkeypatch):
    import apps.cli.handlers.chart_handlers as chart_handlers

    class FakeRequests:
        @staticmethod
        def get(*_args, **_kwargs):
            return _YahooChartResponse()

    monkeypatch.setitem(sys.modules, "requests", FakeRequests)

    hist, currency, error = chart_handlers._fetch_yahoo_chart_frame("SPCX", "1y", "1d")

    assert error == ""
    assert currency == "USD"
    assert list(hist.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert hist.iloc[-1]["Close"] == 193.4


def test_normalise_history_frame_rejects_missing_close():
    import pandas as pd
    import apps.cli.handlers.chart_handlers as chart_handlers

    hist = pd.DataFrame({"price": [1, 2, 3]})

    assert chart_handlers._normalise_history_frame(hist) is None

