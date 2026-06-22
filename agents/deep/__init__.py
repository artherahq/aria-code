"""Deep analysis pipeline — Claude-Code-style layered research on top of AgentTeam.

Adds the four "deep" layers the flat team pipeline was missing:

  P0  deepen      — tool-augmented evidence gathering for material/uncertain findings
  P1  hierarchical + critic — theme-grouped sub-synthesis, then a self-check pass
  P2  quant fusion — fuse ML/backtest/risk signals as ground truth + calibrate confidence
  P3  tiered output + provenance — brief / standard / deep reports with data lineage

Everything degrades gracefully: any layer whose dependency (LLM, quant tools, data
cleaner) is unavailable is skipped, never raised. The deterministic parts (theme
grouping, calibration math, critic rules, tier rendering) run with no LLM/network,
which keeps the pipeline testable.
"""

from __future__ import annotations

from .models import (
    Critique,
    CritiqueIssue,
    DeepAnalysisResult,
    Provenance,
    QuantEvidence,
    ThemeGroup,
)
from .pipeline import DeepAnalysisPipeline, run_deep_analysis

__all__ = [
    "Critique",
    "CritiqueIssue",
    "DeepAnalysisResult",
    "Provenance",
    "QuantEvidence",
    "ThemeGroup",
    "DeepAnalysisPipeline",
    "run_deep_analysis",
]
