"""
agents/financial/risk.py — 风险评估 Agent
==========================================
分析：波动率、最大回撤、与大盘相关性、仓位建议。
"""
from __future__ import annotations
from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult

try:
    import numpy as np
    _HAS_NP = True
except ImportError:
    _HAS_NP = False
    np = None  # type: ignore[assignment]


class RiskAgent(BaseAgent):
    name        = "risk"
    description = "风险评估：波动率/回撤/相关性/仓位建议"

    _SYSTEM = (
        "You are a risk manager. Assess: historical volatility, max drawdown, "
        "liquidity risk, correlation to market, and position sizing recommendation. "
        "Output: risk score 1-10 (1=very low, 10=very high) and "
        "POSITION: <percentage> (e.g. POSITION: 10%)."
    )

    async def fetch_data(self, symbol: str) -> Dict[str, Any]:
        data = await super().fetch_data(symbol)
        if self.data:
            h = self.data.history(symbol, days=252)  # 1年
            if h and h.data is not None:
                data["risk_metrics"] = _compute_risk(h.data)
        return data

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        quote   = data.get("quote", {})
        metrics = data.get("risk_metrics", {})
        price   = quote.get("price", 0)

        risk_str = (
            f"  Ann. Volatility: {metrics.get('ann_vol',0):.1f}%\n"
            f"  Max Drawdown:    {metrics.get('max_dd',0):.1f}%\n"
            f"  Sharpe (1Y):     {metrics.get('sharpe',0):.2f}\n"
            f"  Beta est.:       {metrics.get('beta',1.0):.2f}\n"
            f"  Avg Daily Vol:   {metrics.get('avg_vol_usd',0):,.0f}"
        ) if metrics else "  (risk metrics unavailable)"

        prompt = (
            f"Stock: {symbol}  Price: {price}\n"
            f"Risk Metrics:\n{risk_str}\n\n"
            "Assess:\n"
            "1. Volatility profile (high/medium/low)\n"
            "2. Drawdown history and recovery\n"
            "3. Liquidity adequacy\n"
            "4. Tail risk and black swan exposure\n"
            "5. Recommended position size for a diversified portfolio\n"
            "Output format: Risk Score: X/10\nPOSITION: Y%"
        )

        analysis  = await self._call_llm(self._SYSTEM, prompt, max_tokens=400)
        if not analysis:
            analysis = _template_risk(symbol, metrics)

        signal     = _risk_to_signal(metrics)
        confidence = 0.70
        key_points = [
            f"年化波动率 {metrics.get('ann_vol',0):.1f}%",
            f"最大回撤 {metrics.get('max_dd',0):.1f}%",
            f"夏普比率 {metrics.get('sharpe',0):.2f}",
        ] if metrics else ["风险数据不可用"]

        return AgentResult(
            agent=self.name, symbol=symbol,
            analysis=analysis, confidence=confidence,
            signal=signal, key_points=key_points,
            data_used=metrics,
        )


def _compute_risk(df) -> Dict:
    if not _HAS_NP:
        return {}
    try:
        close = df["close"].values if "close" in df.columns else df.iloc[:, -1].values
        vol   = df["volume"].values if "volume" in df.columns else np.ones(len(close))
        rets  = np.diff(close) / close[:-1]

        ann_vol   = float(np.std(rets) * np.sqrt(252) * 100)
        ann_ret   = float(np.mean(rets) * 252 * 100)
        sharpe    = ann_ret / max(ann_vol, 0.01)
        cum       = np.cumprod(1 + rets)
        peaks     = np.maximum.accumulate(cum)
        drawdowns = (cum - peaks) / peaks
        max_dd    = float(drawdowns.min() * 100)
        avg_vol   = float(np.mean(vol[-20:]) * close[-1]) if len(close) > 20 else 0

        return {
            "ann_vol":    round(ann_vol, 1),
            "ann_ret":    round(ann_ret, 1),
            "sharpe":     round(sharpe, 2),
            "max_dd":     round(max_dd, 1),
            "beta":       1.0,      # 需要指数数据对比
            "avg_vol_usd": round(avg_vol, 0),
        }
    except Exception:
        return {}


def _risk_to_signal(metrics: Dict) -> str:
    vol = metrics.get("ann_vol", 30)
    dd  = metrics.get("max_dd", -20)
    if vol < 20 and dd > -15:
        return "BUY"
    if vol > 50 or dd < -40:
        return "REDUCE"
    return "HOLD"


def _template_risk(symbol: str, m: Dict) -> str:
    vol = m.get("ann_vol", 0)
    dd  = m.get("max_dd",  0)
    risk_level = "高" if vol > 40 else ("中" if vol > 20 else "低")
    return (
        f"{symbol} 风险分析（模板）:\n"
        f"• 年化波动率: {vol:.1f}%（风险水平: {risk_level}）\n"
        f"• 最大回撤: {dd:.1f}%\n"
        f"• 夏普比率: {m.get('sharpe',0):.2f}\n"
        f"• 建议仓位: {'5-10%' if vol>40 else '10-15%' if vol>20 else '15-20%'}\n"
        f"• Risk Score: {min(10, int(vol/5))}/10"
    )
