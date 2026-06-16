"""
sports/predictor.py — 统一足球比赛预测引擎 v2
==============================================
整合 Elo + Dixon-Coles(NB) + 近期状态 + H2H + 赛事情境 五个模块。

v2 改进:
  1. 负二项分布（大比分悬殊时自动启用，尾部更重）
  2. 动态 DC×Elo 混合权重（form 数据越充足 DC 权重越高）
  3. 赛事情境参数（必须赢/已出线保守/淘汰赛）
  4. 动态 WC 场均进球（从 tracker 实时获取）
  5. ρ 随赛果积累自动校准
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from .elo         import EloRatingSystem, get_elo
from .dixon_coles import compute_match_probabilities, estimate_rho_from_results
from .form        import analyze_form, parse_api_results
from .h2h         import analyze_h2h, _neutral_h2h


# ── 联赛场均进球（每队每场，后备默认值）──────────────────────────────────────
_LEAGUE_AVG_GOALS: Dict[str, float] = {
    "wc":       1.35, "euro":    1.20, "copa":   1.28,
    "pl":       1.51, "bl1":     1.56, "sa":     1.33,
    "pd":       1.34, "fl1":     1.43, "cl":     1.40,
    "friendly": 1.45, "default": 1.35,
}

# ── 赛事情境因子 ──────────────────────────────────────────────────────────────
_CONTEXT: Dict[str, Dict[str, float]] = {
    "normal":          {"lmult_h": 1.00, "lmult_a": 1.00, "draw_boost": 0.00},
    "must_win":        {"lmult_h": 1.10, "lmult_a": 0.95, "draw_boost": -0.04},
    "safe":            {"lmult_h": 0.88, "lmult_a": 0.88, "draw_boost":  0.06},
    "knockout":        {"lmult_h": 1.00, "lmult_a": 1.00, "draw_boost":  0.12},
    "knockout_attack": {"lmult_h": 1.08, "lmult_a": 1.00, "draw_boost":  0.05},
}


class FootballPredictor:
    """
    增强型足球比赛预测引擎 v2。

    用法:
        pred = FootballPredictor()
        result = pred.predict("germany", "curacao", league="wc",
                              tournament_context="normal")
    """

    def __init__(self, elo_system: Optional[EloRatingSystem] = None):
        self._elo = elo_system or get_elo()

    def predict(
        self,
        home_team: str,
        away_team: str,
        league: str = "default",
        neutral_venue: bool = True,
        form_home: Optional[List[Dict]] = None,
        form_away: Optional[List[Dict]] = None,
        h2h_matches: Optional[List[Dict]] = None,
        historical_results: Optional[List[Tuple[int, int]]] = None,
        tournament_context: str = "normal",
        league_avg_override: Optional[float] = None,
        home_attack_override: Optional[float] = None,
        away_attack_override: Optional[float] = None,
        home_defense_override: Optional[float] = None,
        away_defense_override: Optional[float] = None,
    ) -> Dict:
        """
        主预测函数。

        tournament_context:
          "normal"          — 小组赛正常（默认）
          "must_win"        — 必须赢（全力进攻）
          "safe"            — 已出线、可保守
          "knockout"        — 淘汰赛（平局→加时）
          "knockout_attack" — 淘汰赛落后方
        """
        # ── Step 0: 基础参数 ───────────────────────────────────────────────────
        league_key = league.lower().replace("-", "").replace("_", "")
        league_avg = league_avg_override or _LEAGUE_AVG_GOALS.get(
            league_key, _LEAGUE_AVG_GOALS["default"]
        )
        ctx = _CONTEXT.get(tournament_context, _CONTEXT["normal"])

        # ── Step 1: Elo → 攻防基础参数（二次曲线，更陡）─────────────────────
        home_stats = self._elo.get_attack_defense(home_team, league_avg)
        away_stats = self._elo.get_attack_defense(away_team, league_avg)

        h_attack  = home_attack_override  or home_stats["attack"]
        a_attack  = away_attack_override  or away_stats["attack"]
        h_defense = home_defense_override or home_stats["defense"]
        a_defense = away_defense_override or away_stats["defense"]

        home_elo  = home_stats["elo"]
        away_elo  = away_stats["elo"]
        elo_diff  = home_elo - away_elo

        # ── Step 2: 近期状态调整 ───────────────────────────────────────────────
        home_form = _neutral_form_dict()
        away_form = _neutral_form_dict()
        form_matches_h = 0
        form_matches_a = 0

        if form_home:
            parsed_h = parse_api_results(form_home, home_team)
            if parsed_h:
                home_form = analyze_form(parsed_h)
                form_matches_h = home_form.get("matches_analyzed", 0)
        if form_away:
            parsed_a = parse_api_results(form_away, away_team)
            if parsed_a:
                away_form = analyze_form(parsed_a)
                form_matches_a = away_form.get("matches_analyzed", 0)

        h_attack  *= home_form["form_factor_attack"]
        a_attack  *= away_form["form_factor_attack"]
        h_defense *= home_form["form_factor_defense"]
        a_defense *= away_form["form_factor_defense"]

        # ── Step 3: 主场优势 + 赛事情境 ───────────────────────────────────────
        home_adv_mult = 1.0 if neutral_venue else 1.12

        # ── Step 4: 期望进球 ───────────────────────────────────────────────────
        lambda_home = h_attack * a_defense * home_adv_mult * league_avg * ctx["lmult_h"]
        lambda_away = a_attack * h_defense * league_avg * ctx["lmult_a"]

        # H2H 微调（±8% 期望进球）
        h2h_result = _neutral_h2h(home_team, away_team)
        if h2h_matches:
            h2h_result = analyze_h2h(h2h_matches, home_team, away_team)
        h2h_adv = h2h_result.get("h2h_advantage", 0.0)
        lambda_home *= (1.0 + h2h_adv)
        lambda_away *= (1.0 - h2h_adv)

        # ── Step 4b: 自动校准修正 ──────────────────────────────────────────────
        # 全局 λ 偏差（实际进球 / 预测 λ 的历史 EMA）
        # 队伍专属进球偏差（≥3 场数据才生效）
        try:
            from .calibrator import get_calibrated_params, get_team_goal_bias
            cal = get_calibrated_params()
            lambda_home *= cal.get("lambda_home_bias", 1.0)
            lambda_away *= cal.get("lambda_away_bias", 1.0)
            lambda_home *= get_team_goal_bias(home_team)
            lambda_away *= get_team_goal_bias(away_team)
        except Exception:
            pass

        lambda_home = max(0.20, min(lambda_home, 8.0))
        lambda_away = max(0.20, min(lambda_away, 8.0))

        # ── Step 5: 动态 ρ 校准 ────────────────────────────────────────────────
        rho = _load_calibrated_rho()
        if historical_results and len(historical_results) >= 20:
            rho = estimate_rho_from_results(historical_results)

        # ── Step 6: Dixon-Coles（NB 自动启用于悬殊场次）──────────────────────
        dc_result = compute_match_probabilities(
            lambda_home, lambda_away, rho, elo_diff=elo_diff
        )

        # ── Step 7: Elo 概率混合（动态权重）──────────────────────────────────
        elo_probs = self._elo.win_probability(home_team, away_team, neutral_venue)

        # form 数据越充足，DC 权重越高；数据稀少时 Elo 权重更保守
        avg_form_matches = (form_matches_h + form_matches_a) / 2.0
        w_dc  = min(0.78, 0.55 + avg_form_matches * 0.04)
        w_elo = 1.0 - w_dc

        mix_home = dc_result["home_win"] * w_dc + elo_probs["home_win"] * w_elo
        mix_draw = dc_result["draw"]     * w_dc + elo_probs["draw"]     * w_elo
        mix_away = dc_result["away_win"] * w_dc + elo_probs["away_win"] * w_elo

        # 淘汰赛平局加成（反映加时/点球场景）
        draw_boost = ctx["draw_boost"]
        if draw_boost != 0:
            mix_draw = max(0.02, mix_draw + draw_boost)

        total = mix_home + mix_draw + mix_away
        mix_home /= total
        mix_draw /= total
        mix_away /= total

        # Raw (pre-temperature) probabilities — recorded for calibration so the
        # temperature optimizer never compounds an already-applied shrink.
        raw_home, raw_draw, raw_away = mix_home, mix_draw, mix_away

        # ── Step 8: 概率温度校准（收敛过度自信的预测）────────────────────────
        try:
            from .calibrator import get_confidence_temp, _apply_temp
            _temp = get_confidence_temp()
            if _temp != 1.0:
                mix_home, mix_draw, mix_away = _apply_temp(mix_home, mix_draw, mix_away, _temp)
        except Exception:
            pass

        def impl_odds(p: float) -> float:
            return round(1.0 / p, 2) if p > 0.01 else 99.0

        use_nb = abs(elo_diff) > 150
        model_tag = f"Elo+DC{'(NB)' if use_nb else ''}+Form+H2H"
        if draw_boost:
            model_tag += f"+{tournament_context}"

        return {
            "home_team":        home_team,
            "away_team":        away_team,
            "home_win":         round(mix_home, 4),
            "draw":             round(mix_draw, 4),
            "away_win":         round(mix_away, 4),
            "raw_home_win":     round(raw_home, 4),
            "raw_draw":         round(raw_draw, 4),
            "raw_away_win":     round(raw_away, 4),
            "btts":             dc_result["btts"],
            "over_2_5":         dc_result["over_2_5"],
            "lambda_home":      round(lambda_home, 2),
            "lambda_away":      round(lambda_away, 2),
            "league_avg_goals": round(league_avg, 2),
            "top_scorelines":   dc_result["top_scorelines"],
            "implied_odds": {
                "home": impl_odds(mix_home),
                "draw": impl_odds(mix_draw),
                "away": impl_odds(mix_away),
            },
            "home_elo":          home_elo,
            "away_elo":          away_elo,
            "elo_diff":          round(elo_diff, 0),
            "home_attack":       round(h_attack, 3),
            "away_attack":       round(a_attack, 3),
            "home_defense":      round(h_defense, 3),
            "away_defense":      round(a_defense, 3),
            "rho":               rho,
            "dc_home_win":       dc_result["home_win"],
            "dc_draw":           dc_result["draw"],
            "dc_away_win":       dc_result["away_win"],
            "elo_home_win":      elo_probs["home_win"],
            "elo_draw":          elo_probs["draw"],
            "elo_away_win":      elo_probs["away_win"],
            "home_form":         home_form.get("form_string", "?????"),
            "away_form":         away_form.get("form_string", "?????"),
            "home_momentum":     home_form.get("momentum", "stable"),
            "away_momentum":     away_form.get("momentum", "stable"),
            "h2h_summary":       h2h_result.get("summary", ""),
            "h2h_advantage":     h2h_adv,
            "w_dc":              round(w_dc, 2),
            "w_elo":             round(w_elo, 2),
            "use_nb":            use_nb,
            "tournament_context": tournament_context,
            "model":             model_tag,
        }


def _neutral_form_dict() -> Dict:
    return {
        "form_factor_attack":  1.0,
        "form_factor_defense": 1.0,
        "form_string":         "?????",
        "momentum":            "stable",
        "matches_analyzed":    0,
    }


def _load_calibrated_rho() -> float:
    """从 tracker 读取已校准的 ρ 值，不可用则返回默认 -0.10。"""
    try:
        from pathlib import Path
        import json
        p = Path.home() / ".arthera" / "wc_rho.json"
        if p.exists():
            d = json.loads(p.read_text())
            return d.get("rho", -0.10)
    except Exception:
        pass
    return -0.10


_predictor_instance: Optional[FootballPredictor] = None


def get_predictor() -> FootballPredictor:
    global _predictor_instance
    if _predictor_instance is None:
        _predictor_instance = FootballPredictor()
    return _predictor_instance


def quick_predict(
    home_team: str,
    away_team: str,
    league: str = "wc",
    neutral_venue: bool = True,
    tournament_context: str = "normal",
    league_avg_override: Optional[float] = None,
) -> Dict:
    """
    一行调用接口。

    示例:
        from packages.quant_engine.sports.predictor import quick_predict
        r = quick_predict("germany", "ivory coast", tournament_context="must_win")
        print(f"德国赢: {r['home_win']*100:.1f}%")
    """
    return get_predictor().predict(
        home_team, away_team, league, neutral_venue,
        tournament_context=tournament_context,
        league_avg_override=league_avg_override,
    )
