"""
agents/realty/exit_settlement.py — 退出清算 Agent
==================================================
项目到期、提前终止或换经营方时，生成清算方案。

输入数据（data dict keys）:
    project_info    — 项目基本信息（合同期限/实际开始结束时间）
    financials      — 财务数据（未结收入/欠费/保证金/预收款/违约情况）
    asset_condition — 资产现状（装修状态/设备清单/改造情况）
    exit_reason     — 退出原因（到期/提前/违约/经营失败/换经营方）

输出:
    analysis    — 清算方案草案
    signal      — BUY=清算顺利无纠纷 / HOLD=有待确认项 / SELL=存在较大争议
    key_points  — 各方清算金额摘要
    data_used   — 包含 settlement_result（结构化清算数据）
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult


class ExitSettlementAgent(BaseAgent):
    name        = "exit_settlement"
    description = "退出清算：生成退出方案（欠费扣除/保证金处理/设备交接/违约金计算）"

    _SYSTEM = (
        "你是一名专业的不动产合同清算顾问，处理经营权共创项目的退出和清算。\n"
        "请根据以下数据生成清算草案：\n"
        "  1. 未结收入清算（未付保底/分润账单合计）\n"
        "  2. 保证金处理（全额退还/部分扣除/全额扣除 + 依据）\n"
        "  3. 违约金计算（若有违约，按合同规则计算金额）\n"
        "  4. 预收款退还（如有预付款未消耗部分）\n"
        "  5. 装修与设备处理（保留/赔偿/折旧计算）\n"
        "  6. 最终各方应收/应付金额汇总\n"
        "  7. 交接清单（钥匙/设备/合同/数据/账户注销）\n"
        "  8. 建议处置时间表（7日内/30日内完成）\n\n"
        "标注每项条款是否为 [系统可计算] / [需人工确认] / [建议律师处理]"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        project   = data.get("project_info", {})
        fin       = data.get("financials", {})
        asset     = data.get("asset_condition", {})
        reason    = data.get("exit_reason", "到期终止")

        settlement = _calculate_settlement(project, fin, asset, reason)

        user_prompt = (
            f"退出清算信息：\n"
            f"  项目: {project.get('name','未命名')}  退出原因: {reason}\n"
            f"  合同到期: {project.get('contract_end','未知')}  实际退出: {project.get('actual_exit','未知')}\n\n"
            f"财务状况：\n"
            f"  未结账单: {settlement['unpaid_invoices']:,.2f}元\n"
            f"  保证金: {fin.get('deposit_amount',0):,.2f}元  "
            f"  可扣除: {settlement['deposit_deductible']:,.2f}元\n"
            f"  违约金: {settlement['penalty']:,.2f}元（{reason}）\n"
            f"  预收款退还: {settlement['prepayment_refund']:,.2f}元\n"
            f"  装修/设备折旧: {settlement['depreciation_deduction']:,.2f}元\n\n"
            f"最终清算：\n"
            f"  经营方应付平台/资产方: {settlement['operator_owes']:,.2f}元\n"
            f"  平台/资产方应退经营方: {settlement['platform_owes']:,.2f}元\n"
            f"  净结算金额: {settlement['net_settlement']:,.2f}元"
            f"（{'经营方需支付' if settlement['net_settlement'] > 0 else '平台需退还'}）\n\n"
            "请生成完整清算草案。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=900)
        if not analysis:
            analysis = _template_settlement(settlement, project, reason)

        signal     = _settlement_signal(settlement, reason)
        confidence = _settlement_confidence(fin)
        key_points = _settlement_key_points(settlement)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"settlement_result": settlement},
        )


# ── 清算计算逻辑 ──────────────────────────────────────────────────────────────

def _calculate_settlement(
    project: Dict, fin: Dict, asset: Dict, reason: str,
) -> Dict:
    D = Decimal

    deposit       = D(str(fin.get("deposit_amount", 0)))
    unpaid        = D(str(fin.get("unpaid_invoices", 0)))
    # 兼容 prepayment_received / prepayment 两种命名
    prepayment    = D(str(fin.get("prepayment_received", fin.get("prepayment", 0))))
    prepaid_used  = D(str(fin.get("prepayment_used", 0)))
    guaranteed    = D(str(fin.get("guaranteed_monthly", 0)))
    exit_penalty_months = D(str(fin.get("exit_penalty_months", 3)))

    # 折旧计算：renovation_cost 可在 financials 或 asset_condition 中
    reno_cost     = D(str(asset.get("renovation_cost",
                                    fin.get("renovation_cost", 0))))
    contract_yrs  = D(str(project.get("contract_years", 1)))
    used_months   = D(str(project.get("used_months", 12)))
    contract_months = contract_yrs * 12
    if contract_months > 0:
        remaining_ratio = max(D("0"), (contract_months - used_months) / contract_months)
    else:
        remaining_ratio = D("0")
    depreciation  = (reno_cost * remaining_ratio).quantize(D("0.01"), ROUND_HALF_UP)

    # 违约金计算
    is_breach = reason in ("提前退出", "违约", "经营违规")
    penalty = (guaranteed * exit_penalty_months
               ).quantize(D("0.01"), ROUND_HALF_UP) if is_breach else D("0")

    # 预收款退还（未消耗部分）
    prepayment_refund = max(D("0"), prepayment - prepaid_used)

    # 保证金可扣除项
    deposit_deductible = min(deposit, unpaid + penalty)

    # 汇总
    operator_owes = unpaid + penalty + depreciation
    platform_owes = prepayment_refund + max(D("0"), deposit - deposit_deductible)
    net = (operator_owes - platform_owes).quantize(D("0.01"), ROUND_HALF_UP)

    return {
        "unpaid_invoices":      float(unpaid),
        "deposit_amount":       float(deposit),
        "deposit_deductible":   float(deposit_deductible),
        "penalty":              float(penalty),
        "prepayment_refund":    float(prepayment_refund),
        "depreciation_deduction": float(depreciation),
        "operator_owes":        float(operator_owes),
        "platform_owes":        float(platform_owes),
        "net_settlement":       float(net),
        "is_breach":            is_breach,
    }


def _settlement_signal(s: Dict, reason: str) -> str:
    if s["net_settlement"] > 0 and not s["is_breach"]:
        return "HOLD"   # 经营方有欠款但无违约，需催收
    if s["is_breach"] and s["net_settlement"] > 0:
        return "SELL"   # 违约且有欠款
    if s["operator_owes"] == 0 and s["platform_owes"] == 0:
        return "BUY"    # 干净退出
    return "HOLD"


def _settlement_confidence(fin: Dict) -> float:
    keys = ["deposit_amount", "unpaid_invoices", "guaranteed_monthly", "exit_penalty_months"]
    filled = sum(1 for k in keys if fin.get(k))
    return round(0.5 + 0.12 * filled, 2)


def _settlement_key_points(s: Dict) -> List[str]:
    pts = [f"未结账单: {s['unpaid_invoices']:,.2f}元"]
    if s["penalty"] > 0:
        pts.append(f"违约金: {s['penalty']:,.2f}元")
    pts.append(f"保证金: {s['deposit_amount']:,.2f}元  可扣除: {s['deposit_deductible']:,.2f}元")
    if s["prepayment_refund"] > 0:
        pts.append(f"预收款退还: {s['prepayment_refund']:,.2f}元")
    direction = "经营方付" if s["net_settlement"] > 0 else "平台退"
    pts.append(f"净结算: {abs(s['net_settlement']):,.2f}元（{direction}）")
    return pts[:5]


def _template_settlement(s: Dict, project: Dict, reason: str) -> str:
    penalty_line = (
        "违约金: {:,.2f}元".format(s['penalty'])
        if s['penalty'] > 0 else "无违约金（正常到期）"
    )
    return (
        f"退出清算草案（模板）\n"
        f"{'='*40}\n"
        f"项目: {project.get('name','未命名')}  退出原因: {reason}\n\n"
        f"一、应收账款清算\n"
        f"  未结账单合计: {s['unpaid_invoices']:,.2f}元 [系统可计算]\n\n"
        f"二、违约金\n"
        f"  {penalty_line} [系统可计算]\n\n"
        f"三、保证金处理\n"
        f"  保证金总额: {s['deposit_amount']:,.2f}元\n"
        f"  扣除欠款后退还: {s['deposit_amount'] - s['deposit_deductible']:,.2f}元 [需人工确认]\n\n"
        f"四、预收款退还\n"
        f"  应退预收款: {s['prepayment_refund']:,.2f}元 [系统可计算]\n\n"
        f"五、装修折旧\n"
        f"  折旧扣除: {s['depreciation_deduction']:,.2f}元 [需人工确认]\n\n"
        f"六、最终净结算\n"
        f"  经营方应付: {s['operator_owes']:,.2f}元\n"
        f"  平台方应退: {s['platform_owes']:,.2f}元\n"
        f"  净结算: {abs(s['net_settlement']):,.2f}元 "
        f"({'经营方支付' if s['net_settlement'] > 0 else '平台退还'})\n\n"
        f"七、交接清单\n"
        f"  [ ] 场地钥匙及门禁权限\n"
        f"  [ ] 合同及附件文件\n"
        f"  [ ] 收款账户注销/变更\n"
        f"  [ ] 备案收银系统注销\n"
        f"  [ ] 经营数据存档\n"
        f"  [ ] 水电气账户过户\n\n"
        f"[注：以上为草案，请由平台运营人员核实后提交审批]"
    )
