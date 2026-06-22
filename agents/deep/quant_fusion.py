"""P2 — fuse quantitative ground truth and calibrate confidence.

The qualitative team votes a signal with a self-reported confidence. That number
is uncalibrated — an agent saying "80%" doesn't mean it's right 80% of the time.
This layer:

  1. gathers quant signals (AI signal, risk metrics, backtest) as ground truth,
  2. nudges confidence by whether quant *agrees* with the qualitative verdict,
  3. scales by a *reliability* factor learned from realised outcomes (CalibrationStore),

so confidence drifts toward the historical hit-rate as outcomes accumulate.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .models import QuantEvidence, Provenance

_BULL = ("STRONG_BUY", "BUY")
_BEAR = ("STRONG_SELL", "SELL")


# ── gather quant evidence ─────────────────────────────────────────────────────
def _default_provider(symbol: str) -> Dict[str, Dict]:
    """Best-effort pull of quant signals from local_finance_tools (each optional)."""
    out: Dict[str, Dict] = {}
    try:
        import local_finance_tools as lft
    except Exception:
        return out
    for key, fn in (("ai", "_get_ai_signal"), ("risk", "_get_risk_metrics"),
                    ("backtest", "_backtest_strategy"), ("factors", "_calculate_factors")):
        f = getattr(lft, fn, None)
        if not f:
            continue
        try:
            res = f({"symbol": symbol})
            if isinstance(res, dict) and res.get("success"):
                out[key] = res
        except Exception:
            pass
    return out


def _num(d: Dict, *keys) -> Optional[float]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def gather_quant_evidence(
    symbol: str,
    provider: Optional[Callable[[str], Dict[str, Dict]]] = None,
) -> Tuple[QuantEvidence, list]:
    """Return (QuantEvidence, [Provenance]). Never raises."""
    raw = {}
    try:
        raw = (provider or _default_provider)(symbol)
    except Exception:
        raw = {}

    prov: list = []
    ev = QuantEvidence()
    if not raw:
        ev.note = "no quant backend available"
        return ev, prov

    ai = raw.get("ai") or {}
    if ai:
        action = str(ai.get("action") or ai.get("signal") or "").upper()
        ev.ai_signal = action or None
        conf = _num(ai, "confidence") or 0.0
        if action in _BULL:
            ev.ai_score = conf
        elif action in _BEAR:
            ev.ai_score = -conf
        else:
            ev.ai_score = 0.0
        prov.append(Provenance("ai_signal", str(ai.get("provider", "quant")), note=action))

    risk = raw.get("risk") or {}
    if risk:
        ev.sharpe = _num(risk, "sharpe", "sharpe_ratio")
        ev.max_drawdown = _num(risk, "max_drawdown", "max_dd")
        prov.append(Provenance("risk_metrics", "local_finance", note="VaR/Sharpe"))

    bt = raw.get("backtest") or {}
    if bt:
        ev.backtest_return = _num(bt, "total_return", "return", "cagr")
        if ev.sharpe is None:
            ev.sharpe = _num(bt, "sharpe", "sharpe_ratio")
        if ev.max_drawdown is None:
            ev.max_drawdown = _num(bt, "max_drawdown", "max_dd")
        prov.append(Provenance("backtest", "local_finance", note=str(bt.get("strategy", ""))))

    fac = raw.get("factors") or {}
    if fac:
        ev.ic = _num(fac, "ic", "information_coefficient")
        ev.factors = {k: v for k, v in fac.items()
                      if k not in ("success", "symbol") and isinstance(v, (int, float))}
        prov.append(Provenance("factors", "local_finance"))

    ev.available = bool(ai or risk or bt or fac)
    return ev, prov


# ── confidence calibration ────────────────────────────────────────────────────
def _bucket(conf: float) -> str:
    if conf < 0.4:
        return "lo"
    if conf < 0.7:
        return "mid"
    return "hi"


def _side(signal: str) -> str:
    if signal in _BULL:
        return "bull"
    if signal in _BEAR:
        return "bear"
    return "neutral"


def agreement(agent_signal: str, quant_verdict: str) -> str:
    a = _side(agent_signal)
    q = {"BULLISH": "bull", "BEARISH": "bear", "NEUTRAL": "neutral"}.get(quant_verdict, "neutral")
    if a == "neutral" or q == "neutral":
        return "neutral"
    return "agree" if a == q else "disagree"


class CalibrationStore:
    """Tracks realised hit-rate per (signal-side, confidence-bucket) on disk.

    ``reliability(conf, signal)`` returns observed_hit_rate / nominal_confidence,
    clamped to a sane band, so a chronically over-confident bucket gets damped and
    an under-confident one gets a small boost. With no history it returns 1.0.
    """

    _NOMINAL = {"lo": 0.30, "mid": 0.55, "hi": 0.80}

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or os.path.expanduser("~/.arthera/deep_calibration.json"))
        self._data: Dict[str, Dict[str, int]] = {}
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text())
        except Exception:
            self._data = {}

    def _key(self, side: str, bucket: str) -> str:
        return f"{side}:{bucket}"

    def reliability(self, conf: float, signal: str = "") -> float:
        rec = self._data.get(self._key(_side(signal), _bucket(conf)))
        if not rec or rec.get("n", 0) < 8:
            return 1.0
        hit = rec["hit"] / rec["n"]
        nominal = self._NOMINAL[_bucket(conf)] or 1.0
        return max(0.6, min(1.25, hit / nominal))

    def record_outcome(self, signal: str, conf: float, correct: bool) -> None:
        k = self._key(_side(signal), _bucket(conf))
        rec = self._data.setdefault(k, {"n": 0, "hit": 0})
        rec["n"] += 1
        rec["hit"] += 1 if correct else 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2))
        except Exception:
            pass


def calibrate_confidence(
    raw_conf: float,
    agent_signal: str,
    quant: QuantEvidence,
    store: Optional[CalibrationStore] = None,
) -> Tuple[float, str]:
    """Return (calibrated_confidence, agreement_label)."""
    agree = agreement(agent_signal, quant.verdict()) if quant and quant.available else "neutral"
    factor = {"agree": 1.15, "neutral": 1.0, "disagree": 0.70}[agree]
    rel = store.reliability(raw_conf, agent_signal) if store else 1.0
    cal = max(0.0, min(1.0, raw_conf * factor * rel))
    return cal, agree
