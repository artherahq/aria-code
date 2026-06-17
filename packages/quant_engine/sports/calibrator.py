"""
sports/calibrator.py — 模型参数自动优化
=========================================
基于历史预测 Brier Score 和实际进球，持续校准攻防斜率与 λ 偏差。

自动优化流程（数据积累触发）:
  ≥ 5 场已结算  → λ 偏差修正（实际进球 / 预测 λ 的指数移动平均）
  ≥ 10 场已结算 → 攻防斜率网格搜索（最小化 Brier Score）
  ≥ 3 场单队数据 → 队伍专属进球偏差修正

持久化:
  ~/.arthera/wc_calibrated_params.json  — 全局斜率 + λ 偏差
  ~/.arthera/team_goal_bias.json        — 各队进球偏差 EMA
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_PARAMS_PATH    = Path.home() / ".arthera" / "wc_calibrated_params.json"
_TEAM_BIAS_PATH = Path.home() / ".arthera" / "team_goal_bias.json"

_DEFAULT_PARAMS = {
    "a1": 1.05, "a2": 0.35,   # 攻击斜率（线性 + 二次）
    "d1": -0.42, "d2": -0.10, # 防守斜率
    "lambda_home_bias": 1.0,   # λ 偏差修正因子
    "lambda_away_bias": 1.0,
    "conf_temp": 1.0,          # 概率温度（<1 收敛过度自信的预测）
    "calibrated_at": None,
    "n_matches": 0,
    "avg_brier": None,
}


# ── 持久化工具 ────────────────────────────────────────────────────────────────

def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── 1. λ 偏差修正 ─────────────────────────────────────────────────────────────

def optimize_lambda_bias(settled_records: List[Dict]) -> Dict[str, float]:
    """
    从已结算预测计算主客队 λ 系统偏差。

    每条记录需包含: actual_home_goals, actual_away_goals, lambda_home, lambda_away。
    偏差 = mean(actual / predicted)；> 1.0 说明模型系统性低估进球。
    需要 ≥ 5 条含实际进球的记录才修正。
    """
    home_ratios, away_ratios = [], []

    for r in settled_records:
        ah = r.get("actual_home_goals")
        aa = r.get("actual_away_goals")
        lh = r.get("lambda_home")
        la = r.get("lambda_away")
        if ah is not None and lh and lh > 0:
            home_ratios.append(ah / lh)
        if aa is not None and la and la > 0:
            away_ratios.append(aa / la)

    result = {"home_bias": 1.0, "away_bias": 1.0, "n_home": len(home_ratios), "n_away": len(away_ratios)}
    if len(home_ratios) >= 5:
        result["home_bias"] = round(sum(home_ratios) / len(home_ratios), 4)
    if len(away_ratios) >= 5:
        result["away_bias"] = round(sum(away_ratios) / len(away_ratios), 4)
    return result


# ── 2. 攻防斜率网格搜索 ───────────────────────────────────────────────────────

def optimize_slopes_from_outcomes(settled_records: List[Dict]) -> Dict:
    """
    从已结算预测记录（需含 home_elo / away_elo / result）搜索最优攻防斜率。

    策略：固定 d1 = -a1×0.40, d2 = -a2×0.29（对称性假设），
    2D 网格搜索 a1 × a2，最小化平均 Brier Score。
    需要 ≥ 10 条含 Elo 数据的记录。
    """
    records = [
        r for r in settled_records
        if r.get("home_elo") and r.get("away_elo") and r.get("result")
    ]
    if len(records) < 10:
        return {**_DEFAULT_PARAMS, "status": "not_enough_data", "n": len(records)}

    try:
        from .dixon_coles import compute_match_probabilities
    except ImportError:
        return {**_DEFAULT_PARAMS, "status": "import_error"}

    best_brier = float("inf")
    best = {}

    for a1 in [0.85, 0.95, 1.05, 1.15, 1.25, 1.35]:
        for a2 in [0.20, 0.28, 0.35, 0.43, 0.50]:
            d1 = round(-a1 * 0.40, 3)
            d2 = round(-a2 * 0.29, 3)
            total_brier = 0.0

            for r in records:
                h_elo = float(r["home_elo"])
                a_elo = float(r["away_elo"])
                si_h  = (h_elo - 1500) / 400.0
                si_a  = (a_elo - 1500) / 400.0

                h_atk = max(0.45, min(1.10 + si_h*a1 + max(0, si_h)*si_h*a2, 2.60))
                a_atk = max(0.45, min(1.10 + si_a*a1 + max(0, si_a)*si_a*a2, 2.60))
                h_def = max(0.40, min(0.95 + si_h*d1 + max(0, si_h)*si_h*d2, 1.25))
                a_def = max(0.40, min(0.95 + si_a*d1 + max(0, si_a)*si_a*d2, 1.25))

                league_avg = float(r.get("league_avg", 1.35))
                lh = max(0.2, min(h_atk * a_def * league_avg, 8.0))
                la = max(0.2, min(a_atk * h_def * league_avg, 8.0))
                elo_diff = h_elo - a_elo

                dc = compute_match_probabilities(lh, la, rho=-0.10, elo_diff=elo_diff)
                res = r["result"]
                ih = 1.0 if res == "home" else 0.0
                id_ = 1.0 if res == "draw" else 0.0
                ia  = 1.0 if res == "away" else 0.0
                total_brier += (dc["home_win"]-ih)**2 + (dc["draw"]-id_)**2 + (dc["away_win"]-ia)**2

            avg_brier = total_brier / len(records)
            if avg_brier < best_brier:
                best_brier = avg_brier
                best = {
                    "a1": a1, "a2": a2, "d1": d1, "d2": d2,
                    "avg_brier": round(avg_brier, 4),
                    "n": len(records),
                    "status": "optimized",
                }

    return best


# ── 2b. 概率温度校准（对抗过度自信）──────────────────────────────────────────

def _apply_temp(ph: float, pd: float, pa: float, temp: float) -> Tuple[float, float, float]:
    """Temperature-scale a 1X2 distribution: p_i ∝ p_i**temp, renormalized.

    temp < 1 flattens (less confident); temp = 1 is a no-op. Guards against
    zero/negative inputs.
    """
    if temp == 1.0:
        return ph, pd, pa
    eps = 1e-9
    h = max(ph, eps) ** temp
    d = max(pd, eps) ** temp
    a = max(pa, eps) ** temp
    s = h + d + a
    return h / s, d / s, a / s


def optimize_confidence_temp(settled_records: List[Dict]) -> Dict:
    """Grid-search the probability temperature that minimizes Brier.

    World-Cup favorites are systematically over-predicted (e.g. 88% → draw),
    so the raw 1X2 distribution is too sharp. We find temp ∈ [0.55, 1.0] that
    flattens predictions to best match observed outcomes.

    Trains on RAW probabilities (raw_home_win/…) when present so it never
    compounds a temperature already applied at output; falls back to the
    stored home_win/… for legacy records. Needs ≥ 8 settled records.
    """
    rows = []
    for r in settled_records:
        if not r.get("result"):
            continue
        ph = r.get("raw_home_win", r.get("home_win"))
        pd = r.get("raw_draw",     r.get("draw"))
        pa = r.get("raw_away_win", r.get("away_win"))
        if ph is None or pd is None or pa is None:
            continue
        rows.append((float(ph), float(pd), float(pa), r["result"]))

    if len(rows) < 8:
        return {"temp": 1.0, "status": "not_enough_data", "n": len(rows)}

    def mean_brier(temp: float) -> float:
        tot = 0.0
        for ph, pd, pa, res in rows:
            h, d, a = _apply_temp(ph, pd, pa, temp)
            ih = 1.0 if res == "home" else 0.0
            idr = 1.0 if res == "draw" else 0.0
            ia = 1.0 if res == "away" else 0.0
            tot += (h - ih) ** 2 + (d - idr) ** 2 + (a - ia) ** 2
        return tot / len(rows)

    base = mean_brier(1.0)
    best_temp, best_brier = 1.0, base
    t = 0.55
    while t <= 1.001:
        b = mean_brier(t)
        if b < best_brier:
            best_brier, best_temp = b, round(t, 2)
        t += 0.05

    return {
        "temp": best_temp,
        "status": "optimized",
        "n": len(rows),
        "brier_before": round(base, 4),
        "brier_after": round(best_brier, 4),
    }


def get_confidence_temp() -> float:
    return get_calibrated_params().get("conf_temp", 1.0)


# ── 3. 队伍专属进球偏差（EMA）─────────────────────────────────────────────────

def update_team_goal_bias(team: str, predicted_lambda: float, actual_goals: int) -> None:
    """
    更新队伍历史进球偏差（指数移动平均，alpha=0.3）。
    bias_ema > 1 表示该队实际进球通常高于模型预测。
    """
    if predicted_lambda <= 0:
        return

    biases: Dict = _load_json(_TEAM_BIAS_PATH, {})
    key   = team.lower().strip()
    ratio = actual_goals / predicted_lambda

    if key not in biases:
        biases[key] = {"ema": round(ratio, 4), "n": 1, "updated": time.strftime("%Y-%m-%d")}
    else:
        alpha = 0.3
        biases[key]["ema"] = round(alpha * ratio + (1 - alpha) * biases[key]["ema"], 4)
        biases[key]["n"]  += 1
        biases[key]["updated"] = time.strftime("%Y-%m-%d")

    _save_json(_TEAM_BIAS_PATH, biases)


def get_team_goal_bias(team: str) -> float:
    """
    返回队伍进球偏差修正因子。
    < 3 场数据时返回 1.0（不修正，防止过拟合）。
    """
    biases = _load_json(_TEAM_BIAS_PATH, {})
    entry  = biases.get(team.lower().strip(), {})
    if entry.get("n", 0) < 3:
        return 1.0
    bias = entry.get("ema", 1.0)
    # 限制修正幅度，防止极端值
    return round(max(0.70, min(bias, 1.50)), 4)


def get_all_team_biases() -> Dict[str, Dict]:
    """返回所有有数据的队伍偏差记录。"""
    return _load_json(_TEAM_BIAS_PATH, {})


# ── 3b. λ 偏差：基于比分 MAE 的精化校准 ──────────────────────────────────────

def optimize_lambda_bias_from_scores(settled_records: List[Dict]) -> Dict:
    """
    用实际比分优化 λ_home / λ_away 全局缩放因子，最小化总进球 MAE。

    与 optimize_lambda_bias 的区别：
      - 后者用 actual/predicted 比值的均值（易受极端值拉偏）
      - 本函数网格搜索 bias_h × bias_a，最小化
          mean(|bias_h * lambda_h - actual_h| + |bias_a * lambda_a - actual_a|)
    需要 ≥ 8 条含实际进球记录才运行。

    返回 {"home_bias": ..., "away_bias": ..., "goals_mae": ..., "n": ..., "status": ...}
    """
    rows = [
        r for r in settled_records
        if r.get("actual_home_goals") is not None
        and r.get("actual_away_goals") is not None
        and r.get("lambda_home") and r.get("lambda_away")
    ]
    if len(rows) < 8:
        return {"home_bias": 1.0, "away_bias": 1.0, "status": "not_enough_data", "n": len(rows)}

    best_mae = float("inf")
    best_bh, best_ba = 1.0, 1.0

    for bh in [0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20]:
        for ba in [0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15]:
            total = 0.0
            for r in rows:
                pred_h = float(r["lambda_home"]) * bh
                pred_a = float(r["lambda_away"]) * ba
                total += abs(pred_h - float(r["actual_home_goals"]))
                total += abs(pred_a - float(r["actual_away_goals"]))
            mae = total / (2 * len(rows))
            if mae < best_mae:
                best_mae, best_bh, best_ba = mae, bh, ba

    return {
        "home_bias":  round(best_bh, 3),
        "away_bias":  round(best_ba, 3),
        "goals_mae":  round(best_mae, 4),
        "n":          len(rows),
        "status":     "optimized",
    }


# ── 4. 参数读写 ───────────────────────────────────────────────────────────────

def get_calibrated_params() -> Dict:
    """读取已保存的校准参数，不存在则返回默认值。"""
    saved = _load_json(_PARAMS_PATH, {})
    return {**_DEFAULT_PARAMS, **saved}


def save_calibration(params: Dict) -> None:
    """保存校准结果（追加 timestamp）。"""
    params["calibrated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = _load_json(_PARAMS_PATH, {})
    existing.update(params)
    _save_json(_PARAMS_PATH, existing)


def calibration_summary() -> str:
    """打印可读的当前校准状态。"""
    p = get_calibrated_params()
    biases = get_all_team_biases()
    lines = [
        f"校准时间: {p.get('calibrated_at', '未校准')}",
        f"攻击斜率: a1={p['a1']}  a2={p['a2']}",
        f"防守斜率: d1={p['d1']}  d2={p['d2']}",
        f"λ 偏差:   主队 ×{p['lambda_home_bias']}  客队 ×{p['lambda_away_bias']}",
        f"平均 Brier: {p.get('avg_brier', 'N/A')}",
        f"样本场次: {p.get('n_matches', 0)}",
        "",
        "队伍进球偏差 (≥3场数据):",
    ]
    for team, d in sorted(biases.items(), key=lambda x: -x[1].get("n", 0)):
        if d.get("n", 0) >= 3:
            lines.append(f"  {team:<20} EMA={d['ema']:.3f}  n={d['n']}")
    return "\n".join(lines)
