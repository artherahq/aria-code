"""Structured data model for the deep analysis pipeline.

Everything the pipeline produces is captured here as plain dataclasses with
``to_dict()`` so the whole analysis is machine-readable (downstream tools, audit,
training data) — not just a blob of synthesis text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── P3: data provenance / lineage ─────────────────────────────────────────────
@dataclass
class Provenance:
    """Where a datum came from and how fresh it is."""
    field:        str                       # e.g. "price", "fundamentals", "ai_signal"
    source:       str                       # e.g. "yfinance", "akshare", "ml_pipeline"
    fetched_at:   float = field(default_factory=time.time)
    age_sec:      Optional[float] = None    # data age (not fetch age) when known
    note:         str = ""

    @property
    def freshness(self) -> str:
        age = self.age_sec if self.age_sec is not None else (time.time() - self.fetched_at)
        if age < 90:
            return "live"
        if age < 3600:
            return f"{int(age // 60)}m old"
        if age < 86400:
            return f"{int(age // 3600)}h old"
        return f"{int(age // 86400)}d old"

    def to_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "source": self.source,
                "freshness": self.freshness, "note": self.note}


# ── P2: quantitative ground truth ─────────────────────────────────────────────
@dataclass
class QuantEvidence:
    """Quantitative signals used to anchor and calibrate the qualitative verdict."""
    ai_signal:        Optional[str] = None      # BUY/HOLD/SELL from the quant model
    ai_score:         Optional[float] = None     # -1..1 expected-return-ish score
    ic:               Optional[float] = None      # information coefficient (model skill)
    sharpe:           Optional[float] = None
    max_drawdown:     Optional[float] = None
    backtest_return:  Optional[float] = None
    factors:          Dict[str, Any] = field(default_factory=dict)
    available:        bool = False
    note:             str = ""

    def verdict(self) -> str:
        """Collapse the quant signals into BULLISH / BEARISH / NEUTRAL."""
        if not self.available:
            return "NEUTRAL"
        if self.ai_signal in ("STRONG_BUY", "BUY"):
            return "BULLISH"
        if self.ai_signal in ("STRONG_SELL", "SELL"):
            return "BEARISH"
        if self.ai_score is not None:
            if self.ai_score >= 0.15:
                return "BULLISH"
            if self.ai_score <= -0.15:
                return "BEARISH"
        return "NEUTRAL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ai_signal": self.ai_signal, "ai_score": self.ai_score,
            "ic": self.ic, "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown, "backtest_return": self.backtest_return,
            "verdict": self.verdict(), "available": self.available, "note": self.note,
        }


# ── P1: hierarchical synthesis ────────────────────────────────────────────────
@dataclass
class ThemeGroup:
    """A cluster of agents that speak to the same theme (valuation, momentum, …)."""
    theme:        str
    agents:       List[str] = field(default_factory=list)
    signal:       str = "HOLD"
    confidence:   float = 0.0
    summary:      str = ""
    key_points:   List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"theme": self.theme, "agents": self.agents, "signal": self.signal,
                "confidence": round(self.confidence, 3), "summary": self.summary,
                "key_points": self.key_points}


# ── P1: critic / self-check ───────────────────────────────────────────────────
@dataclass
class CritiqueIssue:
    severity: str       # "high" | "medium" | "low"
    kind:     str       # "unsupported" | "missing_risk" | "stale_data" | "thin_coverage" | "conflict"
    message:  str

    def to_dict(self) -> Dict[str, Any]:
        return {"severity": self.severity, "kind": self.kind, "message": self.message}


@dataclass
class Critique:
    issues:  List[CritiqueIssue] = field(default_factory=list)
    passed:  bool = True

    @property
    def high(self) -> List[CritiqueIssue]:
        return [i for i in self.issues if i.severity == "high"]

    def to_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "issues": [i.to_dict() for i in self.issues]}


# ── Top-level result ──────────────────────────────────────────────────────────
@dataclass
class DeepAnalysisResult:
    symbol:               str
    final_signal:         str = "HOLD"
    raw_confidence:       float = 0.0        # team vote, uncalibrated
    calibrated_confidence: float = 0.0       # after quant fusion + reliability
    themes:               List[ThemeGroup] = field(default_factory=list)
    quant:                Optional[QuantEvidence] = None
    critique:             Optional[Critique] = None
    provenance:           List[Provenance] = field(default_factory=list)
    synthesis:            str = ""           # top-level narrative (post-critic)
    agent_results:        List[Dict[str, Any]] = field(default_factory=list)
    elapsed_sec:          float = 0.0
    error:                Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "final_signal": self.final_signal,
            "raw_confidence": round(self.raw_confidence, 3),
            "calibrated_confidence": round(self.calibrated_confidence, 3),
            "themes": [t.to_dict() for t in self.themes],
            "quant": self.quant.to_dict() if self.quant else None,
            "critique": self.critique.to_dict() if self.critique else None,
            "provenance": [p.to_dict() for p in self.provenance],
            "synthesis": self.synthesis,
            "elapsed_sec": self.elapsed_sec,
            "error": self.error,
        }
