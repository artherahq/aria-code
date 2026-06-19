"""TradingView symbol mapping helpers.

TradingView is an optional chart/alert surface. These helpers only translate
Aria's canonical market symbols into TradingView URLs; they do not fetch or
trust TradingView data for analysis.
"""
from __future__ import annotations

from urllib.parse import quote


_INDEX_SYMBOLS = {
    "^GSPC": "SP:SPX",
    "^IXIC": "NASDAQ:IXIC",
    "^DJI": "DJ:DJI",
    "^RUT": "RUSSELL:RUT",
    "^VIX": "CBOE:VIX",
    "^HSI": "HKEX:HSI",
    "^HSTECH": "HKEX:HSTECH",
    "^N225": "TVC:NI225",
    "^FTSE": "TVC:UKX",
    "^GDAXI": "XETR:DAX",
    "^FCHI": "EURONEXT:PX1",
}

_FUTURES_SYMBOLS = {
    "GC=F": "COMEX:GC1!",
    "SI=F": "COMEX:SI1!",
    "CL=F": "NYMEX:CL1!",
    "BZ=F": "NYMEX:BRN1!",
    "HG=F": "COMEX:HG1!",
    "NG=F": "NYMEX:NG1!",
    "ZC=F": "CBOT:ZC1!",
    "ZS=F": "CBOT:ZS1!",
}

_FX_SYMBOLS = {
    "CNY=X": "FX_IDC:USDCNY",
    "EURUSD=X": "FX:EURUSD",
    "GBPUSD=X": "FX:GBPUSD",
    "JPY=X": "FX:USDJPY",
    "DX-Y.NYB": "TVC:DXY",
}


def tradingview_symbol(symbol: str) -> str:
    """Map an Aria canonical symbol to a TradingView symbol."""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    if s in _INDEX_SYMBOLS:
        return _INDEX_SYMBOLS[s]
    if s in _FUTURES_SYMBOLS:
        return _FUTURES_SYMBOLS[s]
    if s in _FX_SYMBOLS:
        return _FX_SYMBOLS[s]
    if s.endswith("-USD"):
        return f"BINANCE:{s[:-4]}USDT"
    if s.endswith(".HK"):
        digits = "".join(ch for ch in s[:-3] if ch.isdigit()).lstrip("0") or s[:-3]
        return f"HKEX:{digits}"
    if s.endswith(".SS") or (s.isdigit() and len(s) == 6 and s.startswith(("6", "9"))):
        return f"SSE:{s[:6]}"
    if s.endswith(".SZ") or (s.isdigit() and len(s) == 6):
        return f"SZSE:{s[:6]}"
    if "." in s:
        base, suffix = s.rsplit(".", 1)
        exchange = {
            "DE": "XETR",
            "PA": "EURONEXT",
            "AS": "EURONEXT",
            "MI": "MIL",
            "MC": "BME",
            "L": "LSE",
            "TO": "TSX",
        }.get(suffix, suffix)
        return f"{exchange}:{base}"
    return f"NASDAQ:{s}"


def tradingview_url(symbol: str) -> str:
    tv_symbol = tradingview_symbol(symbol)
    if not tv_symbol:
        return ""
    return f"https://www.tradingview.com/chart/?symbol={quote(tv_symbol, safe='')}"

