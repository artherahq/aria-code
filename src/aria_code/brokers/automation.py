"""Controlled auto-execution for strategy / alert-driven trading.

OFF BY DEFAULT. An order auto-executes only when EVERY gate passes:

  1. Global kill-switch off  — ARIA_DRY_RUN not set        (brokers.trading.global_dry_run)
  2. Automation enabled      — automation.enabled=true      (per-broker opt-in)
  3. Risk/exec passed        — preview.can_execute is True   (no execution_blockers;
                                covers read_only / live-without-allow_live_trade)
  4. Direction/symbol allow  — within allowed_sides / allowed_symbols
  5. Value & rate limits     — max_order_value, max_orders_per_day,
                                max_orders_per_symbol_per_day

Otherwise the order is LEFT AS A DRAFT (the existing semi-auto behaviour) and the
skip is audited. Every decision — execute or skip — is written to the trade audit
log so there is a full paper trail.

This module never lowers an existing guard: it ADDS gates on top of the normal
preview → risk → confirm pipeline (auto-execution simply supplies the confirm).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .trading import (
    TRADE_AUDIT_PATH,
    _audit,
    execute_order_preview,
    global_dry_run,
    load_order_preview,
)


@dataclass(frozen=True)
class AutoExecutePolicy:
    """Per-broker automation policy. Disabled unless explicitly turned on."""
    enabled: bool = False
    max_order_value: float = 0.0          # 0 = no extra cap beyond risk rules
    max_orders_per_day: int = 10
    max_orders_per_symbol_per_day: int = 3
    allowed_sides: tuple[str, ...] = ("buy", "sell")
    allowed_symbols: tuple[str, ...] = ()  # empty = all symbols allowed

    @classmethod
    def from_config(cls, config: Dict[str, Any] | None) -> "AutoExecutePolicy":
        auto = ((config or {}).get("automation") or {})
        if not isinstance(auto, dict):
            return cls()
        sides = auto.get("allowed_sides") or ["buy", "sell"]
        syms = auto.get("allowed_symbols") or []
        return cls(
            enabled=bool(auto.get("enabled", False)),
            max_order_value=float(auto.get("max_order_value", 0.0) or 0.0),
            max_orders_per_day=int(auto.get("max_orders_per_day", 10) or 0),
            max_orders_per_symbol_per_day=int(auto.get("max_orders_per_symbol_per_day", 3) or 0),
            allowed_sides=tuple(str(s).lower() for s in sides),
            allowed_symbols=tuple(str(s).upper() for s in syms),
        )


@dataclass
class AutoExecuteDecision:
    allowed: bool
    reasons: List[str] = field(default_factory=list)


def _order_from_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    return ((preview.get("order_plan") or {}).get("estimated_order") or {})


def _order_value(order: Dict[str, Any]) -> float:
    for key in ("value", "notional", "amount"):
        try:
            v = float(order.get(key) or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    try:
        return float(order.get("price") or 0) * float(order.get("quantity") or 0)
    except (TypeError, ValueError):
        return 0.0


def evaluate_auto_execute(
    preview: Dict[str, Any],
    policy: AutoExecutePolicy,
    *,
    dry_run: bool,
    executed_today: int = 0,
    executed_today_symbol: int = 0,
) -> AutoExecuteDecision:
    """Pure gate evaluation. Returns allowed=True only if ALL gates pass."""
    reasons: List[str] = []
    if dry_run:
        reasons.append("ARIA_DRY_RUN 全局冻结，禁止自动执行")
    if not policy.enabled:
        reasons.append("自动执行未开启 (automation.enabled=false)")
    if not preview.get("can_execute"):
        blockers = preview.get("execution_blockers") or []
        reasons.append("风控/执行未通过: " + ("; ".join(str(b) for b in blockers[:3]) or "can_execute=false"))

    order = _order_from_preview(preview)
    side = str(order.get("side") or preview.get("side") or "").lower()
    sym = str(order.get("symbol") or preview.get("symbol") or "").upper()

    if policy.allowed_sides and side and side not in policy.allowed_sides:
        reasons.append(f"方向 {side} 不在允许列表 {list(policy.allowed_sides)}")
    if policy.allowed_symbols and sym and sym not in policy.allowed_symbols:
        reasons.append(f"标的 {sym} 不在自动交易白名单")

    val = _order_value(order)
    if policy.max_order_value and val > policy.max_order_value:
        reasons.append(f"单笔金额 {val:.2f} 超过上限 {policy.max_order_value:.2f}")
    if policy.max_orders_per_day and executed_today >= policy.max_orders_per_day:
        reasons.append(f"已达当日自动单上限 {policy.max_orders_per_day}")
    if policy.max_orders_per_symbol_per_day and executed_today_symbol >= policy.max_orders_per_symbol_per_day:
        reasons.append(f"{sym} 已达当日自动单上限 {policy.max_orders_per_symbol_per_day}")

    return AutoExecuteDecision(allowed=(not reasons), reasons=reasons)


def _count_auto_executed_today(broker_id: str, symbol: Optional[str] = None) -> int:
    """Count successful auto-executions today (from the trade audit log)."""
    if not TRADE_AUDIT_PATH.exists():
        return 0
    today = datetime.now(timezone.utc).date()
    sym = (symbol or "").upper()
    n = 0
    try:
        for line in TRADE_AUDIT_PATH.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("event") != "auto_execute" or not row.get("success"):
                continue
            if broker_id and row.get("broker_id") != broker_id:
                continue
            if sym and str(row.get("symbol") or "").upper() != sym:
                continue
            ts = row.get("ts")
            if ts and datetime.fromtimestamp(int(ts), tz=timezone.utc).date() != today:
                continue
            n += 1
    except Exception:
        return n
    return n


def run_auto_execute(
    broker: Any,
    preview_id: str,
    policy: Optional[AutoExecutePolicy] = None,
) -> Dict[str, Any]:
    """Auto-execute a preview iff all gates pass; otherwise leave it as a draft.

    Returns a dict with ``auto_executed`` / ``skipped`` and the gate ``reasons``.
    """
    preview = load_order_preview(preview_id)
    if not preview:
        return {"success": False, "error": f"preview not found: {preview_id}"}

    if policy is None:
        policy = AutoExecutePolicy.from_config(getattr(broker, "config", {}) or {})

    broker_id = getattr(broker, "broker_id", "")
    symbol = str(preview.get("symbol") or _order_from_preview(preview).get("symbol") or "")
    decision = evaluate_auto_execute(
        preview, policy,
        dry_run=global_dry_run(),
        executed_today=_count_auto_executed_today(broker_id),
        executed_today_symbol=_count_auto_executed_today(broker_id, symbol),
    )

    if not decision.allowed:
        _audit({"event": "auto_execute_skipped", "preview_id": preview_id,
                "broker_id": broker_id, "symbol": symbol.upper(),
                "reasons": decision.reasons, "auto": True})
        return {"success": False, "auto_executed": False, "skipped": True,
                "reasons": decision.reasons, "preview_id": preview_id}

    result = execute_order_preview(broker, preview_id, confirmed=True)
    success = bool(result.get("success"))
    _audit({"event": "auto_execute", "preview_id": preview_id,
            "broker_id": broker_id, "symbol": symbol.upper(),
            "success": success, "auto": True})
    result["auto_executed"] = success
    return result
