"""TradingView symbol mapping helpers.

TradingView is an optional chart/alert surface. These helpers only translate
Aria's canonical market symbols into TradingView URLs; they do not fetch or
trust TradingView data for analysis.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from artifacts import slugify_topic, user_generated_dir


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


def tradingview_url(symbol: str, *, interval: str | None = None) -> str:
    tv_symbol = tradingview_symbol(symbol)
    if not tv_symbol:
        return ""
    url = f"https://www.tradingview.com/chart/?symbol={quote(tv_symbol, safe='')}"
    if interval:
        url += f"&interval={quote(str(interval), safe='')}"
    return url


def parse_tradingview_alert(payload: dict[str, Any] | str) -> dict[str, Any]:
    """Normalize a TradingView webhook payload.

    TradingView alert bodies are user-defined, so accept common JSON fields and
    a compact text fallback such as "NVDA buy".
    """
    if isinstance(payload, str):
        raw = payload.strip()
        try:
            payload = json.loads(raw)
        except Exception:
            parts = raw.replace(",", " ").split()
            payload = {
                "symbol": parts[0] if parts else "",
                "action": parts[1] if len(parts) > 1 else "",
                "message": raw,
            }
    data = dict(payload or {})
    raw_symbol = (
        data.get("symbol")
        or data.get("ticker")
        or data.get("tv_symbol")
        or data.get("s")
        or ""
    )
    strategy = data.get("strategy") if isinstance(data.get("strategy"), dict) else {}
    action = data.get("action") or data.get("side") or data.get("signal") or strategy.get("order_action") or ""
    symbol = normalize_tradingview_alert_symbol(str(raw_symbol))
    action_norm = str(action or "").strip().upper()
    if action_norm in {"LONG", "BUY", "B"}:
        action_norm = "BUY"
    elif action_norm in {"SHORT", "SELL", "S"}:
        action_norm = "SELL"
    elif action_norm in {"EXIT", "CLOSE", "FLAT"}:
        action_norm = "EXIT"
    elif not action_norm:
        action_norm = "ALERT"
    return {
        "symbol": symbol,
        "action": action_norm,
        "price": data.get("price") or data.get("close") or data.get("last"),
        "time": data.get("time") or data.get("timestamp") or data.get("t"),
        "message": data.get("message") or data.get("alert_message") or "",
        "channels": data.get("channels"),
        "raw": data,
    }


def normalize_tradingview_alert_symbol(symbol: str) -> str:
    """Convert common TradingView symbols back to Aria/yfinance-style symbols."""
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    if ":" in raw:
        exchange, ticker = raw.split(":", 1)
        ticker = ticker.strip()
        if exchange in {"NASDAQ", "NYSE", "AMEX"}:
            return ticker
        if exchange == "HKEX":
            return ticker.zfill(4) + ".HK"
        if exchange == "SSE":
            return ticker.zfill(6)
        if exchange == "SZSE":
            return ticker.zfill(6)
        if exchange in {"BINANCE", "BYBIT", "OKX"} and ticker.endswith("USDT"):
            return ticker[:-4] + "-USD"
        if exchange in {"COMEX", "NYMEX", "CBOT"} and ticker.endswith("1!"):
            reverse = {value: key for key, value in _FUTURES_SYMBOLS.items()}
            return reverse.get(raw, ticker)
        if exchange == "FX":
            reverse = {value: key for key, value in _FX_SYMBOLS.items()}
            return reverse.get(raw, ticker + "=X")
        return ticker
    if raw.endswith("USDT"):
        return raw[:-4] + "-USD"
    return raw


def enqueue_tradingview_alert(payload: dict[str, Any] | str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    """Queue a TradingView alert for the daemon webhook executor."""
    alert = parse_tradingview_alert(payload)
    if not alert["symbol"]:
        return {"success": False, "error": "symbol is required", "alert": alert}
    path = Path(db_path).expanduser() if db_path else Path.home() / ".aria" / "daemon.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    job_id = "tv_" + uuid.uuid4().hex[:12]
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS webhook_jobs (
                id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                source TEXT DEFAULT 'external',
                status TEXT DEFAULT 'pending',
                result TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                done_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO webhook_jobs(id, command, payload, source, status) VALUES (?, ?, ?, ?, 'pending')",
            (job_id, "tradingview_alert", json.dumps(alert, ensure_ascii=False), "tradingview"),
        )
        conn.commit()
    return {"success": True, "job_id": job_id, "alert": alert}


def generate_pine_strategy(symbol: str, *, name: str | None = None) -> str:
    """Generate a TradingView Pine Script strategy template."""
    sym = str(symbol or "SYMBOL").strip().upper()
    title = name or f"Aria {sym} EMA RSI Strategy"
    return f"""//@version=5
strategy("{title}", overlay=true, initial_capital=100000, commission_type=strategy.commission.percent, commission_value=0.05)

fastLen = input.int(20, "Fast EMA", minval=1)
slowLen = input.int(60, "Slow EMA", minval=1)
rsiLen = input.int(14, "RSI Length", minval=1)
rsiBuy = input.float(55, "RSI buy threshold")
rsiSell = input.float(45, "RSI sell threshold")

fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
rsi = ta.rsi(close, rsiLen)

longCondition = ta.crossover(fast, slow) and rsi > rsiBuy
exitCondition = ta.crossunder(fast, slow) or rsi < rsiSell

if longCondition
    strategy.entry("Aria Long", strategy.long)

if exitCondition
    strategy.close("Aria Long")

plot(fast, "Fast EMA", color=color.teal)
plot(slow, "Slow EMA", color=color.orange)
alertcondition(longCondition, "Aria BUY {sym}", "{{\\"symbol\\":\\"{sym}\\",\\"action\\":\\"BUY\\",\\"price\\":{{{{close}}}}}}")
alertcondition(exitCondition, "Aria EXIT {sym}", "{{\\"symbol\\":\\"{sym}\\",\\"action\\":\\"EXIT\\",\\"price\\":{{{{close}}}}}}")
"""


def export_pine_strategy(symbol: str, *, name: str | None = None, output_dir: str | Path | None = None) -> Path:
    """Write a Pine Script strategy file and return its path."""
    sym = str(symbol or "SYMBOL").strip().upper()
    directory = Path(output_dir).expanduser() if output_dir else user_generated_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fname = f"{int(time.time())}_{slugify_topic(sym, 'symbol')}_strategy.pine"
    path = directory / fname
    path.write_text(generate_pine_strategy(sym, name=name), encoding="utf-8")
    return path
