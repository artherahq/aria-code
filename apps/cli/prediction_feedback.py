"""Local LLM-prediction outcome tracker — the finance ground-truth loop.

The market is an objective judge that Claude Code can't have: when Aria says
"NVDA 看多", the market settles it. We record directional/price calls at T=0,
verify them against real moves at T+N, and turn the result into a DPO signal:
  correct call   → "chosen" training example
  wrong  call    → "rejected" training example

Local-first: predictions live in ~/.arthera/predictions.jsonl; settled DPO
signals are appended to the FeedbackStore (shared=False until /privacy opt-in).
Honors the ARIA_NO_TELEMETRY kill switch. Never raises into the chat flow.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Directional keywords (zh + en).
_BULL = ("看多", "看涨", "买入", "增持", "做多", "上涨", "bullish", "buy", "long", "outperform")
_BEAR = ("看空", "看跌", "卖出", "减持", "做空", "下跌", "bearish", "sell", "short", "underperform")
_NEUTRAL = ("中性", "震荡", "观望", "持有", "neutral", "hold", "sideways")

_TARGET_RE = re.compile(
    r"(?:目标价|价格目标|target\s*price)[:：\s]*(?:约|~)?\s*[\$￥]?\s*([\d,]+\.?\d*)",
    re.IGNORECASE,
)

# Settlement threshold: a directional call is "correct" only if the move
# exceeds this magnitude (avoids crediting noise as a hit).
_MOVE_THRESHOLD = 0.01   # 1%


def detect_direction(text: str) -> str:
    """Return 'bullish' | 'bearish' | 'neutral' | '' from a response."""
    if not text:
        return ""
    t = text.lower()
    bull = sum(1 for k in _BULL if k.lower() in t)
    bear = sum(1 for k in _BEAR if k.lower() in t)
    neut = sum(1 for k in _NEUTRAL if k.lower() in t)
    if bull == 0 and bear == 0 and neut == 0:
        return ""
    if bull > bear and bull >= neut:
        return "bullish"
    if bear > bull and bear >= neut:
        return "bearish"
    if neut >= bull and neut >= bear:
        return "neutral"
    return ""


def extract_target_price(text: str) -> Optional[float]:
    m = _TARGET_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


class PredictionTracker:
    """Records and settles LLM market calls against real prices."""

    def __init__(self, config_dir: Path):
        self.path = Path(config_dir) / "predictions.jsonl"

    # ── record (T=0) ──────────────────────────────────────────────────────────
    def record(self, *, symbol: str, response_text: str, entry_price: float,
               session_id: str = "", model: str = "") -> Optional[Dict[str, Any]]:
        """Record a directional call if one is detectable. Returns the entry or None."""
        if os.environ.get("ARIA_NO_TELEMETRY"):
            return None
        if not symbol or not entry_price or entry_price <= 0:
            return None
        direction = detect_direction(response_text)
        if not direction:
            return None
        entry = {
            "id": f"{symbol}_{int(time.time())}",
            "symbol": symbol.upper(),
            "direction": direction,
            "target": extract_target_price(response_text),
            "entry_price": round(float(entry_price), 4),
            "excerpt": (response_text or "")[:400],
            "session_id": session_id,
            "model": model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            return None
        return entry

    # ── settle (T=N) ──────────────────────────────────────────────────────────
    def _load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        except Exception:
            return []
        return out

    def _save(self, rows: List[Dict[str, Any]]) -> None:
        try:
            with self.path.open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @staticmethod
    def _is_correct(direction: str, entry: float, now: float) -> Optional[bool]:
        move = (now - entry) / entry if entry else 0.0
        if direction == "bullish":
            return move > _MOVE_THRESHOLD
        if direction == "bearish":
            return move < -_MOVE_THRESHOLD
        if direction == "neutral":
            return abs(move) <= _MOVE_THRESHOLD
        return None

    def verify_pending(self, quote_fn: Callable[[str], Optional[float]],
                       min_age_hours: float = 24.0,
                       emit_feedback: Optional[Callable[[str, str, str], None]] = None
                       ) -> Dict[str, int]:
        """Settle pending predictions older than min_age_hours against live prices.

        quote_fn(symbol) -> current price (or None).
        emit_feedback(rating, message, comment) -> append a DPO signal (optional).
        Returns {"settled": n, "correct": c, "wrong": w}.
        """
        if os.environ.get("ARIA_NO_TELEMETRY"):
            return {"settled": 0, "correct": 0, "wrong": 0}
        rows = self._load()
        if not rows:
            return {"settled": 0, "correct": 0, "wrong": 0}
        now_ts = datetime.now(timezone.utc)
        settled = correct = wrong = 0
        for r in rows:
            if r.get("status") != "pending":
                continue
            try:
                created = datetime.fromisoformat(r["created_at"])
                age_h = (now_ts - created).total_seconds() / 3600.0
            except Exception:
                continue
            if age_h < min_age_hours:
                continue
            price_now = None
            try:
                price_now = quote_fn(r["symbol"])
            except Exception:
                price_now = None
            if not price_now or price_now <= 0:
                continue
            ok = self._is_correct(r["direction"], r["entry_price"], float(price_now))
            if ok is None:
                continue
            r["status"] = "correct" if ok else "wrong"
            r["exit_price"] = round(float(price_now), 4)
            r["settled_at"] = now_ts.isoformat()
            settled += 1
            correct += int(ok)
            wrong += int(not ok)
            if emit_feedback:
                _move = (float(price_now) - r["entry_price"]) / r["entry_price"] * 100
                _msg = r.get("excerpt", "")
                _cmt = (f"{r['symbol']} {r['direction']} "
                        f"{r['entry_price']}→{price_now} ({_move:+.1f}%) "
                        f"= {'命中' if ok else '落空'}")
                emit_feedback("prediction_correct" if ok else "prediction_wrong", _msg, _cmt)
        if settled:
            self._save(rows)
        return {"settled": settled, "correct": correct, "wrong": wrong}

    def accuracy(self) -> Dict[str, Any]:
        rows = self._load()
        done = [r for r in rows if r.get("status") in ("correct", "wrong")]
        pend = [r for r in rows if r.get("status") == "pending"]
        n = len(done)
        c = sum(1 for r in done if r["status"] == "correct")
        return {
            "total": len(rows),
            "settled": n,
            "pending": len(pend),
            "correct": c,
            "accuracy": round(c / n, 3) if n else None,
        }
