"""
sports/dixon_coles.py — Dixon-Coles 足球比分预测模型
=====================================================
实现 Dixon & Coles (1997) 经典论文的完整模型。

核心改进 vs 纯泊松：
1. τ (tau) 低比分修正：0-0 / 0-1 / 1-0 / 1-1 比分在现实中
   比独立泊松分布出现频率更高，DC 模型用相关性项修正。
2. ρ (rho) 参数：控制修正强度，典型值 -0.13 ~ -0.08。
3. 时间权重：近期比赛权重更高（可选）。

τ 修正矩阵:
    τ(x, y, λ, μ, ρ) =
        1 - λμρ         if x=0, y=0
        1 + λρ          if x=0, y=1
        1 + μρ          if x=1, y=0
        1 - ρ           if x=1, y=1
        1               otherwise

参考文献: Dixon, M.J. & Coles, S.G. (1997).
          "Modelling Association Football Scores and Inefficiencies in the
          Football Betting Market." Applied Statistics, 46(2), 265-280.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _nb_pmf(k: int, mu: float, r: float) -> float:
    """
    负二项分布 PMF，均值 mu，离散参数 r。

    方差 = mu + mu²/r  >  mu（泊松）
    当 r→∞ 时退化为泊松分布。
    r 越小，尾部越重（大比分概率越高）。
    """
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    p = r / (r + mu)
    log_pmf = (
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        + r * math.log(p)
        + k * math.log(1.0 - p)
    )
    return math.exp(log_pmf)


def _auto_dispersion(elo_diff: float) -> float:
    """
    根据 Elo 差距自动选择负二项离散参数 r。
    差距越大 → r 越小 → 尾部越重 → 大比分概率更高。

    elo_diff=0   → r=80  (近似泊松)
    elo_diff=200 → r=25
    elo_diff=387 → r=13  (德国 vs 库拉索)
    elo_diff=500 → r=10
    """
    r = max(6.0, 80.0 / (1.0 + abs(elo_diff) / 100.0))
    return round(r, 1)


def tau_correction(
    x: int,
    y: int,
    lambda_home: float,
    lambda_away: float,
    rho: float = -0.10,
) -> float:
    """
    Dixon-Coles τ 修正因子。

    rho < 0 意味着低比分（含 0 的）出现频率比独立泊松预测的更低，
    但 0-0 / 0-1 / 1-0 / 1-1 特别地有正相关（团队防守同进攻 jointly low）。
    """
    if x == 0 and y == 0:
        return 1.0 - lambda_home * lambda_away * rho
    if x == 0 and y == 1:
        return 1.0 + lambda_home * rho
    if x == 1 and y == 0:
        return 1.0 + lambda_away * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def predict_scoreline_matrix(
    lambda_home: float,
    lambda_away: float,
    rho: float = -0.10,
    max_goals: int = 10,
    elo_diff: float = 0.0,
) -> Dict[Tuple[int, int], float]:
    """
    计算所有比分的概率矩阵。

    当 |elo_diff| > 150 时自动切换到负二项分布以处理大比分场景，
    否则使用标准泊松分布（DC 修正）。

    Returns:
        dict: {(home_goals, away_goals): probability}，归一化为 1.0
    """
    use_nb = abs(elo_diff) > 150
    if use_nb:
        r_home = _auto_dispersion(elo_diff)
        r_away = _auto_dispersion(-elo_diff)

    raw: Dict[Tuple[int, int], float] = {}
    for hg in range(max_goals):
        ph = _nb_pmf(hg, lambda_home, r_home) if use_nb else _poisson_pmf(hg, lambda_home)
        for ag in range(max_goals):
            pa = _nb_pmf(ag, lambda_away, r_away) if use_nb else _poisson_pmf(ag, lambda_away)
            tau = tau_correction(hg, ag, lambda_home, lambda_away, rho)
            raw[(hg, ag)] = ph * pa * tau

    total = sum(raw.values())
    if total <= 0:
        return raw
    return {k: v / total for k, v in raw.items()}


def compute_match_probabilities(
    lambda_home: float,
    lambda_away: float,
    rho: float = -0.10,
    max_goals: int = 10,
    elo_diff: float = 0.0,
) -> Dict:
    """
    从期望进球计算比赛结果概率（含 D-C 修正）。

    Returns:
        {
            "home_win": float,
            "draw": float,
            "away_win": float,
            "btts": float,
            "over_2_5": float,
            "top_scorelines": [...],
            "score_matrix": {...},
        }
    """
    matrix = predict_scoreline_matrix(lambda_home, lambda_away, rho, max_goals, elo_diff)

    home_win = draw = away_win = 0.0
    btts = 0.0
    over_2_5 = 0.0

    for (hg, ag), p in matrix.items():
        if hg > ag:
            home_win += p
        elif hg == ag:
            draw += p
        else:
            away_win += p
        if hg > 0 and ag > 0:
            btts += p
        if hg + ag > 2:
            over_2_5 += p

    top_scores = sorted(matrix.items(), key=lambda x: -x[1])[:8]

    return {
        "home_win":  round(home_win, 4),
        "draw":      round(draw, 4),
        "away_win":  round(away_win, 4),
        "btts":      round(btts, 4),
        "over_2_5":  round(over_2_5, 4),
        "rho":       rho,
        "top_scorelines": [
            {"score": f"{hg}-{ag}", "prob": round(p * 100, 2)}
            for (hg, ag), p in top_scores
        ],
        "implied_odds": {
            "home": round(1 / home_win, 2) if home_win > 0.01 else 99,
            "draw": round(1 / draw, 2)     if draw > 0.01 else 99,
            "away": round(1 / away_win, 2) if away_win > 0.01 else 99,
        },
    }


def estimate_rho_from_results(match_results: List[Tuple[int, int]]) -> float:
    """
    从历史赛果估计最优 ρ 参数（简化版 MLE 搜索）。

    match_results: [(home_goals, away_goals), ...]
    返回: 最优 ρ（范围 -0.3 ~ 0.0）
    """
    if len(match_results) < 20:
        return -0.10  # 数据不足，用默认值

    def log_likelihood(rho: float) -> float:
        ll = 0.0
        for hg, ag in match_results:
            # 用平均进球作简化 λ
            lh = sum(h for h, _ in match_results) / len(match_results)
            la = sum(a for _, a in match_results) / len(match_results)
            p_h = _poisson_pmf(hg, lh)
            p_a = _poisson_pmf(ag, la)
            tau = tau_correction(hg, ag, lh, la, rho)
            p = p_h * p_a * tau
            if p > 0:
                ll += math.log(p)
        return ll

    best_rho = -0.10
    best_ll = float("-inf")
    for rho_int in range(-30, 1, 2):
        rho = rho_int / 100.0
        ll = log_likelihood(rho)
        if ll > best_ll:
            best_ll = ll
            best_rho = rho

    return round(best_rho, 3)


def format_dc_result(result: Dict, home_cn: str, away_cn: str) -> str:
    """格式化 Dixon-Coles 预测结果为 CLI 输出文本。"""
    lines = [
        f"  主队 {home_cn:<12} 获胜: {result['home_win']*100:5.1f}%   赔率: {result['implied_odds']['home']:5.2f}",
        f"  平局              {result['draw']*100:5.1f}%   赔率: {result['implied_odds']['draw']:5.2f}",
        f"  客队 {away_cn:<12} 获胜: {result['away_win']*100:5.1f}%   赔率: {result['implied_odds']['away']:5.2f}",
        "",
        f"  双方均进球 (BTTS): {result['btts']*100:.1f}%   大于2.5球: {result['over_2_5']*100:.1f}%",
    ]
    return "\n".join(lines)
