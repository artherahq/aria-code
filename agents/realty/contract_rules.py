"""
agents/realty/contract_rules.py — 合同规则 Agent
=================================================
将业务谈判结果转化为合同条款和分账规则草案。

输入数据（data dict keys）:
    negotiation     — 谈判结果摘要（保底/分润比例/结算周期/退出条件等）
    asset_info      — 资产基础信息
    operator_info   — 经营方信息（资质/历史数据等）

输出:
    analysis    — 合同条款草案文本
    signal      — BUY=条款清晰可执行 / HOLD=需补充条款 / SELL=有重大风险条款
    key_points  — 关键条款摘要（保底金额/分润比例/结算周期/违约条款）
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from ..base import BaseAgent, AgentResult


class ContractRulesAgent(BaseAgent):
    name        = "contract_rules"
    description = "合同规则生成：将谈判结果结构化为可执行的合同条款和分账规则草案"

    _SYSTEM = (
        "你是一名专业的不动产运营合同起草顾问。\n"
        "你的任务是根据谈判结果，生成结构化的合同条款草案，包含：\n"
        "  1. 经营权共创模式说明（资产方不转让产权，基于使用权获得收益）\n"
        "  2. 收益分配规则：保底收益、流水分润计算方式、阶梯分成规则\n"
        "  3. 结算周期与结算方式\n"
        "  4. 保证金条款（金额、用途、退还条件）\n"
        "  5. 风险准备金（比例、用途）\n"
        "  6. 平台服务费（比例或固定金额）\n"
        "  7. 退出条款（提前退出违约金、清算顺序）\n"
        "  8. 开业义务与履约要求\n\n"
        "输出要求：\n"
        "- 每个条款必须包含：条款名称、具体数值、计算公式（如适用）\n"
        "- 标注 [系统可执行] / [需人工确认] / [建议律师审核]\n"
        "- 最后给出：合同风险评级 [低/中/高] 及主要风险点"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        nego     = data.get("negotiation", {})
        asset    = data.get("asset_info", {})
        operator = data.get("operator_info", {})

        # 解析谈判关键数值（兼容多种字段命名）
        guaranteed    = nego.get("guaranteed_amount",
                                 nego.get("guaranteed_monthly", 0))
        revenue_share = nego.get("revenue_share_pct", 0)
        base_revenue  = nego.get("base_revenue",
                                 nego.get("revenue_share_base", 0))
        tiered_rules  = nego.get("tiered_rules", [])
        settle_cycle  = nego.get("settlement_cycle", "monthly")
        # 保证金：直接金额 > deposit_months × 保底 > 0
        deposit_months = nego.get("deposit_months", 0)
        deposit = nego.get("deposit_amount",
                           nego.get("deposit",
                                    guaranteed * deposit_months if deposit_months else 0))
        risk_reserve  = nego.get("risk_reserve_pct", 3)
        platform_fee  = nego.get("platform_fee_pct", 5)
        contract_years= nego.get("contract_years", 1)
        exit_penalty  = nego.get("exit_penalty_months", 3)

        user_prompt = (
            f"谈判结果：\n"
            f"  保底收益: {guaranteed:,}元/月\n"
            f"  流水分润: 月流水超过{base_revenue:,}元后按{revenue_share}%分润\n"
            f"  阶梯规则: {tiered_rules or '无阶梯'}\n"
            f"  结算周期: {settle_cycle}\n"
            f"  保证金: {deposit:,}元\n"
            f"  风险准备金: 流水的{risk_reserve}%\n"
            f"  平台服务费: 流水的{platform_fee}%\n"
            f"  合同年限: {contract_years}年\n"
            f"  提前退出违约: {exit_penalty}个月保底金额\n\n"
            f"资产信息: {asset.get('name','未命名')} ({asset.get('area',0)}m²)\n"
            f"经营方: {operator.get('name','未知')} "
            f"(行业: {operator.get('industry','未知')})\n\n"
            "请生成完整的合同条款草案。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=1000)
        if not analysis:
            analysis = _template_contract(nego, asset, operator)

        # 结构化规则供系统执行
        structured = _build_structured_rules(nego)
        signal     = _contract_signal(nego)
        confidence = _contract_confidence(nego)
        key_points = _contract_key_points(nego, structured)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"structured_rules": structured, "negotiation": nego},
        )


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _build_structured_rules(nego: Dict) -> Dict:
    """生成系统可直接执行的分账规则 JSON（兼容多种字段命名）"""
    _g             = nego.get("guaranteed_amount", nego.get("guaranteed_monthly", 0))
    guaranteed     = Decimal(str(_g))
    share_pct      = Decimal(str(nego.get("revenue_share_pct", 0)))
    base_revenue   = Decimal(str(nego.get("base_revenue", nego.get("revenue_share_base", 0))))
    risk_reserve   = Decimal(str(nego.get("risk_reserve_pct", 3)))
    platform_fee   = Decimal(str(nego.get("platform_fee_pct", 5)))
    _dm            = nego.get("deposit_months", 0)
    deposit        = Decimal(str(nego.get("deposit_amount",
                                          nego.get("deposit",
                                                   float(_g) * _dm if _dm else 0))))

    return {
        "guaranteed_monthly":   float(guaranteed),
        "revenue_share_pct":    float(share_pct),
        "revenue_share_base":   float(base_revenue),
        "risk_reserve_pct":     float(risk_reserve),
        "platform_fee_pct":     float(platform_fee),
        "deposit_amount":       float(deposit),
        "settlement_cycle":     nego.get("settlement_cycle", "monthly"),
        "tiered_rules":         nego.get("tiered_rules", []),
        "contract_years":       nego.get("contract_years", 1),
        "exit_penalty_months":  nego.get("exit_penalty_months", 3),
    }


def _contract_signal(nego: Dict) -> str:
    issues = 0
    if not (nego.get("guaranteed_amount") or nego.get("guaranteed_monthly")): issues += 1
    if not nego.get("revenue_share_pct"):  issues += 1
    if not (nego.get("deposit") or nego.get("deposit_amount") or nego.get("deposit_months")): issues += 1
    if not nego.get("settlement_cycle"):   issues += 1
    if not nego.get("contract_years"):     issues += 1

    if issues == 0:  return "BUY"
    if issues <= 2:  return "HOLD"
    return "SELL"


def _contract_confidence(nego: Dict) -> float:
    keys_pairs = [
        ("guaranteed_amount", "guaranteed_monthly"),
        ("revenue_share_pct",),
        ("base_revenue", "revenue_share_base"),
        ("deposit", "deposit_amount", "deposit_months"),
        ("settlement_cycle",),
        ("contract_years",),
    ]
    filled = sum(1 for kp in keys_pairs if any(nego.get(k) for k in kp))
    return round(0.4 + 0.1 * filled, 2)


def _contract_key_points(nego: Dict, rules: Dict) -> List[str]:
    pts = []
    g = rules.get("guaranteed_monthly", 0)
    s = rules.get("revenue_share_pct", 0)
    b = rules.get("revenue_share_base", 0)
    d = rules.get("deposit_amount", 0)
    c = rules.get("settlement_cycle", "monthly")
    yrs = rules.get("contract_years", 1)

    if g:  pts.append(f"保底收益: {g:,.0f}元/月（年合计 {g*12:,.0f}元）")
    if s:  pts.append(f"流水分润: 超过{b:,.0f}元后按{s}%分润")
    if d:  pts.append(f"保证金: {d:,.0f}元（合同结束后退还）")
    if c:  pts.append(f"结算周期: {c}")
    if yrs: pts.append(f"合同年限: {yrs}年，到期可续签")
    return pts[:5]


def _template_contract(nego: Dict, asset: Dict, operator: Dict) -> str:
    g  = nego.get("guaranteed_amount", nego.get("guaranteed_monthly", 0))
    s  = nego.get("revenue_share_pct", 0)
    b  = nego.get("base_revenue", nego.get("revenue_share_base", 0))
    dm = nego.get("deposit_months", 0)
    d  = nego.get("deposit_amount", nego.get("deposit", g * dm if dm else 0))
    rf = nego.get("risk_reserve_pct", 3)
    pf = nego.get("platform_fee_pct", 5)
    ep = nego.get("exit_penalty_months", 3)
    c  = nego.get("settlement_cycle", "月结")
    y  = nego.get("contract_years", 1)

    return (
        f"经营权共创合同规则草案（模板）\n"
        f"{'='*40}\n"
        f"资产方：{asset.get('owner','待确认')}    经营方：{operator.get('name','待确认')}\n"
        f"标的资产：{asset.get('name','未命名')} ({asset.get('area',0)}m²)\n\n"
        f"第一条 收益分配规则\n"
        f"  1.1 保底收益: {g:,}元/月 [系统可执行]\n"
        f"  1.2 流水分润: 月流水超过{b:,}元后，资产方按{s}%分润 [系统可执行]\n"
        f"  1.3 平台服务费: 月流水的{pf}% [系统可执行]\n"
        f"  1.4 风险准备金: 月流水的{rf}%，累计至3个月保底额度后暂停 [系统可执行]\n\n"
        f"第二条 结算条款\n"
        f"  2.1 结算周期: {c} [系统可执行]\n"
        f"  2.2 保底补足: 当月流水分润低于保底金额时，由经营方补足差额 [需人工确认]\n\n"
        f"第三条 保证金\n"
        f"  3.1 保证金: {d:,}元，签约时一次性缴纳 [需人工确认]\n"
        f"  3.2 用途: 用于弥补违约、欠费等损失 [系统可执行]\n\n"
        f"第四条 合同期限与退出\n"
        f"  4.1 合同年限: {y}年 [系统可执行]\n"
        f"  4.2 提前退出违约金: {ep}个月保底金额（即{g*ep:,}元）[需人工确认]\n\n"
        f"【风险评级: {'低' if g>0 and d>0 else '中'}】[建议律师审核]"
    )
