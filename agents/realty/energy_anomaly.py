"""
agents/realty/energy_anomaly.py — 能耗异常 Agent
=================================================
用水电、门禁、停车、客流判断经营状态，识别：空置/低效/超范围/欠费/异常经营。

输入数据（data dict keys）:
    energy_records  — 水电用量历史（按日/周/月）
    access_records  — 门禁/访客记录
    parking_records — 停车记录
    foot_traffic    — 客流数据
    revenue_data    — 对应期营业流水（用于交叉验证）
    benchmarks      — 同业基准值（可选）

输出:
    analysis    — 能耗分析报告
    signal      — BUY=正常 / HOLD=轻度异常 / SELL=严重异常/疑似空置
    key_points  — 异常指标清单
"""
from __future__ import annotations

from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult


class EnergyAnomalyAgent(BaseAgent):
    name        = "energy_anomaly"
    description = "能耗异常：交叉分析水电、门禁、客流与流水，识别空置/低效/异常经营"

    _SYSTEM = (
        "你是一名商业空间运营监控专家，擅长通过能耗和空间数据识别异常经营行为。\n"
        "请对能耗分析结果进行解读：\n"
        "  1. 水电用量是否与营业流水匹配（高流水应有高能耗）\n"
        "  2. 门禁/客流记录是否与营业时段一致\n"
        "  3. 是否存在非营业时段用电（可能夜间经营未申报）\n"
        "  4. 能耗趋势分析（是否逐月下降，可能经营在减少）\n"
        "  5. 综合判断：[正常经营] / [低效经营] / [空置] / [疑似违规经营] / [超范围经营]\n"
        "  6. 处置建议（巡检、整改、补缴水电费等）"
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        energy  = data.get("energy_records", {})
        access  = data.get("access_records", {})
        parking = data.get("parking_records", {})
        traffic = data.get("foot_traffic", {})
        revenue = data.get("revenue_data", {})
        bench   = data.get("benchmarks", {})

        check = _analyze_energy(energy, access, parking, traffic, revenue, bench)

        user_prompt = (
            f"能耗分析数据：\n"
            f"  本期用电: {energy.get('electricity_kwh',0):.1f} kWh  "
            f"  用水: {energy.get('water_tons',0):.1f} 吨\n"
            f"  环比变化: 用电{check['elec_change_pct']:+.1f}%  用水{check['water_change_pct']:+.1f}%\n"
            f"  门禁进出: {access.get('entry_count',0)}次/期  停车: {parking.get('vehicle_count',0)}辆/期\n"
            f"  客流量: {traffic.get('total_visits',0)}人次\n"
            f"  营业流水: {revenue.get('declared',0):,.2f}元\n"
            f"  每千元流水能耗: {check['energy_per_revenue']:.2f} kWh\n"
            f"  基准能耗/千元: {bench.get('energy_per_1k_revenue', 2.5):.2f} kWh\n\n"
            f"发现异常：\n"
            + "\n".join(f"  - {a}" for a in check["anomalies"])
            + "\n\n请完成能耗异常分析报告。"
        )

        analysis = await self._call_llm(self._SYSTEM, user_prompt, max_tokens=600)
        if not analysis:
            analysis = _template_energy(check, energy, revenue)

        signal     = _energy_signal(check)
        confidence = _energy_confidence(check)
        key_points = _energy_key_points(check, energy)

        return AgentResult(
            agent      = self.name,
            symbol     = symbol,
            analysis   = analysis,
            confidence = confidence,
            signal     = signal,
            key_points = key_points,
            data_used  = {"energy_check": check},
        )


# ── 核验逻辑 ──────────────────────────────────────────────────────────────────

def _analyze_energy(
    energy: Dict, access: Dict, parking: Dict,
    traffic: Dict, revenue: Dict, bench: Dict,
) -> Dict:
    elec      = energy.get("electricity_kwh", 0)
    elec_prev = energy.get("prev_electricity_kwh", elec)
    water     = energy.get("water_tons", 0)
    water_prev= energy.get("prev_water_tons", water)
    rev       = revenue.get("declared", 0)
    entries   = access.get("entry_count", 0)
    vehicles  = parking.get("vehicle_count", 0)
    visits    = traffic.get("total_visits", 0)

    elec_change  = (elec - elec_prev) / elec_prev * 100 if elec_prev else 0
    water_change = (water - water_prev) / water_prev * 100 if water_prev else 0

    # 每千元流水能耗
    energy_per_rev = elec / (rev / 1000) if rev > 0 else 0
    bench_per_rev  = bench.get("energy_per_1k_revenue", 2.5)

    anomalies = []

    # 空置判断（低能耗 + 低流水 + 低门禁）
    if elec < 10 and rev < 1000 and entries < 5:
        anomalies.append("水电用量极低且无门禁记录，疑似空置状态")

    # 能耗与流水不匹配
    if rev > 0 and energy_per_rev > bench_per_rev * 3:
        anomalies.append(f"能耗/流水比异常偏高（{energy_per_rev:.2f}）,可能有黑账")
    elif rev > 0 and energy_per_rev < bench_per_rev * 0.3 and elec > 50:
        anomalies.append("高能耗配合低流水，疑似流水漏报或私账")

    # 能耗持续下降
    if elec_change < -30:
        anomalies.append(f"用电量环比下降{abs(elec_change):.1f}%，经营规模可能在萎缩")

    # 非营业时段用电（能耗数据需细化到时段）
    night_elec = energy.get("night_electricity_kwh", 0)
    if night_elec > elec * 0.4:
        anomalies.append(f"夜间用电占比{night_elec/elec*100:.1f}%偏高，可能存在夜间未申报经营")

    # 门禁/客流 vs 流水
    if visits > 0 and rev > 0:
        revenue_per_visit = rev / visits
        bench_rpv = bench.get("revenue_per_visit", 50)
        if revenue_per_visit < bench_rpv * 0.3:
            anomalies.append(f"人均消费{revenue_per_visit:.1f}元/人远低于基准{bench_rpv}元，流水疑似偏低")

    # 水费欠缴
    if energy.get("water_arrears", 0) > 0:
        anomalies.append(f"水费欠缴: {energy['water_arrears']:,.2f}元")
    if energy.get("electricity_arrears", 0) > 0:
        anomalies.append(f"电费欠缴: {energy['electricity_arrears']:,.2f}元")

    return {
        "elec_kwh":           elec,
        "water_tons":         water,
        "elec_change_pct":    elec_change,
        "water_change_pct":   water_change,
        "energy_per_revenue": energy_per_rev,
        "bench_per_revenue":  bench_per_rev,
        "entries":            entries,
        "vehicles":           vehicles,
        "visits":             visits,
        "anomalies":          anomalies,
        "anomaly_count":      len(anomalies),
    }


def _energy_signal(check: Dict) -> str:
    n = check.get("anomaly_count", 0)
    if n == 0: return "BUY"
    if n == 1: return "HOLD"
    if n == 2: return "SELL"
    return "STRONG_SELL"


def _energy_confidence(check: Dict) -> float:
    sources = sum(1 for k in ["elec_kwh", "entries", "visits"] if check.get(k, 0) > 0)
    return round(min(0.9, 0.5 + 0.15 * sources), 2)


def _energy_key_points(check: Dict, energy: Dict) -> List[str]:
    pts = [f"用电 {check['elec_kwh']:.1f} kWh（环比{check['elec_change_pct']:+.1f}%）"
           f"  用水 {check['water_tons']:.1f} 吨"]
    pts.append(f"门禁进出 {check['entries']} 次  客流 {check['visits']} 人次")
    for a in check["anomalies"][:3]:
        pts.append(f"异常: {a}")
    return pts[:5]


def _template_energy(check: Dict, energy: Dict, revenue: Dict) -> str:
    status = "正常" if check["anomaly_count"] == 0 else f"发现 {check['anomaly_count']} 项异常"
    return (
        f"能耗异常分析（模板）：\n"
        f"  用电: {check['elec_kwh']:.1f} kWh  用水: {check['water_tons']:.1f} 吨\n"
        f"  门禁: {check['entries']}次  客流: {check['visits']}人次\n"
        f"  营业流水: {revenue.get('declared',0):,.2f}元\n"
        f"  每千元能耗: {check['energy_per_revenue']:.2f} kWh（基准{check['bench_per_revenue']:.2f}）\n"
        f"  核验状态: {status}\n"
        + ("  异常项目:\n" + "\n".join(f"    • {a}" for a in check["anomalies"])
           if check["anomalies"] else "  无异常")
    )
