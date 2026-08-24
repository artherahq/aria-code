"""
sports/form.py — 球队近期状态分析
===================================
用指数衰减权重分析近 N 场比赛，
动态调整攻守参数，捕捉球队当前状态。

状态因子 (form_factor) 定义:
    - 近5场全胜 → 1.12（上调 12%）
    - 近5场全败 → 0.88（下调 12%）
    - 近5场持平 → 1.00
    - 中间值线性插值

影响方式:
    attack_adjusted  = attack_base  * form_factor_attack
    defense_adjusted = defense_base * form_factor_defense
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


def _decay_weight(match_index: int, total: int, decay: float = 0.85) -> float:
    """
    越近期的比赛权重越高。
    match_index=0 是最新一场，match_index=total-1 是最早一场。
    """
    return decay ** match_index


def analyze_form(
    results: List[Dict],
    n: int = 6,
    decay: float = 0.85,
) -> Dict:
    """
    分析球队近期状态。

    Args:
        results: 近期比赛结果列表，每项格式:
                 {"is_win": bool, "is_draw": bool, "goals_for": int,
                  "goals_against": int, "is_home": bool}
                 按时间倒序（最新在前）。
        n:       分析最近 n 场（默认6场）。
        decay:   指数衰减系数（0.85 → 最新权重是最早的 0.85^5 ≈ 44%）。

    Returns:
        {
            "form_string": "WWDLW",
            "weighted_win_rate": float,
            "form_factor_attack": float,
            "form_factor_defense": float,
            "avg_goals_for": float,
            "avg_goals_against": float,
            "momentum": str,  # "rising" | "declining" | "stable"
        }
    """
    recent = results[:n]
    if not recent:
        return _neutral_form()

    total_weight = 0.0
    weighted_wins = 0.0
    weighted_draws = 0.0
    weighted_gf = 0.0
    weighted_ga = 0.0
    form_chars = []

    for i, m in enumerate(recent):
        w = _decay_weight(i, len(recent), decay)
        total_weight += w
        if m.get("is_win"):
            weighted_wins += w
            form_chars.append("W")
        elif m.get("is_draw"):
            weighted_draws += w
            form_chars.append("D")
        else:
            form_chars.append("L")
        weighted_gf += w * m.get("goals_for", 1.0)
        weighted_ga += w * m.get("goals_against", 1.0)

    if total_weight <= 0:
        return _neutral_form()

    w_win_rate  = weighted_wins  / total_weight
    avg_gf      = weighted_gf    / total_weight
    avg_ga      = weighted_ga    / total_weight

    # 攻击状态因子：进球越多上调越多
    # 基准：1.35进球/场=1.0，每±0.35调整±0.08
    gf_baseline = 1.35
    form_attack = 1.0 + (avg_gf - gf_baseline) / gf_baseline * 0.12
    form_attack = max(0.82, min(form_attack, 1.18))

    # 防守状态因子：失球越少上调越多
    # 注意：defense 参数越小代表越强（对手进球少），失球少→factor下调（防守更好）
    ga_baseline = 1.20
    form_defense = 1.0 - (avg_ga - ga_baseline) / ga_baseline * 0.10
    form_defense = max(0.85, min(form_defense, 1.15))

    # 势头分析（对比前半段 vs 后半段胜率）
    half = max(1, len(recent) // 2)
    recent_half_wins  = sum(1 for m in recent[:half] if m.get("is_win"))
    earlier_half_wins = sum(1 for m in recent[half:] if m.get("is_win"))
    if recent_half_wins > earlier_half_wins:
        momentum = "rising"
    elif recent_half_wins < earlier_half_wins:
        momentum = "declining"
    else:
        momentum = "stable"

    return {
        "form_string":         "".join(form_chars),
        "weighted_win_rate":   round(w_win_rate, 3),
        "form_factor_attack":  round(form_attack, 3),
        "form_factor_defense": round(form_defense, 3),
        "avg_goals_for":       round(avg_gf, 2),
        "avg_goals_against":   round(avg_ga, 2),
        "momentum":            momentum,
        "matches_analyzed":    len(recent),
    }


def _neutral_form() -> Dict:
    return {
        "form_string":         "?????",
        "weighted_win_rate":   0.5,
        "form_factor_attack":  1.0,
        "form_factor_defense": 1.0,
        "avg_goals_for":       1.35,
        "avg_goals_against":   1.20,
        "momentum":            "stable",
        "matches_analyzed":    0,
    }


def parse_api_results(matches: List[Dict], team_name: str) -> List[Dict]:
    """
    将 football-data.org API 返回的比赛记录转换为 form 分析格式。

    team_name: 用于判断主客队角色（小写）
    """
    parsed = []
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        home_team = (m.get("homeTeam") or {}).get("name", "").lower()
        away_team = (m.get("awayTeam") or {}).get("name", "").lower()
        home_goals = ft.get("home")
        away_goals = ft.get("away")
        if home_goals is None or away_goals is None:
            continue

        team_low = team_name.lower()
        is_home = team_low in home_team

        if is_home:
            gf, ga = int(home_goals), int(away_goals)
        else:
            gf, ga = int(away_goals), int(home_goals)

        is_win  = gf > ga
        is_draw = gf == ga

        parsed.append({
            "is_win":          is_win,
            "is_draw":         is_draw,
            "is_home":         is_home,
            "goals_for":       gf,
            "goals_against":   ga,
            "date":            m.get("utcDate", ""),
        })

    # 按日期倒序（最新在前）
    parsed.sort(key=lambda x: x.get("date", ""), reverse=True)
    return parsed


def form_bar(form_string: str) -> str:
    """近期状态可视化条。"""
    _MAP = {"W": "●", "D": "◑", "L": "○"}
    return " ".join(_MAP.get(c, "?") for c in form_string[:6])


def momentum_label(momentum: str) -> str:
    _LABELS = {"rising": "↑ 上升", "declining": "↓ 下滑", "stable": "→ 平稳"}
    return _LABELS.get(momentum, "?")
