"""
agents/realty/cashflow_verify.py — 流水核验 Agent
=================================================
判断经营方申报流水是否真实，识别私账/逃费/低报行为。

输入数据（data dict keys）:
    pos_transactions    — POS/扫码流水列表（可选）
    bank_statements     — 银行流水（可选）
    delivery_revenue    — 外卖/团购收入（可选）
    inventory_data      — 库存进货数据（可选）
    energy_data         — 水电用量（可选）
    declared_revenue    — 经营方自报流水
    expected_revenue    — 系统预估流水（基于历史/业态/客流）

输出:
    analysis    — 核验报告
    signal      — BUY=流水真实 / HOLD=轻度异常需复核 / SELL=疑似造假/私账
    key_points  — 异常项目清单
"""
from __future__ import annotations

from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult


class CashFlowVerifyAgent(BaseAgent):
    name        = "cashflow_verify"
    description = "流水核验：多源数据交叉比对，识别私账/逃费/低报流水行为"

    _SYSTEM = (
        "你是一名专业的财务审计和风控专家，擅长识别经营流水中的异常。\n"
        "请对流水核验结果进行分析：\n"
        "  1. 各数据源的一致性分析（POS/银行/外卖/库存/能耗）\n"
        "  2. 申报流水与预估流水的差异分析\n"
        "  3. 识别的异常模式（如：只用现金、收款码变更、能耗与流水不匹配）\n"
        "  4. 风险等级判断：[正常] / [需复核] / [疑似私账] / [建议稽查]\n"
        "  5. 具体建议：要求补充哪些凭证，或启动哪种核查程序"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        # 支持两种输入格式：
        #   标量简化模式: pos_revenue=55000, bank_revenue=48000, cash_ratio=0.35
        #   列表精细模式: pos_transactions=[{amount,payment_type,...}], bank_statements=[{amount,type,...}]
        pos      = data.get("pos_transactions", [])
        bank     = data.get("bank_statements", [])
        delivery = data.get("delivery_revenue", 0)
        inventory= data.get("inventory_data", {})
        energy   = data.get("energy_data", {})
        declared = data.get("declared_revenue", 0)
        expected = data.get("expected_revenue", 0)

        # 标量快捷键：如果没有列表数据但有聚合数字，转换为单条虚拟记录
        if not pos and data.get("pos_revenue", 0):
            pos_total_val = data["pos_revenue"]
            cash_r = data.get("cash_ratio", 0.0)   # 0.0~1.0
            if cash_r > 0:
                pos = [
                    {"amount": pos_total_val * (1 - cash_r), "payment_type": "digital"},
                    {"amount": pos_total_val * cash_r,       "payment_type": "cash"},
                ]
            else:
                pos = [{"amount": pos_total_val, "payment_type": "digital"}]
        if not bank and data.get("bank_revenue", 0):
            bank = [{"amount": data["bank_revenue"], "type": "IN"}]
        if not delivery and data.get("wechat_revenue", 0):
            delivery = data.get("wechat_revenue", 0) + data.get("alipay_revenue", 0)

        # 交叉核验
        check = _cross_verify(pos, bank, delivery, inventory, energy, declared, expected)

        user_prompt = (
            f"流水核验数据：\n"
            f"  经营方申报流水: {declared:,.2f}元\n"
            f"  系统预估流水: {expected:,.2f}元\n"
            f"  差异率: {check['gap_pct']:.1f}%（{'偏低' if check['gap_pct'] < 0 else '偏高'}）\n\n"
            f"数据源汇总：\n"
            f"  POS/扫码流水: {check['pos_total']:,.2f}元（{len(pos)}笔）\n"
            f"  银行流水: {check['bank_total']:,.2f}元\n"
            f"  外卖/团购收入: {delivery:,.2f}元\n"
            f"  能耗指数（用电）: {energy.get('electricity_kwh',0)} kWh\n"
            f"  库存周转估算: {check['inventory_implied_revenue']:,.2f}元\n\n"
            f"发现异常：\n"
            + "\n".join(f"  - {a}" for a in check["anomalies"])
            + "\n\n请完成流水核验报告。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=700)
        if not analysis:
            analysis = _template_verify(check, declared, expected)

        signal     = _verify_signal(check)
        confidence = _verify_confidence(check)
        key_points = _verify_key_points(check, declared, expected)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"verify_result": check},
        )


# ── 核验逻辑 ──────────────────────────────────────────────────────────────────

def _cross_verify(
    pos: List[Dict], bank: List[Dict], delivery: float,
    inventory: Dict, energy: Dict, declared: float, expected: float,
) -> Dict:
    pos_total = sum(t.get("amount", 0) for t in pos if not t.get("is_refund"))
    bank_total = sum(t.get("amount", 0) for t in bank if t.get("type") in ("IN", "in", None))

    # 库存推算流水（进货金额 * 毛利率倒推）
    purchase_cost = inventory.get("purchase_cost", 0)
    gross_margin  = inventory.get("expected_margin_pct", 40) / 100
    inv_implied   = purchase_cost / (1 - gross_margin) if gross_margin < 1 else 0

    # 综合核验流水（取最大可信来源）
    verified_sources = [s for s in [pos_total, bank_total, delivery + pos_total] if s > 0]
    verified_max = max(verified_sources) if verified_sources else declared

    gap_pct = ((declared - expected) / expected * 100) if expected else 0
    source_gap = ((declared - verified_max) / declared * 100) if declared else 0

    anomalies = []

    # 申报 vs 预期 差距
    if gap_pct < -30:
        anomalies.append(f"申报流水比预估低{abs(gap_pct):.1f}%，偏差较大")
    if gap_pct < -50:
        anomalies.append("申报流水不足预估50%，建议启动稽查")

    # POS vs 银行 不一致
    if pos_total > 0 and bank_total > 0:
        consistency = abs(pos_total - bank_total) / max(pos_total, bank_total)
        if consistency > 0.2:
            anomalies.append(f"POS流水({pos_total:,.0f})与银行流水({bank_total:,.0f})差异{consistency*100:.1f}%")

    # 申报 vs 核验源 差距
    if declared > 0 and verified_max > 0 and source_gap > 25:
        anomalies.append(f"申报流水比可核验来源高{source_gap:.1f}%，可能虚报")
    elif declared < verified_max * 0.7:
        anomalies.append(f"申报流水({declared:,.0f})低于可核验来源({verified_max:,.0f})，疑似漏报")

    # 能耗异常（有水电无流水）
    elec = energy.get("electricity_kwh", 0)
    if elec > 0 and declared == 0:
        anomalies.append("有水电消耗记录但申报流水为零，疑似有经营未申报")

    # 库存推算差距
    if inv_implied > 0 and declared < inv_implied * 0.6:
        anomalies.append(f"库存进货推算流水{inv_implied:,.0f}元，申报仅{declared:,.0f}元，差距较大")

    # 现金比例过高
    cash_txns = [t for t in pos if t.get("payment_type") in ("cash", "现金")]
    if pos and len(cash_txns) / len(pos) > 0.3:
        anomalies.append(f"现金交易占比{len(cash_txns)/len(pos)*100:.1f}%偏高，难以核验")

    return {
        "pos_total":              pos_total,
        "bank_total":             bank_total,
        "delivery":               delivery,
        "inventory_implied_revenue": inv_implied,
        "verified_max":           verified_max,
        "gap_pct":                gap_pct,
        "source_gap_pct":         source_gap,
        "anomalies":              anomalies,
        "anomaly_count":          len(anomalies),
    }


def _verify_signal(check: Dict) -> str:
    n = check.get("anomaly_count", 0)
    gap = abs(check.get("gap_pct", 0))
    if n == 0 and gap < 15: return "BUY"
    if n <= 1 and gap < 30: return "HOLD"
    if n <= 2 and gap < 50: return "SELL"
    return "STRONG_SELL"


def _verify_confidence(check: Dict) -> float:
    sources = sum(1 for k in ["pos_total", "bank_total", "delivery"] if check.get(k, 0) > 0)
    return round(min(0.9, 0.5 + 0.15 * sources), 2)


def _verify_key_points(check: Dict, declared: float, expected: float) -> List[str]:
    pts = [f"申报流水: {declared:,.2f}元  预估流水: {expected:,.2f}元  "
           f"差异: {check['gap_pct']:+.1f}%"]
    if check["pos_total"]:
        pts.append(f"POS核验流水: {check['pos_total']:,.2f}元")
    for a in check["anomalies"][:3]:
        pts.append(f"异常: {a}")
    return pts[:5]


def _template_verify(check: Dict, declared: float, expected: float) -> str:
    status = "正常" if check["anomaly_count"] == 0 else "发现异常"
    return (
        f"流水核验报告（模板）：\n"
        f"  申报流水: {declared:,.2f}元  预估流水: {expected:,.2f}元  "
        f"差异: {check['gap_pct']:+.1f}%\n"
        f"  POS流水: {check['pos_total']:,.2f}元  银行流水: {check['bank_total']:,.2f}元\n"
        f"  核验状态: {status}\n"
        + ("  发现异常:\n" + "\n".join(f"    • {a}" for a in check["anomalies"])
           if check["anomalies"] else "  无异常项目")
    )
