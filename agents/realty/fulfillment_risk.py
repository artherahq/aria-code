"""
agents/realty/fulfillment_risk.py — 履约风控 Agent
===================================================
监控合同履约和资金风险，生成风险等级和处置建议。

输入数据（data dict keys）:
    invoices        — 账单列表（应收/实收/逾期情况）
    contract_rules  — 合同规则（保底/保证金/结算周期等）
    cashflow_status — 最新流水核验状态
    account_changes — 收款账户变更记录
    compliance_flags— 合规预警标志（私自换码/改业态等）

输出:
    analysis    — 风险报告
    signal      — BUY=低风险 / HOLD=中等风险 / SELL=高风险 / STRONG_SELL=极高风险
    key_points  — 风险事项清单（按级别）
"""
from __future__ import annotations

from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult


class FulfillmentRiskAgent(BaseAgent):
    name        = "fulfillment_risk"
    description = "履约风控：监控逾期/保底/保证金/私账/业态合规，生成风险等级和处置建议"

    _SYSTEM = (
        "你是一名专业的不动产合同履约风险管理专家。\n"
        "请对履约风险评估结果进行分析：\n"
        "  1. 逾期风险：账单逾期天数、逾期金额、逾期频次\n"
        "  2. 保底风险：实际流水是否持续低于保底标准\n"
        "  3. 保证金风险：保证金余额是否充足（低于1个月保底需预警）\n"
        "  4. 资金风险：是否存在私自更换收款码、现金比例过高\n"
        "  5. 合规风险：是否擅自改变业态、违规装修、无照经营\n"
        "  6. 综合风险等级：[低] / [中] / [高] / [极高]\n"
        "  7. 处置建议：催缴/整改通知/保证金扣除/退出清算（分级建议）"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        invoices   = data.get("invoices", [])
        rules      = data.get("contract_rules", {})
        cf_status  = data.get("cashflow_status", {})
        acct_chg   = data.get("account_changes", [])
        flags      = data.get("compliance_flags", [])

        risk = _assess_risk(invoices, rules, cf_status, acct_chg, flags)

        user_prompt = (
            f"履约风险评估：\n"
            f"  逾期账单: {risk['overdue_count']}张  逾期金额: {risk['overdue_amount']:,.2f}元\n"
            f"  最长逾期: {risk['max_overdue_days']}天\n"
            f"  保底覆盖率: {risk['guaranteed_coverage_pct']:.1f}%（近3期平均）\n"
            f"  保证金余额: {risk['deposit_balance']:,.2f}元  "
            f"  要求: {risk['deposit_required']:,.2f}元\n"
            f"  账户变更记录: {len(acct_chg)}次\n"
            f"  合规标志: {', '.join(flags) or '无'}\n\n"
            f"风险事项（{risk['risk_count']}项）：\n"
            + "\n".join(f"  [{item['level']}] {item['desc']}" for item in risk["risk_items"])
            + "\n\n请完成风险报告并给出处置建议。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=700)
        if not analysis:
            analysis = _template_risk_report(risk, rules)

        signal     = _risk_signal(risk)
        confidence = _risk_confidence(risk, invoices)
        key_points = _risk_key_points(risk)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"risk_assessment": risk},
        )


# ── 风险评估逻辑 ──────────────────────────────────────────────────────────────

def _assess_risk(
    invoices: List[Dict], rules: Dict, cf: Dict,
    acct_changes: List[Dict], flags: List[str],
) -> Dict:
    guaranteed  = rules.get("guaranteed_monthly", 0)
    deposit_req = rules.get("deposit_amount", 0)
    deposit_bal = rules.get("deposit_balance", deposit_req)  # 当前余额

    risk_items: List[Dict] = []

    # 1. 逾期账单分析
    overdue = [inv for inv in invoices if inv.get("status") in ("overdue", "逾期")]
    overdue_amount = sum(inv.get("amount", 0) - inv.get("paid", 0) for inv in overdue)
    max_overdue_days = max((inv.get("overdue_days", 0) for inv in overdue), default=0)

    if overdue_amount > 0:
        level = "极高" if max_overdue_days > 60 else ("高" if max_overdue_days > 30 else "中")
        risk_items.append({"level": level,
                           "desc": f"共{len(overdue)}张账单逾期，金额{overdue_amount:,.2f}元，最长{max_overdue_days}天"})

    # 2. 保底覆盖率（取近几期）
    recent_revenues = [inv.get("revenue", 0) for inv in invoices[-3:] if inv.get("revenue")]
    if recent_revenues and guaranteed > 0:
        avg_coverage = sum(r / guaranteed * 100 for r in recent_revenues) / len(recent_revenues)
        if avg_coverage < 60:
            risk_items.append({"level": "高",
                               "desc": f"近{len(recent_revenues)}期流水平均仅覆盖保底{avg_coverage:.1f}%"})
        elif avg_coverage < 80:
            risk_items.append({"level": "中",
                               "desc": f"近期流水覆盖保底{avg_coverage:.1f}%，持续偏低"})
    else:
        avg_coverage = 100.0

    # 3. 保证金不足
    if deposit_req > 0 and deposit_bal < deposit_req * 0.5:
        risk_items.append({"level": "高",
                           "desc": f"保证金余额{deposit_bal:,.0f}元不足要求{deposit_req:,.0f}元的50%"})
    elif deposit_req > 0 and deposit_bal < deposit_req:
        risk_items.append({"level": "中",
                           "desc": f"保证金余额{deposit_bal:,.0f}元低于合同要求{deposit_req:,.0f}元"})

    # 4. 账户变更
    if len(acct_changes) > 0:
        unauth = [c for c in acct_changes if not c.get("approved")]
        if unauth:
            risk_items.append({"level": "极高",
                               "desc": f"发现{len(unauth)}次未授权收款账户变更，疑似私账行为"})
        else:
            risk_items.append({"level": "低",
                               "desc": f"共{len(acct_changes)}次账户变更，已审批"})

    # 5. 流水核验异常
    cf_signal = cf.get("signal", "")
    if cf_signal in ("SELL", "STRONG_SELL"):
        risk_items.append({"level": "高",
                           "desc": f"流水核验异常（{cf.get('anomaly_summary','')}），需稽查"})

    # 6. 合规标志
    for flag in flags:
        risk_items.append({"level": "高", "desc": f"合规违规: {flag}"})

    return {
        "overdue_count":           len(overdue),
        "overdue_amount":          overdue_amount,
        "max_overdue_days":        max_overdue_days,
        "guaranteed_coverage_pct": avg_coverage,
        "deposit_balance":         deposit_bal,
        "deposit_required":        deposit_req,
        "risk_items":              risk_items,
        "risk_count":              len(risk_items),
        "has_extreme_risk":        any(r["level"] == "极高" for r in risk_items),
        "has_high_risk":           any(r["level"] == "高" for r in risk_items),
    }


def _risk_signal(risk: Dict) -> str:
    if risk.get("has_extreme_risk"): return "STRONG_SELL"
    if risk.get("has_high_risk"):    return "SELL"
    mid_count = sum(1 for r in risk.get("risk_items", []) if r["level"] == "中")
    if mid_count >= 2:               return "SELL"
    if mid_count >= 1:               return "HOLD"
    return "BUY"


def _risk_confidence(risk: Dict, invoices: List) -> float:
    data_points = sum([
        1 if invoices else 0,
        1 if risk.get("deposit_required", 0) > 0 else 0,
        1 if risk.get("guaranteed_coverage_pct", 100) < 100 else 0,
    ])
    return round(0.6 + 0.1 * data_points, 2)


def _risk_key_points(risk: Dict) -> List[str]:
    pts = []
    if risk["overdue_amount"] > 0:
        pts.append(f"逾期金额: {risk['overdue_amount']:,.2f}元（最长{risk['max_overdue_days']}天）")
    pts.append(f"保底覆盖率: {risk['guaranteed_coverage_pct']:.1f}%")
    pts.append(f"保证金: {risk['deposit_balance']:,.2f}/{risk['deposit_required']:,.2f}元")
    for item in [r for r in risk["risk_items"] if r["level"] in ("极高", "高")][:2]:
        pts.append(f"[{item['level']}风险] {item['desc'][:50]}")
    return pts[:5]


def _template_risk_report(risk: Dict, rules: Dict) -> str:
    level_map = {True: "极高", False: ""}
    overall = "极高" if risk["has_extreme_risk"] else ("高" if risk["has_high_risk"] else
              ("中" if risk["risk_count"] > 0 else "低"))
    return (
        f"履约风控报告（模板）：\n"
        f"  综合风险等级: {overall}\n"
        f"  逾期账单: {risk['overdue_count']}张  逾期金额: {risk['overdue_amount']:,.2f}元\n"
        f"  最长逾期天数: {risk['max_overdue_days']}天\n"
        f"  保底覆盖率: {risk['guaranteed_coverage_pct']:.1f}%\n"
        f"  保证金: {risk['deposit_balance']:,.2f}/{risk['deposit_required']:,.2f}元\n"
        f"  风险事项 ({risk['risk_count']}项):\n"
        + "\n".join(f"    [{r['level']}] {r['desc']}" for r in risk["risk_items"])
        + f"\n  处置建议: "
        + ("立即启动退出清算程序" if overall == "极高" else
           "发出整改通知，7日内未整改启动违约程序" if overall == "高" else
           "发出催缴通知，持续监控" if overall == "中" else "保持正常监控")
    )
