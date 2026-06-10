"""
agents/realty/revenue_share.py — 分账规则 Agent
================================================
根据合同规则和实际流水，计算各方收益并生成分账配置建议。

输入数据（data dict keys）:
    contract_rules  — 合同规则（保底/分润比例/阶梯/服务费/风险准备金等）
    transaction_data — 本期实际流水数据（总流水/支付渠道明细/退款等）
    accounts        — 各方收款账户信息（可选，用于分账配置验证）

输出:
    analysis    — 分账计算说明文本
    signal      — BUY=分账正常 / HOLD=有补足情况 / SELL=流水严重不足
    key_points  — 各方金额摘要
    data_used   — 包含 split_result（可直接入库）
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult


class RevenueShareAgent(BaseAgent):
    name        = "revenue_share"
    description = "分账规则：根据合同和实际流水计算各方应得金额，生成分账配置建议"

    _SYSTEM = (
        "你是一名分账与财务核算专家，专注于不动产经营权共创模式下的收益分配。\n"
        "请对分账计算结果进行解读和说明：\n"
        "  1. 说明本期各方应得金额的计算过程（分润基数→分润金额→保底补足→扣除项）\n"
        "  2. 分析流水与保底的差距，判断经营状态（正常/偏低/严重不足）\n"
        "  3. 如有阶梯分成被触发，解释阶梯规则的执行情况\n"
        "  4. 给出本期结算的注意事项或风险提示\n"
        "  5. 对下期经营给出1-2条改善建议"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        rules = data.get("contract_rules", {})
        txn   = data.get("transaction_data", {})
        accts = data.get("accounts", {})

        split = _calculate_split(rules, txn)

        user_prompt = (
            f"本期分账计算结果：\n"
            f"  实际总流水: {split['gross_revenue']:,.2f}元\n"
            f"  参与分润流水: {split['net_revenue']:,.2f}元\n"
            f"  保底金额: {split['guaranteed']:,.2f}元\n"
            f"  分润比例: {split['share_pct']}%\n"
            f"  资产方分润金额: {split['owner_share']:,.2f}元\n"
            f"  保底补足金额: {split['top_up']:,.2f}元（需经营方补缴）\n"
            f"  资产方实得: {split['owner_total']:,.2f}元\n"
            f"  平台服务费: {split['platform_fee']:,.2f}元\n"
            f"  风险准备金: {split['risk_reserve']:,.2f}元\n"
            f"  经营方实得: {split['operator_net']:,.2f}元\n"
            f"  结算周期: {rules.get('settlement_cycle','月结')}\n\n"
            f"合同规则: 保底{rules.get('guaranteed_monthly',0):,}元/月, "
            f"流水分润{rules.get('revenue_share_pct',0)}%, "
            f"平台{rules.get('platform_fee_pct',5)}%, "
            f"风险准备金{rules.get('risk_reserve_pct',3)}%\n\n"
            "请对以上分账结果进行解读和说明。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=600)
        if not analysis:
            analysis = _template_split_analysis(split, rules)

        signal     = _split_signal(split)
        confidence = 0.95  # 分账计算是确定性逻辑，置信度高
        key_points = _split_key_points(split)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"split_result": split, "contract_rules": rules},
        )


# ── 核心计算逻辑（Decimal精度） ────────────────────────────────────────────────

def _calculate_split(rules: Dict, txn: Dict) -> Dict:
    """
    分账计算（精确到分）

    分配顺序:
        1. 从总流水中扣除: 平台服务费 + 风险准备金 + 退款
        2. 剩余净流水 vs 分润基数 → 计算资产方分润
        3. 资产方分润 vs 保底 → 计算保底补足
        4. 经营方实得 = 净流水 - 资产方总收益 - 平台费 - 风险准备金
    """
    D = Decimal

    gross        = D(str(txn.get("gross_revenue", 0)))
    refunds      = D(str(txn.get("refunds", 0)))
    guaranteed   = D(str(rules.get("guaranteed_monthly", 0)))
    share_pct    = D(str(rules.get("revenue_share_pct", 0))) / D("100")
    base_revenue = D(str(rules.get("revenue_share_base", 0)))
    platform_pct = D(str(rules.get("platform_fee_pct", 5))) / D("100")
    reserve_pct  = D(str(rules.get("risk_reserve_pct", 3))) / D("100")

    # 各扣除项
    net_revenue   = gross - refunds
    platform_fee  = (net_revenue * platform_pct).quantize(D("0.01"), ROUND_HALF_UP)
    risk_reserve  = (net_revenue * reserve_pct).quantize(D("0.01"), ROUND_HALF_UP)
    distributable = net_revenue - platform_fee - risk_reserve

    # 资产方分润（超出分润基数部分 * 分润比例）
    shareable_revenue = max(D("0"), distributable - base_revenue)
    owner_share       = (shareable_revenue * share_pct).quantize(D("0.01"), ROUND_HALF_UP)

    # 保底补足（若分润不足保底，经营方须补足）
    top_up = max(D("0"), guaranteed - owner_share)

    owner_total    = owner_share + top_up
    operator_net   = distributable - owner_total

    # 阶梯分成（如有）
    tiered_triggered = _apply_tiered(rules.get("tiered_rules", []), net_revenue)

    return {
        "gross_revenue":    float(gross),
        "refunds":          float(refunds),
        "net_revenue":      float(net_revenue),
        "platform_fee":     float(platform_fee),
        "risk_reserve":     float(risk_reserve),
        "distributable":    float(distributable),
        "guaranteed":       float(guaranteed),
        "share_pct":        float(rules.get("revenue_share_pct", 0)),
        "owner_share":      float(owner_share),
        "top_up":           float(top_up),
        "owner_total":      float(owner_total),
        "operator_net":     float(operator_net),
        "tiered_triggered": tiered_triggered,
    }


def _apply_tiered(tiered_rules: List[Dict], revenue: Decimal) -> List[Dict]:
    """计算触发的阶梯分成规则"""
    triggered = []
    for rule in tiered_rules:
        threshold = Decimal(str(rule.get("threshold", 0)))
        pct       = rule.get("pct", 0)
        if revenue >= threshold:
            triggered.append({"threshold": float(threshold), "pct": pct,
                               "status": "triggered"})
        else:
            triggered.append({"threshold": float(threshold), "pct": pct,
                               "status": "not_triggered"})
    return triggered


def _split_signal(split: Dict) -> str:
    net        = split.get("net_revenue", 0)
    guaranteed = split.get("guaranteed", 0)
    op_net     = split.get("operator_net", 0)

    if guaranteed <= 0:  return "HOLD"
    coverage = net / guaranteed if guaranteed else 0
    if coverage >= 2.0 and op_net > 0:  return "BUY"
    if coverage >= 1.0 and op_net > 0:  return "HOLD"
    if op_net > 0 and coverage >= 0.5:  return "SELL"    # 经营方能勉强覆盖
    return "STRONG_SELL"   # 经营方净收入为负或流水极低


def _split_status(split: Dict) -> str:
    """根据分账结果给出人读友好状态描述"""
    top_up   = split.get("top_up", 0)
    op_net   = split.get("operator_net", 0)
    guaranteed = split.get("guaranteed", 0)
    if top_up == 0:
        return "流水分润超过保底，经营状况良好"
    if op_net >= guaranteed * 0.5:
        return "保底兜底生效（属正常），经营方净收入尚可"
    if op_net > 0:
        return "保底兜底生效，经营方净收入偏低，建议持续关注"
    return "经营方净收入为负，无法自覆保底，需紧急处置"


def _split_key_points(split: Dict) -> List[str]:
    pts = []
    pts.append(f"本期总流水: {split['gross_revenue']:,.2f}元")
    pts.append(f"资产方实得: {split['owner_total']:,.2f}元"
               + (f"（含补足{split['top_up']:,.2f}元）" if split['top_up'] > 0 else ""))
    pts.append(f"经营方实得: {split['operator_net']:,.2f}元")
    pts.append(f"平台服务费: {split['platform_fee']:,.2f}元  风险准备金: {split['risk_reserve']:,.2f}元")
    if split.get("top_up", 0) > 0:
        pts.append(f"保底补足: {split['top_up']:,.2f}元（需经营方在结算日前缴纳）")
    return pts


def _template_split_analysis(split: Dict, rules: Dict) -> str:
    top_up_line = (
        "保底补足: {:,.2f}元（须补缴）".format(split['top_up'])
        if split['top_up'] > 0 else "流水覆盖保底，无需补足"
    )
    return (
        f"分账计算说明（模板）：\n"
        f"  本期总流水: {split['gross_revenue']:,.2f}元  退款: {split['refunds']:,.2f}元\n"
        f"  净流水: {split['net_revenue']:,.2f}元\n"
        f"  平台服务费 ({rules.get('platform_fee_pct',5)}%): -{split['platform_fee']:,.2f}元\n"
        f"  风险准备金 ({rules.get('risk_reserve_pct',3)}%): -{split['risk_reserve']:,.2f}元\n"
        f"  可分配金额: {split['distributable']:,.2f}元\n"
        f"  资产方分润: {split['owner_share']:,.2f}元\n"
        f"  保底收益: {split['guaranteed']:,.2f}元\n"
        f"  {top_up_line}\n"
        f"  资产方实得: {split['owner_total']:,.2f}元\n"
        f"  经营方实得: {split['operator_net']:,.2f}元\n"
        f"  状态: {_split_status(split)}"
    )
