"""TradingView symbol mapping helpers.

TradingView is an optional chart/alert surface. These helpers only translate
Aria's canonical market symbols into TradingView URLs; they do not fetch or
trust TradingView data for analysis.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from artifacts import slugify_topic, user_generated_dir


# ── Webhook security ──────────────────────────────────────────────────────────

def _expected_webhook_secret() -> str:
    return str(os.getenv("ARIA_WEBHOOK_SECRET", "") or "").strip()


def _alert_passphrase(raw: dict[str, Any]) -> str:
    """Pull the shared secret a TradingView alert body may carry.

    TradingView cannot send custom HMAC headers, so the documented way to secure
    its webhooks is a passphrase embedded in the JSON body. Accept the common
    field names.
    """
    for key in ("passphrase", "secret", "token", "key", "webhook_secret"):
        val = raw.get(key)
        if val:
            return str(val).strip()
    return ""


def verify_webhook_secret(raw: dict[str, Any]) -> bool:
    """True if alerts are allowed through.

    When ARIA_WEBHOOK_SECRET is unset, verification is disabled (open) — the
    deployment is assumed to be localhost-only. When set, the alert body MUST
    carry a matching passphrase (constant-time compare) or it is rejected.
    """
    expected = _expected_webhook_secret()
    if not expected:
        return True
    provided = _alert_passphrase(raw)
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def verify_webhook_hmac(raw_body: bytes | str, signature: str, secret: str | None = None) -> bool:
    """Constant-time HMAC-SHA256 check for a fronting proxy/signer.

    For deployments that put a signer in front of the daemon (e.g. a serverless
    relay that can compute HMAC, unlike TradingView itself). Compares the hex
    digest of ``raw_body`` against ``signature`` (optional ``sha256=`` prefix).
    """
    key = (secret if secret is not None else _expected_webhook_secret())
    if not key:
        return False
    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")
    sig = str(signature or "").strip()
    if sig.startswith("sha256="):
        sig = sig[7:]
    digest = hmac.new(key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, sig)


def _alert_dedup_key(alert: dict[str, Any]) -> str:
    """Stable key for collapsing duplicate alerts (TV retries the same bar)."""
    basis = "|".join(str(alert.get(k) or "") for k in ("symbol", "action", "time", "price"))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


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
        or data.get("syminfo.ticker")
        or data.get("syminfo.tickerid")
        or data.get("s")
        or ""
    )
    strategy = data.get("strategy") if isinstance(data.get("strategy"), dict) else {}
    action = (
        data.get("action")
        or data.get("side")
        or data.get("signal")
        or data.get("order_action")
        or data.get("strategy.order.action")
        or strategy.get("order_action")
        or ""
    )
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
        "price": data.get("price") or data.get("close") or data.get("last") or data.get("strategy.order.price"),
        "time": data.get("time") or data.get("timestamp") or data.get("t"),
        "message": data.get("message") or data.get("alert_message") or "",
        "channels": data.get("channels"),
        "raw": data,
    }


def _as_float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        return out if out == out else None
    except Exception:
        return None


def _first_present(raw: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in raw and raw.get(name) not in (None, ""):
            return raw.get(name)
    return None


def _strategy_field(raw: dict[str, Any], names: tuple[str, ...]) -> Any:
    strategy = raw.get("strategy")
    if not isinstance(strategy, dict):
        return None
    return _first_present(strategy, names)


def _current_position_quantity(broker: Any, symbol: str) -> float:
    sym = str(symbol or "").upper()
    try:
        for pos in broker.positions() or []:
            if str(getattr(pos, "symbol", "") or "").upper() == sym:
                return float(getattr(pos, "quantity", 0.0) or 0.0)
    except Exception:
        pass
    return 0.0


def _ensure_tradingview_broker(broker_id: str | None = None) -> Any:
    """Connect a broker for TradingView alert previews.

    If no broker is configured, create a local paper account. This keeps
    webhooks useful while never defaulting to live execution.
    """
    from brokers.config import add_broker_config, get_broker_config, get_default_broker_config, set_default_broker
    from brokers.registry import BrokerRegistry

    selected_id = str(broker_id or "").strip()
    if not selected_id:
        selected_id = str((get_default_broker_config() or {}).get("id") or "")

    if not selected_id:
        selected_id = "paper_main"
        if not get_broker_config(selected_id):
            add_broker_config({
                "id": selected_id,
                "type": "paper",
                "label": "Aria TradingView 仿盘",
                "mode": "paper",
                "starting_cash": 100000,
                "currency": "USD",
                "default": True,
            })
            set_default_broker(selected_id)

    registry = BrokerRegistry()
    return registry.connect(selected_id)


def build_tradingview_order_preview(
    payload: dict[str, Any] | str,
    *,
    broker: Any | None = None,
    broker_id: str | None = None,
) -> dict[str, Any]:
    """Turn a TradingView alert into an Aria trade preview.

    The function never executes an order. It only creates a `preview_id` through
    the broker trading service, so live trading still requires manual
    confirmation through `/trade confirm <preview_id>` or `broker_order`.
    """
    alert = parse_tradingview_alert(payload)
    symbol = str(alert.get("symbol") or "").upper()
    action = str(alert.get("action") or "ALERT").upper()
    raw = dict(alert.get("raw") or {})
    if not symbol:
        return {"success": False, "error": "symbol is required", "alert": alert}
    if action not in {"BUY", "SELL", "EXIT"}:
        return {
            "success": True,
            "trade_preview_created": False,
            "reason": "non_trade_alert",
            "alert": alert,
        }

    selected_broker_id = broker_id or raw.get("broker_id") or raw.get("account_id")
    if broker is None:
        try:
            broker = _ensure_tradingview_broker(str(selected_broker_id or "") or None)
        except Exception as exc:
            return {"success": False, "error": f"broker connect failed: {exc}", "alert": alert}

    qty = _as_float_or_none(
        _first_present(raw, (
            "quantity",
            "qty",
            "shares",
            "contracts",
            "order_size",
            "strategy.order.contracts",
            "strategy.position_size",
        ))
        or _strategy_field(raw, ("order_contracts", "position_size"))
    )
    target_weight = _as_float_or_none(_first_present(raw, ("target_weight", "weight", "target")))
    price = _as_float_or_none(alert.get("price"))
    order_type = str(raw.get("order_type") or raw.get("type") or "limit").lower()
    if order_type not in {"limit", "market"}:
        order_type = "limit"

    side = "buy" if action == "BUY" else "sell"
    if side == "sell" and qty is None:
        qty = _current_position_quantity(broker, symbol)
        if qty <= 0:
            return {
                "success": True,
                "trade_preview_created": False,
                "reason": "no_position_to_exit" if action == "EXIT" else "missing_quantity",
                "alert": alert,
                "broker_id": getattr(broker, "broker_id", ""),
                "broker_label": getattr(broker, "label", ""),
            }
    if side == "buy" and qty is None and target_weight is None:
        return {
            "success": True,
            "trade_preview_created": False,
            "reason": "missing_quantity",
            "alert": alert,
            "hint": "TradingView BUY alerts need quantity/qty or target_weight to create an order preview.",
            "broker_id": getattr(broker, "broker_id", ""),
            "broker_label": getattr(broker, "label", ""),
        }

    from brokers import OrderIntent, build_order_preview

    preview = build_order_preview(
        broker,
        OrderIntent(
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
            order_type=order_type,
            target_weight=target_weight,
            source="tradingview_alert",
            user_message=str(alert.get("message") or ""),
            metadata={
                "tradingview_action": action,
                "tradingview_time": alert.get("time"),
            },
        ),
    )
    return {
        "success": True,
        "trade_preview_created": True,
        "alert": alert,
        "preview_id": preview.get("preview_id"),
        "trade_preview": preview,
        "can_execute": preview.get("can_execute"),
        "mode": preview.get("mode"),
        "broker_id": preview.get("broker_id"),
        "broker_label": preview.get("broker_label"),
        "execution_blockers": preview.get("execution_blockers") or [],
        "confirm_command": f"/trade confirm {preview.get('preview_id')}",
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


def enqueue_tradingview_alert(
    payload: dict[str, Any] | str,
    *,
    db_path: str | Path | None = None,
    dedup_window_seconds: int = 90,
) -> dict[str, Any]:
    """Queue a TradingView alert for the daemon webhook executor.

    Security & integrity:
      * If ARIA_WEBHOOK_SECRET is set, the alert body must carry a matching
        passphrase, else it is rejected (prevents injected buy/sell alerts).
      * Duplicate alerts (TradingView retries the same bar) are collapsed within
        ``dedup_window_seconds`` so they can't create duplicate order drafts.
    """
    alert = parse_tradingview_alert(payload)
    if not alert["symbol"]:
        return {"success": False, "error": "symbol is required", "alert": alert}

    if not verify_webhook_secret(alert.get("raw") or {}):
        return {"success": False, "error": "unauthorized: missing/invalid webhook secret",
                "rejected": True, "alert": {"symbol": alert.get("symbol"), "action": alert.get("action")}}

    dedup_key = _alert_dedup_key(alert)
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
                done_at TEXT,
                dedup_key TEXT
            )
            """
        )
        # Older DBs may predate the dedup_key column — add it on the fly.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(webhook_jobs)")}
        if "dedup_key" not in cols:
            conn.execute("ALTER TABLE webhook_jobs ADD COLUMN dedup_key TEXT")
        existing = conn.execute(
            "SELECT id FROM webhook_jobs WHERE dedup_key = ? "
            "AND created_at >= datetime('now', ?) ORDER BY created_at DESC LIMIT 1",
            (dedup_key, f"-{int(dedup_window_seconds)} seconds"),
        ).fetchone()
        if existing:
            return {"success": True, "job_id": existing[0], "deduped": True, "alert": alert}
        conn.execute(
            "INSERT INTO webhook_jobs(id, command, payload, source, status, dedup_key) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (job_id, "tradingview_alert", json.dumps(alert, ensure_ascii=False), "tradingview", dedup_key),
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
alertcondition(longCondition, "Aria BUY {sym}", "{{\\"symbol\\":\\"{sym}\\",\\"action\\":\\"BUY\\",\\"quantity\\":1,\\"price\\":{{{{close}}}}}}")
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
