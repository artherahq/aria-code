"""
agents/realty/business_match.py — 业态匹配 Agent
=================================================
匹配资产条件与经营方能力，给出业态组合建议。

输入数据（data dict keys）:
    asset_info      — 资产基础信息（面积、位置、层高、客流等）
    operators       — 候选经营方列表（可选）
    market_context  — 周边业态、人口、消费力数据（可选）

输出:
    analysis    — 业态匹配建议
    signal      — BUY=高度匹配 / HOLD=一般匹配 / SELL=低匹配
    key_points  — 推荐业态 Top3 + 理由
"""
from __future__ import annotations

from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult


class BusinessMatchAgent(BaseAgent):
    name        = "business_match"
    description = "业态匹配：根据空间条件和市场数据推荐适合的经营业态组合"

    _SYSTEM = (
        "你是一名商业空间招商运营专家，擅长根据空间条件匹配最优业态。\n"
        "分析维度：\n"
        "  1. 空间适配度（面积、层高、水电、消防条件）\n"
        "  2. 市场需求（周边客群、竞争格局、消费力）\n"
        "  3. 经营方能力（品牌力、资金实力、运营经验）\n"
        "  4. 收益预期（流水规模、分润可行性）\n\n"
        "输出格式：\n"
        "1. 推荐业态 Top3（每个包含：业态名称、预期月流水范围、分润比例建议、空间需求匹配度）\n"
        "2. 经营方匹配建议（如有候选方）\n"
        "3. 不建议的业态及原因\n"
        "4. 综合匹配评级：[高度匹配] / [一般匹配] / [谨慎入场]"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        asset     = data.get("asset_info", {})
        operators = data.get("operators", [])
        market    = data.get("market_context", {})

        area        = asset.get("area", 0)
        location    = asset.get("location", "")
        floor_ht    = asset.get("floor_height", 0)
        power_cap   = asset.get("power_capacity", "未知")
        allowed     = asset.get("allowed_business", [])
        prohibited  = asset.get("prohibited_business", [])
        foot_traffic= asset.get("foot_traffic", "未知")
        can_fire    = asset.get("open_fire_allowed", False)
        can_renovate= asset.get("renovation_allowed", True)

        op_summary = ""
        if operators:
            op_lines = []
            for op in operators[:3]:
                op_lines.append(
                    f"  - {op.get('name','未知')}: 行业={op.get('industry','未知')}, "
                    f"预算={op.get('budget',0):,}元, 品牌={op.get('brand_level','未知')}"
                )
            op_summary = "候选经营方：\n" + "\n".join(op_lines)

        user_prompt = (
            f"资产条件：\n"
            f"  位置: {location}  面积: {area}m²  层高: {floor_ht}m\n"
            f"  电容量: {power_cap}  明火许可: {'是' if can_fire else '否'}\n"
            f"  可改造: {'是' if can_renovate else '否'}  客流量: {foot_traffic}\n"
            f"  允许业态: {', '.join(allowed) or '无限制'}\n"
            f"  禁止业态: {', '.join(prohibited) or '无'}\n"
            f"  市场背景: {market.get('summary','暂无')}\n"
            f"{op_summary}\n\n"
            "请给出业态匹配分析。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=700)
        if not analysis:
            analysis = _template_match(asset, operators)

        signal     = _match_signal(asset, market)
        confidence = _match_confidence(asset, market)
        key_points = _match_key_points(asset, operators, market)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"asset_info": asset, "operators_count": len(operators)},
        )


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _match_signal(asset: Dict, market: Dict) -> str:
    score = 0
    area  = asset.get("area", 0)
    allowed = asset.get("allowed_business", [])
    foot_traffic = str(asset.get("foot_traffic", "")).lower()
    ft_score = asset.get("foot_traffic_score", 0)   # numeric 0-10

    # market_score: direct int, or derive from market_maturity string
    market_score = market.get("score", 0)
    if not market_score:
        maturity = str(market.get("market_maturity", "")).lower()
        market_score = {"high": 8, "medium": 5, "low": 2, "成熟": 8, "一般": 5, "新兴": 4}.get(maturity, 0)

    if area >= 100:   score += 1
    if area >= 300:   score += 1
    if len(allowed) >= 3: score += 1
    if "高" in foot_traffic or "large" in foot_traffic or ft_score >= 7: score += 1
    if market_score >= 7:  score += 1
    elif 0 < market_score <= 3: score -= 1

    if score >= 4:  return "BUY"
    if score >= 2:  return "HOLD"
    return "SELL"


def _match_confidence(asset: Dict, market: Dict) -> float:
    fields = ["area", "location", "foot_traffic", "allowed_business", "floor_height"]
    filled = sum(1 for f in fields if asset.get(f))
    market_bonus = 0.1 if market.get("summary") else 0
    return round(min(0.9, 0.4 + 0.1 * filled + market_bonus), 2)


def _match_key_points(asset: Dict, operators: List, market: Dict) -> List[str]:
    points = []
    area    = asset.get("area", 0)
    allowed = asset.get("allowed_business", [])
    can_fire= asset.get("open_fire_allowed", False)

    if allowed:
        points.append(f"允许业态：{', '.join(allowed[:3])} 等 {len(allowed)} 类")
    if area:
        points.append(f"面积 {area}m²，适合{'大型品牌' if area >= 300 else '中小型经营方'}")
    if not can_fire:
        points.append("不允许明火，餐饮业态受限（建议西餐/轻食/烘焙）")
    if operators:
        points.append(f"共 {len(operators)} 个候选经营方，需进一步资质评估")
    if market.get("competitors"):
        points.append(f"周边竞争: {market['competitors']}")
    return points[:5]


def _template_match(asset: Dict, operators: List) -> str:
    area    = asset.get("area", 0)
    allowed = asset.get("allowed_business", [])
    can_fire= asset.get("open_fire_allowed", False)

    top3 = allowed[:3] if allowed else ["零售", "轻餐饮", "生活服务"]
    if not can_fire and "餐饮" in top3:
        top3 = [b for b in top3 if "餐饮" not in b] + ["轻食/咖啡"]

    return (
        f"业态匹配分析（模板）：\n"
        f"  面积: {area}m²  适合业态: {', '.join(allowed[:5]) or '无限制'}\n"
        f"  推荐 Top3:\n"
        + "\n".join(f"    {i+1}. {b}（预期流水 {(area*200*(i+1)):,.0f}-{area*400*(i+1):,.0f}元/月）"
                    for i, b in enumerate(top3[:3]))
        + f"\n  候选经营方数: {len(operators)}"
    )
