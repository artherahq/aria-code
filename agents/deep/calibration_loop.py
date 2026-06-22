"""P2 closed loop — log predictions, later score them against realised price.

Calibration only improves if confidence is checked against what actually happened.
This module:

  1. logs every deep verdict (symbol, signal, confidence, reference price, time),
  2. once a prediction's horizon has elapsed, fetches the realised return and
     marks it correct/incorrect,
  3. feeds the outcome into CalibrationStore so the reliability factor — and thus
     future calibrated confidence — drifts toward the true hit-rate.

Everything is injectable (price function, clock) so it tests without network.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .quant_fusion import CalibrationStore

_BULL = ("STRONG_BUY", "BUY")
_BEAR = ("STRONG_SELL", "SELL")


def correctness(signal: str, ret: float, threshold: float = 0.02) -> bool:
    """Was the call right given the realised return?"""
    if signal in _BULL:
        return ret > threshold
    if signal in _BEAR:
        return ret < -threshold
    return abs(ret) <= threshold       # HOLD: right when it stayed flat


class PredictionLog:
    """Append-only log of deep verdicts, JSON-list backed (low volume)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or os.path.expanduser("~/.arthera/deep_predictions.json"))
        self._items: List[Dict] = []
        try:
            if self.path.exists():
                self._items = json.loads(self.path.read_text())
        except Exception:
            self._items = []

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def log(self, symbol: str, signal: str, confidence: float,
            ref_price: float, ts: Optional[float] = None) -> str:
        pid = uuid.uuid4().hex[:12]
        self._items.append({
            "id": pid, "symbol": symbol, "signal": signal,
            "confidence": float(confidence), "ref_price": float(ref_price),
            "ts": ts if ts is not None else time.time(), "evaluated": False,
        })
        self._save()
        return pid

    def pending(self, horizon_days: float, now: Optional[float] = None) -> List[Dict]:
        now = now if now is not None else time.time()
        cutoff = now - horizon_days * 86400
        return [p for p in self._items if not p.get("evaluated") and p["ts"] <= cutoff]

    def mark_evaluated(self, pid: str, ret: float, correct: bool,
                       source: str = "price") -> None:
        for p in self._items:
            if p["id"] == pid:
                p["evaluated"] = True
                p["realised_return"] = ret
                p["correct"] = correct
                p["source"] = source
                break
        self._save()


def evaluate_due(
    store: CalibrationStore,
    log: PredictionLog,
    price_fn: Callable[[str], Optional[float]],
    horizon_days: float = 5.0,
    threshold: float = 0.02,
    now: Optional[float] = None,
) -> Dict[str, int]:
    """Score every prediction past its horizon and update calibration. Returns counts."""
    evaluated = hits = 0
    for p in log.pending(horizon_days, now=now):
        try:
            cur = price_fn(p["symbol"])
        except Exception:
            cur = None
        if not cur or not p.get("ref_price"):
            continue
        ret = (cur - p["ref_price"]) / p["ref_price"]
        ok = correctness(p["signal"], ret, threshold)
        store.record_outcome(p["signal"], p["confidence"], ok)
        log.mark_evaluated(p["id"], round(ret, 5), ok)
        evaluated += 1
        hits += 1 if ok else 0
    return {"evaluated": evaluated, "hits": hits,
            "hit_rate": round(hits / evaluated, 3) if evaluated else 0.0}


def evaluate_from_ledger(
    store: CalibrationStore,
    log: PredictionLog,
    ledger,
) -> Dict[str, int]:
    """Score predictions against the portfolio ledger's REALISED P&L (actual closed
    trades) — a stronger ground truth than market price for symbols you traded.

    BUY is right when the symbol's realised P&L is positive, SELL when negative.
    HOLD is skipped (no clean threshold without a cost basis). ``ledger`` is any
    object exposing ``get_realized_pnl() -> [{symbol, realized_pnl, ...}]``.
    """
    try:
        realized = ledger.get_realized_pnl()
    except Exception:
        return {"evaluated": 0, "hits": 0, "hit_rate": 0.0}
    pnl_map = {str(r.get("symbol", "")).upper(): r.get("realized_pnl", 0.0)
               for r in (realized or []) if r.get("realized_pnl")}

    evaluated = hits = 0
    for p in log.pending(0.0):          # any un-evaluated prediction
        sym = str(p["symbol"]).upper()
        if sym not in pnl_map or p["signal"] not in (_BULL + _BEAR):
            continue
        pnl = pnl_map[sym]
        ok = (pnl > 0) if p["signal"] in _BULL else (pnl < 0)
        store.record_outcome(p["signal"], p["confidence"], ok)
        log.mark_evaluated(p["id"], round(pnl, 2), ok, source="ledger")
        evaluated += 1
        hits += 1 if ok else 0
    return {"evaluated": evaluated, "hits": hits,
            "hit_rate": round(hits / evaluated, 3) if evaluated else 0.0}
