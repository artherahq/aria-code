"""
agents/portfolio_agent.py — 组合级分析 Agent
=============================================
跨标的分析：相关性矩阵、波动率、集中度风险、再平衡建议。
数据源：yfinance 1年日线（免费，无需 key）
触发：/portfolio analyze [symbols…]
      /portfolio rebalance [symbols…]
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class PortfolioAgent(BaseAgent):

    name        = "portfolio"
    description = "组合级分析 — 相关性、分散度、整体风险、再平衡建议"

    _SYSTEM = (
        "You are a portfolio risk analyst. Given price history and statistics for "
        "a portfolio of stocks, your job is to:\n"
        "1. Evaluate overall portfolio risk (Low / Medium / High)\n"
        "2. Identify diversification gaps — highly correlated pairs, sector crowding\n"
        "3. Flag concentration risk if any single position dominates\n"
        "4. Give 2-3 actionable rebalancing suggestions (be specific: which stocks to "
        "trim / add / replace and why)\n"
        "5. End with a one-line verdict: HEALTHY / NEEDS_ATTENTION / HIGH_RISK\n"
        "Be concrete. Avoid boilerplate."
    )

    # ── BaseAgent compatibility ───────────────────────────────────────────────

    async def fetch_data(self, symbol: str) -> Dict[str, Any]:
        symbols = [s.strip().upper() for s in symbol.split(",") if s.strip()]
        return await self.fetch_portfolio_data(symbols)

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        symbols = data.get("symbols") or [s.strip().upper() for s in symbol.split(",") if s.strip()]
        return await self.analyze_portfolio(symbols, data)

    # ── Multi-symbol interface ────────────────────────────────────────────────

    async def run_portfolio(self, symbols: List[str]) -> AgentResult:
        """Primary entry point for multi-symbol analysis."""
        data   = await self.fetch_portfolio_data(symbols)
        return await self.analyze_portfolio(symbols, data)

    async def fetch_portfolio_data(self, symbols: List[str]) -> Dict[str, Any]:
        data: Dict[str, Any] = {"symbols": symbols}
        if len(symbols) < 2:
            return data

        price_series: Dict[str, Any] = {}
        latest_prices: Dict[str, float] = {}
        sector_map: Dict[str, str] = {}

        for sym in symbols:
            try:
                import yfinance as yf
                ticker = yf.Ticker(sym)
                hist   = ticker.history(period="1y")
                if not hist.empty and len(hist) > 20:
                    price_series[sym] = hist["Close"]
                    latest_prices[sym] = float(hist["Close"].iloc[-1])
                    # Try to get sector info
                    try:
                        info = ticker.info or {}
                        sector = info.get("sector") or info.get("industry", "")
                        if sector:
                            sector_map[sym] = sector
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("[portfolio] yfinance %s: %s", sym, e)

        if len(price_series) < 2:
            data["error"] = "insufficient_data"
            return data

        try:
            import pandas as pd
            import numpy as np

            df      = pd.DataFrame(price_series).dropna()
            returns = df.pct_change().dropna()

            corr        = returns.corr()
            ann_returns = (returns.mean() * 252).round(4)
            ann_vols    = (returns.std() * np.sqrt(252)).round(4)
            cov_matrix  = returns.cov() * 252
            valid_syms  = list(df.columns)
            n           = len(valid_syms)
            weights     = np.ones(n) / n
            port_vol    = float(np.sqrt(weights @ cov_matrix.values @ weights))

            # High correlation pairs (|r| > 0.70)
            high_corr: List[Dict] = []
            for i in range(len(valid_syms)):
                for j in range(i + 1, len(valid_syms)):
                    c = float(corr.iloc[i, j])
                    if abs(c) > 0.70:
                        high_corr.append({
                            "sym1": valid_syms[i],
                            "sym2": valid_syms[j],
                            "corr": round(c, 3),
                        })

            # Diversification ratio: weighted avg individual vol / portfolio vol
            avg_vol = float(np.mean([float(ann_vols.get(s, 0)) for s in valid_syms]))
            div_ratio = round(avg_vol / port_vol, 2) if port_vol > 0 else 1.0

            # 52-week return per symbol
            returns_1y: Dict[str, float] = {}
            for sym in valid_syms:
                first = float(df[sym].iloc[0])
                last  = float(df[sym].iloc[-1])
                if first > 0:
                    returns_1y[sym] = round((last - first) / first, 4)

            # Best/worst performer
            sorted_ret = sorted(returns_1y.items(), key=lambda x: x[1], reverse=True)

            data.update({
                "valid_symbols": valid_syms,
                "latest_prices": latest_prices,
                "ann_returns":   {s: float(v) for s, v in ann_returns.items()},
                "ann_vols":      {s: float(v) for s, v in ann_vols.items()},
                "port_vol_ann":  round(port_vol, 4),
                "div_ratio":     div_ratio,
                "corr_matrix":   corr.round(3).to_dict(),
                "high_corr":     high_corr,
                "returns_1y":    returns_1y,
                "best_performer":  sorted_ret[0]  if sorted_ret else None,
                "worst_performer": sorted_ret[-1] if sorted_ret else None,
                "sector_map":    sector_map,
            })

        except ImportError:
            data["error"] = "pandas/numpy not available"
        except Exception as e:
            logger.warning("[portfolio] stats calculation: %s", e)
            data["error"] = str(e)

        return data

    async def analyze_portfolio(
        self, symbols: List[str], data: Dict[str, Any]
    ) -> AgentResult:
        if len(symbols) < 2:
            return AgentResult(
                agent=self.name, symbol=",".join(symbols),
                analysis="组合分析需要至少 2 个标的。",
                confidence=0.0, signal="HOLD",
                key_points=["标的数量不足"],
            )

        if data.get("error") == "insufficient_data":
            return AgentResult(
                agent=self.name, symbol=",".join(symbols),
                analysis="无法获取足够的历史价格数据进行组合分析。",
                confidence=0.3, signal="HOLD",
                key_points=["历史数据不足（需要 >20 个交易日）"],
            )

        valid_syms = data.get("valid_symbols", symbols)
        port_block = _format_portfolio_stats(data)

        prompt = (
            f"Portfolio: {', '.join(valid_syms)} ({len(valid_syms)} positions, equal weight)\n\n"
            f"{port_block}\n\n"
            "Analyze this portfolio:\n"
            "1. Overall risk level (Low / Medium / High)\n"
            "2. Diversification quality — any dangerous correlations or sector crowding?\n"
            "3. Top 2-3 concerns (be specific about which symbols)\n"
            "4. Concrete rebalancing suggestions\n"
            "5. End with: HEALTHY / NEEDS_ATTENTION / HIGH_RISK"
        )

        analysis = await self._call_llm(self._SYSTEM, prompt, max_tokens=650)
        if not analysis:
            analysis = _template_analysis(valid_syms, data)

        verdict    = _extract_verdict(analysis)
        signal     = _verdict_to_signal(verdict)
        confidence = _estimate_confidence(data)
        key_points = _build_key_points(data, verdict)

        return AgentResult(
            agent=self.name,
            symbol=",".join(valid_syms),
            analysis=analysis,
            confidence=confidence,
            signal=signal,
            key_points=key_points,
            data_used={
                "n": len(valid_syms),
                "port_vol_ann": data.get("port_vol_ann"),
                "div_ratio": data.get("div_ratio"),
                "high_corr_count": len(data.get("high_corr", [])),
            },
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_portfolio_stats(d: Dict) -> str:
    lines = []

    # Returns & volatility table
    vols    = d.get("ann_vols", {})
    rets    = d.get("ann_returns", {})
    rets_1y = d.get("returns_1y", {})
    if vols:
        lines.append("Symbol │ Ann.Vol │ Ann.Ret │ 1Y Return")
        lines.append("─" * 42)
        for sym in d.get("valid_symbols", list(vols)):
            v  = vols.get(sym, 0)
            r  = rets.get(sym, 0)
            y  = rets_1y.get(sym, 0)
            lines.append(
                f"{sym:<6} │ {v*100:>6.1f}% │ {r*100:>6.1f}% │ {y*100:>+7.1f}%"
            )

    port_vol = d.get("port_vol_ann", 0)
    div_r    = d.get("div_ratio", 1)
    if port_vol:
        lines.append(f"\nPortfolio Ann.Vol: {port_vol*100:.1f}%")
        lines.append(f"Diversification Ratio: {div_r:.2f}x "
                     f"({'good' if div_r >= 1.3 else 'poor'})")

    high_corr = d.get("high_corr", [])
    if high_corr:
        lines.append(f"\nHigh-Correlation Pairs (|r|>0.70):")
        for p in high_corr[:5]:
            lines.append(f"  {p['sym1']} ↔ {p['sym2']}: {p['corr']:+.2f}")

    sectors = d.get("sector_map", {})
    if sectors:
        from collections import Counter
        sector_counts = Counter(sectors.values())
        dominant = [(s, c) for s, c in sector_counts.items() if c >= 2]
        if dominant:
            lines.append("\nSector Concentration:")
            for sec, cnt in dominant:
                lines.append(f"  {sec}: {cnt} positions")

    best  = d.get("best_performer")
    worst = d.get("worst_performer")
    if best and worst:
        lines.append(f"\nBest 1Y: {best[0]} ({best[1]*100:+.1f}%)")
        lines.append(f"Worst 1Y: {worst[0]} ({worst[1]*100:+.1f}%)")

    return "\n".join(lines)


def _extract_verdict(analysis: str) -> str:
    text = analysis.upper()
    if "HIGH_RISK" in text or "HIGH RISK" in text:
        return "HIGH_RISK"
    if "NEEDS_ATTENTION" in text or "NEEDS ATTENTION" in text:
        return "NEEDS_ATTENTION"
    if "HEALTHY" in text:
        return "HEALTHY"
    return "NEEDS_ATTENTION"


def _verdict_to_signal(verdict: str) -> str:
    return {"HEALTHY": "HOLD", "NEEDS_ATTENTION": "SELL", "HIGH_RISK": "SELL"}.get(verdict, "HOLD")


def _estimate_confidence(d: Dict) -> float:
    base = 0.60
    n    = len(d.get("valid_symbols", []))
    if n >= 5:
        base += 0.05
    if d.get("div_ratio", 1) < 1.1:
        base += 0.05
    return min(round(base, 2), 0.75)


def _build_key_points(d: Dict, verdict: str) -> List[str]:
    pts = []
    n   = len(d.get("valid_symbols", []))
    pts.append(f"{n} 个标的，等权配置")

    port_vol = d.get("port_vol_ann", 0)
    if port_vol:
        risk_lv = "高风险" if port_vol > 0.30 else ("中等" if port_vol > 0.18 else "低风险")
        pts.append(f"组合年化波动 {port_vol*100:.1f}%（{risk_lv}）")

    div_r = d.get("div_ratio", 1)
    if div_r < 1.1:
        pts.append(f"分散度不足（ratio {div_r:.2f}x，标的相关性高）")
    else:
        pts.append(f"分散度合理（ratio {div_r:.2f}x）")

    high_corr = d.get("high_corr", [])
    if high_corr:
        top = high_corr[0]
        pts.append(f"最高相关对: {top['sym1']}↔{top['sym2']} ({top['corr']:+.2f})")

    pts.append(f"整体评级: {verdict}")
    return pts[:6]


def _template_analysis(symbols: List[str], d: Dict) -> str:
    high_corr = d.get("high_corr", [])
    port_vol  = d.get("port_vol_ann", 0)
    div_r     = d.get("div_ratio", 1)

    verdict = "NEEDS_ATTENTION"
    if not high_corr and port_vol < 0.20 and div_r >= 1.2:
        verdict = "HEALTHY"
    elif len(high_corr) >= 3 or port_vol > 0.30:
        verdict = "HIGH_RISK"

    lines = [f"{','.join(symbols)} 组合分析报告（模板）"]
    lines.append(f"年化波动率: {port_vol*100:.1f}%")
    lines.append(f"分散度系数: {div_r:.2f}x")
    if high_corr:
        lines.append(f"高相关对 {len(high_corr)} 组，最高: "
                     f"{high_corr[0]['sym1']}↔{high_corr[0]['sym2']} {high_corr[0]['corr']:+.2f}")
    else:
        lines.append("无显著高相关对，分散良好。")
    lines.append(f"\n{verdict}")
    return "\n".join(lines)
