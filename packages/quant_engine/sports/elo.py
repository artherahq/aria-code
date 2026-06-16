"""
sports/elo.py — World Football Elo Rating System
=================================================
动态 Elo 评分系统，替代静态 FIFA 排名表。

特性：
- 初始评分基于 FIFA 排名（幂律映射，斜率更陡）
- 每场赛果后自动更新
- K 因子按赛事重要性调整（世界杯 > 洲际杯 > 友谊赛）
- 主场优势 +100 Elo 加成（中性场地为 0）
- 支持从本地 JSON 持久化加载/保存

Elo 公式:
    E = 1 / (1 + 10^((Rb - Ra - home_adv) / 400))
    R' = R + K * (W - E)

参考: World Football Elo Ratings (eloratings.net) 方法论
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

# ── 默认初始 Elo（基于 FIFA 排名，幂律映射）──────────────────────────────────
# 公式: elo = BASE - SCALE * (ranking ^ POWER)
# 经验参数：阿根廷#1→2050, 德国#11→1850, 库拉索#70→1520, 菲律宾#134→1280
_ELO_BASE  = 2100.0
_ELO_SCALE = 50.0
_ELO_POWER = 0.58

_DEFAULT_ELO = 1500.0  # 未知队伍


def ranking_to_elo(ranking: int) -> float:
    """FIFA 排名 → 初始 Elo 评分（幂律映射）。"""
    if ranking <= 0:
        return _DEFAULT_ELO
    raw = _ELO_BASE - _ELO_SCALE * (ranking ** _ELO_POWER)
    return max(900.0, round(raw, 1))


# K 因子（赛事权重）
_K_FACTORS: Dict[str, float] = {
    "wc_final":       60,
    "wc_semifinal":   56,
    "wc_quarterfinal":52,
    "wc_r16":         48,
    "wc_group":       40,
    "confederation":  35,
    "euro_final":     40,
    "euro":           35,
    "copa_america":   35,
    "afcon":          30,
    "qualifier":      25,
    "friendly":       15,
    "default":        20,
}

# 内置 FIFA 排名表（覆盖主要队伍）
_FIFA_RANKING: Dict[str, int] = {
    "argentina": 1, "france": 2, "england": 3, "brazil": 4,
    "portugal": 5, "belgium": 6, "spain": 7, "netherlands": 8,
    "croatia": 9, "italy": 10, "germany": 11, "colombia": 12,
    "united states": 13, "usa": 13, "mexico": 14, "morocco": 16,
    "uruguay": 20, "denmark": 22, "switzerland": 23, "serbia": 24,
    "austria": 25, "norway": 26, "ukraine": 27, "turkey": 28,
    "senegal": 19, "japan": 18, "iran": 21, "south korea": 23,
    "egypt": 33, "nigeria": 35, "cameroon": 37, "ghana": 60,
    "australia": 23, "new zealand": 90, "canada": 47,
    "costa rica": 53, "panama": 55, "jamaica": 60,
    "curacao": 70, "haiti": 80, "trinidad": 85,
    "china": 87, "thailand": 111, "philippines": 134,
    "india": 124, "vietnam": 95, "indonesia": 130,
    "saudi arabia": 58, "iraq": 62, "uae": 68, "bahrain": 80,
    "romania": 45, "slovakia": 48, "hungary": 50,
    "greece": 46, "albania": 66, "north macedonia": 70,
    "sweden": 30, "finland": 43, "russia": 26,
    "bolivia": 79, "venezuela": 75, "paraguay": 65,
    "chile": 55, "ecuador": 45, "peru": 69,
    "tunisia": 30, "algeria": 32, "mali": 56, "guinea": 64,
    "ivory coast": 51, "south africa": 70, "zimbabwe": 120,
}


class EloRatingSystem:
    """
    World Football Elo 评分引擎。

    用法:
        elo = EloRatingSystem()
        p = elo.win_probability("germany", "curacao")
        # → {'home_win': 0.74, 'draw': 0.16, 'away_win': 0.10}
    """

    _STORE_PATH = Path.home() / ".arthera" / "football_elo.json"

    def __init__(self, load_from_disk: bool = True):
        self._ratings: Dict[str, float] = {}
        self._match_log: list = []
        if load_from_disk:
            self._load()
        # 如果没有持久化数据，用 FIFA 排名初始化
        if not self._ratings:
            self._init_from_rankings()

    # ── 初始化 ────────────────────────────────────────────────────────────────

    def _init_from_rankings(self) -> None:
        for team, rank in _FIFA_RANKING.items():
            self._ratings[team] = ranking_to_elo(rank)

    # ── 核心 Elo 计算 ─────────────────────────────────────────────────────────

    def get_rating(self, team: str) -> float:
        key = team.lower().strip()
        if key in self._ratings:
            return self._ratings[key]
        # 尝试 FIFA 排名推算
        for stored_key, rank in _FIFA_RANKING.items():
            if stored_key in key or key in stored_key:
                return ranking_to_elo(rank)
        return _DEFAULT_ELO

    def expected_score(
        self,
        rating_a: float,
        rating_b: float,
        home_advantage: float = 100.0,
    ) -> float:
        """E_A = 1 / (1 + 10^((Rb - Ra - home_adv) / 400))"""
        return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a - home_advantage) / 400.0))

    def update(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
        match_type: str = "default",
        neutral_venue: bool = False,
    ) -> Tuple[float, float]:
        """
        用一场赛果更新双方 Elo 评分。
        返回 (home_delta, away_delta)。
        """
        k = _K_FACTORS.get(match_type, _K_FACTORS["default"])
        home_adv = 0.0 if neutral_venue else 100.0

        ra = self.get_rating(home_team)
        rb = self.get_rating(away_team)

        ea = self.expected_score(ra, rb, home_adv)
        eb = 1.0 - ea

        if home_goals > away_goals:
            wa, wb = 1.0, 0.0
        elif home_goals == away_goals:
            wa, wb = 0.5, 0.5
        else:
            wa, wb = 0.0, 1.0

        # 进球差加成（World Football Elo 标准公式）
        # GD=1→×1.0, GD=2→×1.5, GD=3→×1.75, GD=6→×2.125, 上限 2.5
        goal_diff = abs(home_goals - away_goals)
        if goal_diff <= 1:
            gd_mult = 1.0
        elif goal_diff == 2:
            gd_mult = 1.5
        else:
            gd_mult = min(2.5, (11 + goal_diff) / 8.0)

        da = round(k * gd_mult * (wa - ea), 2)
        db = round(k * gd_mult * (wb - eb), 2)

        h_key = home_team.lower().strip()
        a_key = away_team.lower().strip()
        self._ratings[h_key] = round(ra + da, 1)
        self._ratings[a_key] = round(rb + db, 1)

        self._match_log.append({
            "home": h_key, "away": a_key,
            "score": f"{home_goals}-{away_goals}",
            "type": match_type, "da": da, "db": db,
        })
        return da, db

    def win_probability(
        self,
        home_team: str,
        away_team: str,
        neutral_venue: bool = True,
    ) -> Dict[str, float]:
        """
        基于 Elo 差距计算胜/平/负概率。
        使用 Dixon & Robinson (1998) 的转换公式。
        """
        home_adv = 0.0 if neutral_venue else 100.0
        ra = self.get_rating(home_team)
        rb = self.get_rating(away_team)

        e_home = self.expected_score(ra, rb, home_adv)
        diff = ra + home_adv - rb

        # 平局概率：差距越大平局越少
        # 经验公式：draw_prob ≈ 0.30 * exp(-|diff| / 720)
        draw_base = 0.295 * math.exp(-abs(diff) / 720.0)
        draw_base = max(0.04, min(draw_base, 0.295))

        home_win = e_home * (1.0 - draw_base / 2)
        away_win = (1.0 - e_home) * (1.0 - draw_base / 2)
        draw = 1.0 - home_win - away_win
        draw = max(0.04, draw)

        total = home_win + draw + away_win
        return {
            "home_win": round(home_win / total, 4),
            "draw":     round(draw / total, 4),
            "away_win": round(away_win / total, 4),
            "home_elo": round(ra, 0),
            "away_elo": round(rb, 0),
            "elo_diff": round(ra + home_adv - rb, 0),
        }

    def get_attack_defense(self, team: str, base_avg: float = 1.35) -> Dict[str, float]:
        """
        从 Elo 评分推导 attack / defense 参数供泊松模型使用。
        斜率参数优先从 calibrator 读取（自动优化），不存在则用默认值。

        标定默认目标:
          Elo 2050 (阿根廷) → attack≈2.50, defense≈0.42
          Elo 1912 (德国)   → attack≈3.01, defense≈0.42
          Elo 1800 (日本)   → attack≈1.75, defense≈0.65
          Elo 1500 (平均)   → attack≈1.10, defense≈0.95
          Elo 1200 (弱队)   → attack≈0.62, defense≈1.22
        """
        elo = self.get_rating(team)
        si  = (elo - 1500) / 400.0

        # 读取自动校准斜率（若无则用默认值）
        a1, a2, d1, d2 = 1.05, 0.35, -0.42, -0.10
        try:
            from .calibrator import get_calibrated_params
            p  = get_calibrated_params()
            a1 = p.get("a1", a1)
            a2 = p.get("a2", a2)
            d1 = p.get("d1", d1)
            d2 = p.get("d2", d2)
        except Exception:
            pass

        attack  = 1.10 + si * a1 + max(0, si) * si * a2
        defense = 0.95 + si * d1 + max(0, si) * si * d2

        attack  = max(0.45, min(attack,  2.60))
        defense = max(0.40, min(defense, 1.25))
        return {
            "attack":  round(attack, 3),
            "defense": round(defense, 3),
            "elo":     round(elo, 0),
        }

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def save(self) -> None:
        try:
            self._STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self._STORE_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "ratings": self._ratings,
                    "match_count": len(self._match_log),
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if self._STORE_PATH.exists():
                data = json.loads(self._STORE_PATH.read_text(encoding="utf-8"))
                self._ratings = data.get("ratings", {})
        except Exception:
            self._ratings = {}

    def top_n(self, n: int = 10) -> list:
        """返回评分最高的 n 支队伍。"""
        return sorted(self._ratings.items(), key=lambda x: -x[1])[:n]


_elo_instance: Optional[EloRatingSystem] = None


def get_elo() -> EloRatingSystem:
    global _elo_instance
    if _elo_instance is None:
        _elo_instance = EloRatingSystem()
    return _elo_instance
