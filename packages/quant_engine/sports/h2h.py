"""
sports/h2h.py — 历史交锋 (Head-to-Head) 分析与调整
===================================================
分析两队历史对阵记录，提供调整系数。

理论依据：
  某些队伍存在"心理/战术克制"效应，超出 Elo 差距能解释的范围。
  H2H 调整系数在实际市场定价中通常权重约 8-12%。

调整范围：
  h2h_advantage: -0.08 ~ +0.08（相对于主队/队1）
  代入预测公式：lambda_home *= (1 + h2h_advantage)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


def analyze_h2h(
    matches: List[Dict],
    team1: str,
    team2: str,
    max_matches: int = 10,
    decay: float = 0.90,
) -> Dict:
    """
    分析两队历史交锋记录。

    Args:
        matches:     比赛记录列表（football-data.org 格式）
        team1:       队1名称（通常是"主队"或查询方）
        team2:       队2名称
        max_matches: 最多分析 N 场
        decay:       时间衰减系数

    Returns:
        {
            "total_matches": int,
            "team1_wins": int,
            "draws": int,
            "team2_wins": int,
            "team1_goals": int,
            "team2_goals": int,
            "h2h_advantage": float,   # team1 相对优势 (-0.08 ~ +0.08)
            "win_rate_team1": float,
            "dominant_team": str,
            "summary": str,
        }
    """
    if not matches:
        return _neutral_h2h(team1, team2)

    t1_low = team1.lower()
    t2_low = team2.lower()

    results = []
    for m in matches[:max_matches]:
        ht = (m.get("homeTeam") or {}).get("name", "").lower()
        at = (m.get("awayTeam") or {}).get("name", "").lower()
        ft = m.get("score", {}).get("fullTime", {})
        hg = ft.get("home")
        ag = ft.get("away")
        if hg is None or ag is None:
            continue

        t1_is_home = t1_low in ht
        if t1_is_home:
            t1g, t2g = int(hg), int(ag)
        else:
            t1g, t2g = int(ag), int(hg)

        results.append({
            "t1_goals": t1g, "t2_goals": t2g,
            "date": m.get("utcDate", ""),
        })

    if not results:
        return _neutral_h2h(team1, team2)

    results.sort(key=lambda x: x.get("date", ""), reverse=True)

    total_w   = 0.0
    t1_win_w  = 0.0
    draw_w    = 0.0
    t2_win_w  = 0.0
    t1_goals  = 0
    t2_goals  = 0
    t1_wins = draws = t2_wins = 0

    for i, r in enumerate(results):
        w = decay ** i
        total_w += w
        t1g, t2g = r["t1_goals"], r["t2_goals"]
        t1_goals += t1g
        t2_goals += t2g
        if t1g > t2g:
            t1_win_w += w
            t1_wins += 1
        elif t1g == t2g:
            draw_w += w
            draws += 1
        else:
            t2_win_w += w
            t2_wins += 1

    if total_w <= 0:
        return _neutral_h2h(team1, team2)

    t1_wr = t1_win_w / total_w
    t2_wr = t2_win_w / total_w

    # H2H 优势调整系数：偏离0.5的部分映射到 ±0.08
    # t1_wr=1.0 → +0.08, t1_wr=0.5 → 0.0, t1_wr=0.0 → -0.08
    h2h_adv = (t1_wr - 0.5) * 0.16
    h2h_adv = max(-0.08, min(h2h_adv, 0.08))

    n = len(results)
    if t1_wins > t2_wins + 1:
        dominant = team1
    elif t2_wins > t1_wins + 1:
        dominant = team2
    else:
        dominant = "平分秋色"

    summary = (
        f"{team1} {t1_wins}胜 {draws}平 {t2_wins}负 "
        f"({t1_goals}:{t2_goals}) "
        f"近{n}场"
    )

    return {
        "total_matches":  n,
        "team1_wins":     t1_wins,
        "draws":          draws,
        "team2_wins":     t2_wins,
        "team1_goals":    t1_goals,
        "team2_goals":    t2_goals,
        "h2h_advantage":  round(h2h_adv, 4),
        "win_rate_team1": round(t1_wr, 3),
        "dominant_team":  dominant,
        "summary":        summary,
    }


def _neutral_h2h(team1: str, team2: str) -> Dict:
    return {
        "total_matches":  0,
        "team1_wins":     0,
        "draws":          0,
        "team2_wins":     0,
        "team1_goals":    0,
        "team2_goals":    0,
        "h2h_advantage":  0.0,
        "win_rate_team1": 0.5,
        "dominant_team":  "数据不足",
        "summary":        f"{team1} vs {team2} — 无历史数据",
    }


def fetch_h2h_from_api(
    home_team: str,
    away_team: str,
    api_get_fn,
    limit: int = 10,
) -> List[Dict]:
    """
    从 football-data.org API 获取历史交锋数据（需要 API key）。
    api_get_fn: 封装好的 GET 函数，如 football_data_client._get()
    """
    try:
        data = api_get_fn("/teams", {"name": home_team})
        if not data:
            return []
        teams = data.get("teams", [])
        if not teams:
            return []
        team_id = teams[0]["id"]

        h2h_data = api_get_fn(f"/teams/{team_id}/matches", {
            "competitions": "WC,CL,PL,BL1,SA,FL1,PD",
            "limit": str(limit),
        })
        if not h2h_data:
            return []
        matches = h2h_data.get("matches", [])
        at_low = away_team.lower()
        return [
            m for m in matches
            if at_low in (m.get("homeTeam") or {}).get("name", "").lower()
            or at_low in (m.get("awayTeam") or {}).get("name", "").lower()
        ]
    except Exception:
        return []
