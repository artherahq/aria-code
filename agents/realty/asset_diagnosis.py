"""
agents/realty/asset_diagnosis.py — 资产诊断 Agent
==================================================
判断资产适合出租、出售还是经营权共创。

输入数据（data dict keys）:
    asset_info       — 资产基础信息 dict（面积、位置、产权状态、空置时间等）
    market_context   — 周边业态、租金行情（可选）
    renovation_cost  — 估算改造成本（可选）

输出:
    analysis    — 详细分析文本
    signal      — BUY=推荐共创 / HOLD=需观察 / SELL=建议出租或出售
    key_points  — 3-5 条关键结论
"""
from __future__ import annotations

from typing import Any, Dict
from ..base import BaseAgent, AgentResult


class AssetDiagnosisAgent(BaseAgent):
    name        = "asset_diagnosis"
    description = "资产诊断：判断处置方式（出租/出售/共创）并给出保底与分润结构建议"

    _SYSTEM = (
        "你是一名专业的不动产资产运营顾问，擅长评估商业空间的经营潜力。\n"
        "你的任务是根据资产条件、空置状况、市场行情，判断该资产最适合哪种处置方式：\n"
        "  A) 传统出租（固定租金）\n"
        "  B) 经营权共创（保底+流水分润）\n"
        "  C) 出售（一次性变现）\n\n"
        "输出格式要求：\n"
        "1. 推荐方式及理由（2-3句话）\n"
        "2. 如推荐共创，给出：保底收益建议（元/月）、分润比例建议（%）、适合业态Top3\n"
        "3. 主要风险提示（1-2条）\n"
        "4. 综合评分：[适合共创] / [建议观察] / [不建议共创]"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        asset   = data.get("asset_info", {})
        market  = data.get("market_context", {})
        reno    = data.get("renovation_cost", 0)

        # 构建资产摘要
        area        = asset.get("area", 0)
        location    = asset.get("location", "未知位置")
        vacancy_days= asset.get("vacancy_days", 0)
        expected_rent = asset.get("expected_rent", 0)
        floor_height  = asset.get("floor_height", 0)
        business_types = asset.get("allowed_business", [])
        prohibited    = asset.get("prohibited_business", [])
        property_state= asset.get("property_state", "正常")

        user_prompt = (
            f"资产信息：\n"
            f"  位置: {location}\n"
            f"  面积: {area} m²  层高: {floor_height}m\n"
            f"  产权状态: {property_state}  空置天数: {vacancy_days}天\n"
            f"  评估租金: {expected_rent}元/月  改造成本估算: {reno}元\n"
            f"  适合业态: {', '.join(business_types) or '未指定'}\n"
            f"  禁止业态: {', '.join(prohibited) or '无'}\n"
            f"  市场背景: {market.get('summary', '暂无周边数据')}\n\n"
            "请根据以上信息完成资产诊断。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=600)
        if not analysis:
            analysis = _template_diagnosis(asset, reno)

        signal      = _score_to_signal(asset, reno)
        confidence  = _calc_confidence(asset)
        key_points  = _extract_key_points(asset, reno, market)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"asset_info": asset, "renovation_cost": reno},
        )


# ── 内部辅助函数 ──────────────────────────────────────────────────────────────

def _score_to_signal(asset: Dict, reno: float) -> str:
    """根据资产条件估算共创适合度"""
    area         = asset.get("area", 0)
    vacancy_days = asset.get("vacancy_days", 0)
    rent         = asset.get("expected_rent", 0)
    allowed      = asset.get("allowed_business", [])
    state        = asset.get("property_state", "正常")

    score = 0
    if area >= 50:      score += 1
    if area >= 200:     score += 1
    if vacancy_days >= 90:  score += 1   # 空置越久越需要共创
    if rent > 0:        score += 1
    if len(allowed) >= 2: score += 1
    if state != "正常":   score -= 2
    if reno > rent * 24:  score -= 1     # 改造成本超过2年租金则不划算

    if score >= 4:  return "BUY"       # 非常适合共创
    if score >= 2:  return "HOLD"      # 需进一步评估
    return "SELL"                       # 建议传统出租或出售


def _calc_confidence(asset: Dict) -> float:
    """数据越完整，置信度越高"""
    fields = ["area", "location", "expected_rent", "allowed_business", "property_state"]
    filled = sum(1 for f in fields if asset.get(f))
    return round(0.5 + 0.1 * filled, 2)


def _extract_key_points(asset: Dict, reno: float, market: Dict) -> list:
    points = []
    area = asset.get("area", 0)
    vacancy = asset.get("vacancy_days", 0)
    rent = asset.get("expected_rent", 0)
    allowed = asset.get("allowed_business", [])

    if area >= 200:
        points.append(f"面积 {area}m² 具备多业态组合潜力")
    if vacancy >= 90:
        points.append(f"已空置 {vacancy} 天，引入经营方具有紧迫性")
    if rent > 0:
        points.append(f"评估租金 {rent:,.0f} 元/月，保底基准参考值")
    if allowed:
        points.append(f"适合业态：{', '.join(allowed[:3])}")
    if reno > 0:
        points.append(f"改造成本约 {reno:,.0f} 元，需纳入分润谈判")
    return points[:5]


def _template_diagnosis(asset: Dict, reno: float) -> str:
    area    = asset.get("area", 0)
    vacancy = asset.get("vacancy_days", 0)
    rent    = asset.get("expected_rent", 0)
    allowed = asset.get("allowed_business", [])

    suitability = "建议经营权共创" if area >= 100 and vacancy >= 30 else "建议先评估市场需求"
    return (
        f"资产诊断（模板）：\n"
        f"  面积: {area}m²  空置: {vacancy}天  评估租金: {rent:,.0f}元/月\n"
        f"  适合业态: {', '.join(allowed[:3]) or '待评估'}\n"
        f"  改造成本估算: {reno:,.0f}元\n"
        f"  诊断结论: {suitability}\n"
        f"  建议保底: {int(rent * 0.7):,}元/月  建议分润比例: 8-12%"
    )
