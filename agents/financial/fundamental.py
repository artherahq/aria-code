"""
agents/financial/fundamental.py — 基本面分析 Agent
====================================================
分析：PE/PB/ROE、营收增速、竞争壁垒、估值水位。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from ..base import BaseAgent, AgentResult


class FundamentalAgent(BaseAgent):
    name        = "fundamental"
    description = "基本面分析：估值/ROE/竞争优势/财务健康"

    _SYSTEM = (
        "You are a fundamental equity analyst. Evaluate: PE/PB valuation levels "
        "(vs historical range and sector peers), ROE quality, revenue growth, "
        "balance sheet health, and competitive moat. "
        "Be data-driven. End with: UNDERVALUED / FAIRLY_VALUED / OVERVALUED."
    )

    async def fetch_data(self, symbol: str) -> Dict[str, Any]:
        data = await super().fetch_data(symbol)
        if self.data:
            try:
                f = self.data.fundamentals(symbol)
                if f:
                    data["fundamentals"] = {
                        "pe_ttm":          f.pe_ttm,
                        "pb":              f.pb,
                        "roe":             f.roe,
                        "revenue_growth":  f.revenue_growth,
                        "dividend_yield":  f.dividend_yield,
                        "source":          f.source,
                    }
            except Exception:
                pass
        return data

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        quote = data.get("quote", {})
        fund  = data.get("fundamentals", {})
        price = quote.get("price", 0)
        pe    = fund.get("pe_ttm") or quote.get("pe_ttm", 0)
        pb    = fund.get("pb")     or quote.get("pb",     0)
        roe   = fund.get("roe",    0)
        rev_g = fund.get("revenue_growth", 0)
        div_y = fund.get("dividend_yield", 0)

        fund_str = (
            f"  PE(TTM): {pe:.1f}x\n"
            f"  PB:      {pb:.2f}x\n"
            f"  ROE:     {roe:.1f}%\n"
            f"  Revenue growth: {rev_g:.1f}%\n"
            f"  Dividend yield: {div_y:.2f}%"
        ) if pe or pb else "  (fundamental data unavailable)"

        prompt = (
            f"Stock: {symbol}  Price: {price}\n"
            f"Fundamentals:\n{fund_str}\n\n"
            "Evaluate:\n"
            "1. Valuation (PE/PB vs historical/sector)\n"
            "2. Profitability quality (ROE trend)\n"
            "3. Growth outlook (revenue/earnings)\n"
            "4. Balance sheet and dividend\n"
            "5. Competitive moat\n"
            "Conclusion: UNDERVALUED / FAIRLY_VALUED / OVERVALUED"
        )

        analysis  = await self._call_llm(self._SYSTEM, prompt, max_tokens=500)
        if not analysis:
            analysis = _template_fundamental(symbol, pe, pb, roe, rev_g)

        signal     = _extract_signal(analysis, pe)
        confidence = _calc_confidence(pe, pb, roe)
        key_points = _extract_key_points(analysis)

        return AgentResult(
            agent=self.name, symbol=symbol,
            analysis=analysis, confidence=confidence,
            signal=signal, key_points=key_points,
            data_used={"pe": pe, "pb": pb, "roe": roe},
        )


def _extract_signal(text: str, pe: float = 0) -> str:
    t = text.upper()
    if "UNDERVALUED" in t or "低估" in t:   return "BUY"
    if "OVERVALUED"  in t or "高估" in t:   return "SELL"
    # PE 辅助判断
    if pe > 0:
        if pe < 15:   return "BUY"
        if pe > 50:   return "SELL"
    return "HOLD"


def _calc_confidence(pe: float, pb: float, roe: float) -> float:
    if pe <= 0 and pb <= 0 and roe == 0:
        return 0.4   # 无数据，低置信度
    score = 0.5
    if pe > 0:
        if   pe < 15: score += 0.15
        elif pe < 25: score += 0.05
        elif pe > 50: score -= 0.15
    if roe > 20: score += 0.15
    elif roe > 10: score += 0.05
    return round(min(0.95, max(0.2, score)), 2)


def _extract_key_points(text: str) -> List[str]:
    points = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith(("1.", "2.", "3.", "4.", "5.", "•", "-", "·")) and len(line) > 5:
            points.append(line.lstrip("1234567890.-•· "))
    return points[:4]


def _template_fundamental(symbol: str, pe: float, pb: float,
                           roe: float, rev_g: float) -> str:
    valuation = "低估" if pe and pe < 15 else ("高估" if pe and pe > 40 else "合理")
    return (
        f"{symbol} 基本面分析（模板）:\n"
        f"• 估值：PE={pe:.0f}x  PB={pb:.1f}x  → {valuation}\n"
        f"• 盈利能力：ROE={roe:.1f}%  营收增速={rev_g:.1f}%\n"
        "• 建议结合行业对比和管理层指引综合判断\n"
        f"• 结论: {'UNDERVALUED' if pe and pe<15 else 'FAIRLY_VALUED'}"
    )
