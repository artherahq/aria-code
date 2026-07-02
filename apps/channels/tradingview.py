"""TradingView alert → structured channel task (aria.channel_task.v1).

The channels contract requires external entrypoints to submit *structured
tasks* to the gateway instead of poking CLI internals. This adapter turns a
raw TradingView webhook payload into that task shape, reusing the hardened
primitives that already live in apps/cli/tradingview_bridge.py (field-tolerant
parsing, passphrase + HMAC verification, dedup keys).

Verification posture mirrors the bridge's documented semantics: with
ARIA_WEBHOOK_SECRET unset verification is open (localhost-only assumption),
but the task carries explicit ``verified`` / ``open_mode`` flags so a
downstream gate (daemon, SafetyService) can refuse open-mode tasks in
non-local deployments.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from apps.cli.tradingview_bridge import (
    _alert_dedup_key as alert_dedup_key,
    _expected_webhook_secret,
    parse_tradingview_alert,
    verify_webhook_hmac,
    verify_webhook_secret,
)
from apps.channels.registry import CHANNEL_TASK_SCHEMA


def alert_to_task(
    payload: Dict[str, Any] | str,
    *,
    raw_body: Optional[bytes | str] = None,
    signature: Optional[str] = None,
    clock: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """Normalize + authenticate one webhook delivery.

    Returns ``{"success": True, "task": {...}}`` or
    ``{"success": False, "error": ...}`` (tool-result convention).
    """
    alert = parse_tradingview_alert(payload)
    raw = alert.get("raw") or {}

    secret_configured = bool(_expected_webhook_secret())
    if signature is not None:
        # A fronting signer supplied an HMAC — it must verify (never open).
        if not verify_webhook_hmac(raw_body if raw_body is not None else "", signature):
            return {"success": False, "error": "tradingview alert rejected: HMAC signature mismatch"}
        verified = True
    else:
        if not verify_webhook_secret(raw):
            return {"success": False, "error": "tradingview alert rejected: passphrase missing or wrong"}
        verified = secret_configured  # open mode when no secret is configured

    if not alert.get("symbol"):
        return {"success": False, "error": "tradingview alert rejected: no symbol in payload"}

    task = {
        "schema": CHANNEL_TASK_SCHEMA,
        "channel": "tradingview",
        "kind": "alert",
        "received_at": clock(),
        "verified": verified,
        "open_mode": not secret_configured and signature is None,
        "dedup_key": alert_dedup_key(alert),
        "symbol": alert["symbol"],
        "action": alert["action"],
        "price": alert.get("price"),
        "time": alert.get("time"),
        "message": alert.get("message") or "",
        "prompt": task_prompt(alert),
    }
    return {"success": True, "task": task}


def task_prompt(alert: Dict[str, Any]) -> str:
    """Render the gateway-facing natural-language task for this alert.

    The daemon submits this prompt to runtime's ``run_turn`` — the agent then
    analyses the alert with its normal tools; nothing here places orders.
    """
    symbol = alert.get("symbol", "")
    action = alert.get("action", "ALERT")
    price = alert.get("price")
    at = f" at {price}" if price not in (None, "") else ""
    msg = str(alert.get("message") or "").strip()
    tail = f' Alert message: "{msg}".' if msg else ""
    return (
        f"TradingView alert received: {action} signal for {symbol}{at}.{tail} "
        f"Assess the signal against current market data and the portfolio, and "
        f"summarize whether it warrants action. Do not place any orders."
    )
