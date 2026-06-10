"""
agents/realty/ops_optimize.py — 运营提升 Agent
===============================================
分析坪效、客流、商品排行、活动效果，提出经营优化建议。

输入数据（data dict keys）:
    project_info    — 项目基本信息（面积/业态/开业时间）
    performance_data — 经营绩效数据（坪效/客流/流水趋势/商品排行）
    marketing_data  — 营销活动数据（会员/优惠券/外卖/团购）
    peer_benchmarks — 同业基准数据（可选）

输出:
    analysis    — 运营优化建议
    signal      — BUY=经营健康/高潜力 / HOLD=有改善空间 / SELL=经营不佳需干预
    key_points  — Top3 优化建议
"""
from __future__ import annotations

from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult


class OpsOptimizeAgent(BaseAgent):
    name        = "ops_optimize"
    description = "运营提升：分析坪效/客流/营销效果，提出招商补位和经营优化建议"

    _SYSTEM = (
        "你是一名资深的商业空间运营顾问，专注于帮助经营方提升业绩。\n"
        "请基于以下经营数据给出针对性的优化建议：\n"
        "  1. 坪效分析（当前坪效 vs 行业基准，提升空间）\n"
        "  2. 客流优化（高峰时段利用率、低谷期激活策略）\n"
        "  3. 商品/品类优化（畅销/滞销商品，品类结构调整）\n"
        "  4. 营销活动效果评估（会员召回、优惠券核销率、外卖占比）\n"
        "  5. 营业时间优化建议\n"
        "  6. 招商补位建议（空置面积是否可引入互补业态）\n"
        "  7. 优先级最高的3项具体可执行建议（含预期效果）"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        project  = data.get("project_info", {})
        perf     = data.get("performance_data", {})
        marketing= data.get("marketing_data", {})
        bench    = data.get("peer_benchmarks", {})

        metrics = _calc_metrics(project, perf, marketing, bench)

        user_prompt = (
            f"项目信息：{project.get('name','未命名')} "
            f"({project.get('area',0)}m²  {project.get('business_type','未知业态')})\n"
            f"开业: {project.get('open_date','未知')}\n\n"
            f"经营绩效：\n"
            f"  月均流水: {perf.get('monthly_revenue',0):,.2f}元\n"
            f"  坪效: {metrics['revenue_per_sqm']:.1f}元/m²/月  行业基准: {bench.get('revenue_per_sqm',300):.1f}\n"
            f"  日均客流: {perf.get('daily_visits',0):.0f}人次  转化率: {metrics['conversion_rate']:.1%}\n"
            f"  会员复购率: {marketing.get('member_repurchase_pct',0):.1f}%\n"
            f"  外卖收入占比: {metrics['delivery_pct']:.1f}%\n"
            f"  优惠券核销率: {marketing.get('coupon_redemption_pct',0):.1f}%\n\n"
            f"Top10商品: {perf.get('top_products', [])[:5]}\n"
            f"垫底商品: {perf.get('bottom_products', [])[:3]}\n\n"
            "请给出运营优化建议。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=700)
        if not analysis:
            analysis = _template_ops(metrics, perf, marketing, bench)

        signal     = _ops_signal(metrics, bench)
        confidence = _ops_confidence(perf, marketing)
        key_points = _ops_key_points(metrics, perf, marketing, bench)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"metrics": metrics},
        )


# ── 辅助逻辑 ──────────────────────────────────────────────────────────────────

def _calc_metrics(project: Dict, perf: Dict, marketing: Dict, bench: Dict) -> Dict:
    area     = project.get("area", 1)
    revenue  = perf.get("monthly_revenue", 0)
    visits   = perf.get("daily_visits", 0) * 30
    orders   = perf.get("monthly_orders", 0)
    delivery = marketing.get("delivery_revenue", 0)

    return {
        "revenue_per_sqm":  revenue / area if area else 0,
        "conversion_rate":  orders / visits if visits else 0,
        "delivery_pct":     delivery / revenue * 100 if revenue else 0,
        "bench_psm":        bench.get("revenue_per_sqm", 300),
        "psm_gap_pct":      (revenue / area - bench.get("revenue_per_sqm", 300))
                            / bench.get("revenue_per_sqm", 300) * 100 if area else 0,
    }


def _ops_signal(metrics: Dict, bench: Dict) -> str:
    psm = metrics.get("revenue_per_sqm", 0)
    bench_psm = metrics.get("bench_psm", 300)
    if bench_psm <= 0: return "HOLD"
    ratio = psm / bench_psm
    if ratio >= 1.2: return "BUY"
    if ratio >= 0.8: return "HOLD"
    if ratio >= 0.5: return "SELL"
    return "STRONG_SELL"


def _ops_confidence(perf: Dict, marketing: Dict) -> float:
    has = sum([
        1 if perf.get("monthly_revenue") else 0,
        1 if perf.get("daily_visits")    else 0,
        1 if marketing.get("member_count") else 0,
        1 if perf.get("top_products")    else 0,
    ])
    return round(0.5 + 0.1 * has, 2)


def _ops_key_points(metrics: Dict, perf: Dict, marketing: Dict, bench: Dict) -> List[str]:
    pts = []
    psm = metrics.get("revenue_per_sqm", 0)
    bench_psm = metrics.get("bench_psm", 300)

    pts.append(f"坪效: {psm:.1f}元/m²/月  行业基准: {bench_psm:.1f}  差距: {metrics['psm_gap_pct']:+.1f}%")
    pts.append(f"转化率: {metrics['conversion_rate']:.1%}  外卖占比: {metrics['delivery_pct']:.1f}%")

    if psm < bench_psm * 0.7:
        pts.append("坪效显著低于基准，建议重新评估业态或开展营销活动")
    if marketing.get("coupon_redemption_pct", 50) < 20:
        pts.append("优惠券核销率低，营销触达效果需改善")
    if marketing.get("member_repurchase_pct", 0) < 30:
        pts.append("会员复购率偏低，建议加强会员运营和精准推送")
    return pts[:5]


def _template_ops(metrics: Dict, perf: Dict, marketing: Dict, bench: Dict) -> str:
    psm = metrics.get("revenue_per_sqm", 0)
    bench_psm = metrics.get("bench_psm", 300)
    gap = metrics.get("psm_gap_pct", 0)

    recs = []
    if psm < bench_psm:
        recs.append("1. 引入高客单价品类或体验型业态，提升坪效")
    if metrics.get("delivery_pct", 0) < 20:
        recs.append("2. 开通美团/饿了么外卖渠道，提高营业时段覆盖")
    if marketing.get("member_count", 0) < 500:
        recs.append("3. 启动会员招募计划，目标500+活跃会员")
    recs.append("4. 分析低峰时段，设计限时优惠活动激活非高峰客流")

    return (
        f"运营提升建议（模板）：\n"
        f"  坪效: {psm:.1f}元/m²/月（{'+' if gap >= 0 else ''}{gap:.1f}% vs 行业）\n"
        f"  转化率: {metrics['conversion_rate']:.1%}  外卖占比: {metrics['delivery_pct']:.1f}%\n\n"
        f"优化建议：\n"
        + "\n".join(f"  {r}" for r in recs)
    )
