"""
sports/tracker.py — 预测追踪、准确率统计、自动 Elo 同步
=========================================================
功能:
  1. 记录每次预测（赛前调用 record_prediction）
     — 自动计算并存储比分概率矩阵 top_scorelines + predicted_score
  2. 赛后记录实际结果，自动计算:
     — 1X2 Brier Score / Log-Loss
     — 比分精确命中 score_exact / 比分排名 score_rank
     — 比分 RPS（Ranked Probability Score over scoreline space）
     — 进球 MAE（预期进球 vs 实际进球的平均绝对误差）
  3. 从 football-data.org API 自动同步已结束 WC 比赛 Elo
  4. 动态计算赛事实际场均进球（替换硬编码 1.32）
  5. backfill_score_metrics() — 为历史无比分指标的记录补算

持久化路径:
  ~/.arthera/football_predictions.json  — 预测记录
  ~/.arthera/elo_synced_matches.json    — 已同步比赛 ID（防重复）
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_PRED_PATH   = Path.home() / ".arthera" / "football_predictions.json"
_SYNCED_PATH = Path.home() / ".arthera" / "elo_synced_matches.json"
_AVG_PATH    = Path.home() / ".arthera" / "wc_league_avg.json"

_SCORE_MAX_G = 9   # 评估矩阵上限：0..8 进球


# ── 比分概率工具 ──────────────────────────────────────────────────────────────

def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _scoreline_matrix(lh: float, la: float, max_g: int = _SCORE_MAX_G) -> Dict[Tuple[int, int], float]:
    """全比分概率矩阵 {(home_goals, away_goals): prob}，0..max_g-1 进球。"""
    matrix: Dict[Tuple[int, int], float] = {}
    for h in range(max_g):
        ph = _poisson_pmf(h, lh)
        for a in range(max_g):
            matrix[(h, a)] = ph * _poisson_pmf(a, la)
    return matrix


def _top_scorelines(lh: float, la: float, n: int = 10) -> List[Dict]:
    """返回概率最高的 n 个比分，含排名。"""
    matrix = _scoreline_matrix(lh, la)
    ranked = sorted(matrix.items(), key=lambda x: -x[1])[:n]
    return [
        {"score": f"{h}-{a}", "h": h, "a": a, "prob": round(p * 100, 2), "rank": i + 1}
        for i, ((h, a), p) in enumerate(ranked)
    ]


def _score_rps(matrix: Dict[Tuple[int, int], float], actual_h: int, actual_a: int) -> float:
    """
    比分空间上的 Ranked Probability Score。

    在进球总数维度上累积 CDF 误差：
      RPS = mean_over_g( (F_pred(g) - F_actual(g))^2 )
    其中 F(g) = P(total_goals ≤ g)，范围 0..2*(max_g-1)。
    值域 [0, 1]，越低越好；完美预测 = 0。
    """
    max_total = 2 * (_SCORE_MAX_G - 1)
    actual_total = actual_h + actual_a

    # CDF for predicted total goals
    total_probs: Dict[int, float] = {}
    for (h, a), p in matrix.items():
        t = h + a
        total_probs[t] = total_probs.get(t, 0.0) + p

    cum_pred = 0.0
    cum_actual = 0.0
    rps = 0.0
    for g in range(max_total + 1):
        cum_pred   += total_probs.get(g, 0.0)
        cum_actual += 1.0 if g == actual_total else 0.0
        rps += (cum_pred - cum_actual) ** 2

    return round(rps / (max_total + 1), 5)


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


# ── 1. 预测记录 ───────────────────────────────────────────────────────────────

def record_prediction(
    home_team: str,
    away_team: str,
    home_win: float,
    draw: float,
    away_win: float,
    match_date: str = "",
    competition: str = "WC",
    extra: Optional[Dict] = None,
) -> str:
    """
    赛前记录一次预测，返回 prediction_id。

    用法:
        pid = record_prediction("germany", "curacao", 0.714, 0.165, 0.121,
                                match_date="2026-06-14", competition="WC")
    """
    records = _load_json(_PRED_PATH, [])
    pid = f"{home_team}_vs_{away_team}_{match_date}".replace(" ", "_")
    entry = {
        "id":          pid,
        "home_team":   home_team,
        "away_team":   away_team,
        "home_win":    round(home_win, 4),
        "draw":        round(draw, 4),
        "away_win":    round(away_win, 4),
        "match_date":  match_date,
        "competition": competition,
        "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result":      None,
        "brier_score": None,
        "log_loss":    None,
        **(extra or {}),
    }
    # Auto-compute scoreline distribution from lambdas if available
    lh = (extra or {}).get("lambda_home")
    la = (extra or {}).get("lambda_away")
    if lh and la and lh > 0 and la > 0:
        top = _top_scorelines(float(lh), float(la), n=10)
        entry["top_scorelines"]  = top
        entry["predicted_score"] = top[0]["score"] if top else None

    records = [r for r in records if r.get("id") != pid]
    records.append(entry)
    _save_json(_PRED_PATH, records)
    return pid


def record_result(
    prediction_id: str,
    actual_outcome: str,          # "home" | "draw" | "away"
    home_goals: Optional[int] = None,
    away_goals: Optional[int] = None,
) -> Optional[Dict]:
    """
    赛后填入实际结果，自动计算 Brier Score 和 Log-Loss。

    Brier Score = (p_home-1_home)² + (p_draw-1_draw)² + (p_away-1_away)²
    Log-Loss    = -log(p_actual)
    """
    records = _load_json(_PRED_PATH, [])
    for r in records:
        if r.get("id") == prediction_id:
            oc = actual_outcome.lower()
            i_home = 1.0 if oc == "home" else 0.0
            i_draw = 1.0 if oc == "draw" else 0.0
            i_away = 1.0 if oc == "away" else 0.0

            brier = (
                (r["home_win"] - i_home) ** 2
                + (r["draw"]     - i_draw) ** 2
                + (r["away_win"] - i_away) ** 2
            )
            p_act = r["home_win"] * i_home + r["draw"] * i_draw + r["away_win"] * i_away
            logloss = -math.log(max(p_act, 1e-7))

            r["result"]      = oc
            r["brier_score"] = round(brier, 4)
            r["log_loss"]    = round(logloss, 4)
            if home_goals is not None:
                r["actual_home_goals"] = home_goals
            if away_goals is not None:
                r["actual_away_goals"] = away_goals

            # ── 比分级别评估 ──────────────────────────────────────────────────
            if home_goals is not None and away_goals is not None:
                ah, aa = int(home_goals), int(away_goals)
                actual_score_str = f"{ah}-{aa}"

                # 1. 精确比分命中
                r["score_exact"] = (r.get("predicted_score") == actual_score_str)

                # 2. 比分排名（在存储的 top_scorelines 里的位置）
                top = r.get("top_scorelines", [])
                ranks = [s["rank"] for s in top if s["score"] == actual_score_str]
                r["score_rank"] = ranks[0] if ranks else None

                # 3. 用 lambda 重算完整矩阵 → RPS + 精确概率
                lh = r.get("lambda_home")
                la = r.get("lambda_away")
                if lh and la and lh > 0 and la > 0:
                    matrix = _scoreline_matrix(float(lh), float(la))
                    r["score_prob"]   = round(matrix.get((ah, aa), 0.0) * 100, 3)
                    r["score_rps"]    = _score_rps(matrix, ah, aa)
                    r["goals_mae"]    = round((abs(float(lh) - ah) + abs(float(la) - aa)) / 2, 3)
                    r["goals_mae_h"]  = round(abs(float(lh) - ah), 3)
                    r["goals_mae_a"]  = round(abs(float(la) - aa), 3)
                    # rank from full matrix (not just stored top 10)
                    ranked_all = sorted(matrix.items(), key=lambda x: -x[1])
                    for rank_idx, ((rh, ra), _) in enumerate(ranked_all):
                        if rh == ah and ra == aa:
                            r["score_rank_full"] = rank_idx + 1
                            break
                    else:
                        r["score_rank_full"] = len(ranked_all)

            _save_json(_PRED_PATH, records)
            return r
    return None


def get_accuracy_stats() -> Dict:
    """返回所有已结算预测的准确率统计（含比分级别指标）。"""
    records = _load_json(_PRED_PATH, [])
    settled = [r for r in records if r.get("result") and r.get("brier_score") is not None]
    if not settled:
        return {"total": 0, "message": "暂无已结算预测"}

    correct = sum(
        1 for r in settled
        if (r["result"] == "home" and r["home_win"] >= max(r["draw"], r["away_win"]))
        or (r["result"] == "draw" and r["draw"] >= max(r["home_win"], r["away_win"]))
        or (r["result"] == "away" and r["away_win"] >= max(r["home_win"], r["draw"]))
    )
    avg_brier   = sum(r["brier_score"] for r in settled) / len(settled)
    avg_logloss = sum(r["log_loss"]    for r in settled) / len(settled)

    stats: Dict = {
        "total":           len(settled),
        "correct":         correct,
        "accuracy":        round(correct / len(settled), 3),
        "avg_brier_score": round(avg_brier, 4),
        "avg_log_loss":    round(avg_logloss, 4),
    }

    # ── 比分级别统计 ──────────────────────────────────────────────────────────
    has_score = [r for r in settled if r.get("actual_home_goals") is not None and r.get("score_rps") is not None]
    if has_score:
        exact_hits     = [r for r in has_score if r.get("score_exact")]
        rank_vals      = [r["score_rank_full"] for r in has_score if r.get("score_rank_full")]
        rps_vals       = [r["score_rps"]       for r in has_score if r.get("score_rps") is not None]
        mae_vals       = [r["goals_mae"]        for r in has_score if r.get("goals_mae") is not None]
        mae_h_vals     = [r["goals_mae_h"]      for r in has_score if r.get("goals_mae_h") is not None]
        mae_a_vals     = [r["goals_mae_a"]      for r in has_score if r.get("goals_mae_a") is not None]
        prob_vals      = [r["score_prob"]        for r in has_score if r.get("score_prob") is not None]

        stats["score"] = {
            "total_with_score":    len(has_score),
            "exact_hits":          len(exact_hits),
            "exact_rate":          round(len(exact_hits) / len(has_score), 3),
            "avg_score_rank":      round(sum(rank_vals) / len(rank_vals), 1) if rank_vals else None,
            "avg_score_rps":       round(sum(rps_vals)  / len(rps_vals),  5) if rps_vals  else None,
            "avg_goals_mae":       round(sum(mae_vals)   / len(mae_vals),  3) if mae_vals  else None,
            "avg_goals_mae_home":  round(sum(mae_h_vals) / len(mae_h_vals), 3) if mae_h_vals else None,
            "avg_goals_mae_away":  round(sum(mae_a_vals) / len(mae_a_vals), 3) if mae_a_vals else None,
            "avg_score_prob_pct":  round(sum(prob_vals)  / len(prob_vals),  3) if prob_vals  else None,
        }

    stats["records"] = settled
    return stats


# ── 历史记录比分指标补算 ──────────────────────────────────────────────────────

def backfill_score_metrics() -> Dict:
    """
    为历史已结算但缺少比分指标的记录补算:
      top_scorelines, predicted_score, score_exact, score_rank,
      score_rps, goals_mae, score_rank_full, score_prob

    幂等：已有完整指标的记录跳过。
    返回 {"updated": n, "skipped": n}。
    """
    records = _load_json(_PRED_PATH, [])
    updated = 0

    for r in records:
        lh = r.get("lambda_home")
        la = r.get("lambda_away")
        if not (lh and la and lh > 0 and la > 0):
            continue

        # 补 top_scorelines / predicted_score
        if not r.get("top_scorelines"):
            top = _top_scorelines(float(lh), float(la), n=10)
            r["top_scorelines"]  = top
            r["predicted_score"] = top[0]["score"] if top else None
            updated += 1

        # 补比分评估（仅对已有实际进球的记录）
        ah = r.get("actual_home_goals")
        aa = r.get("actual_away_goals")
        if ah is None or aa is None:
            continue
        if r.get("score_rps") is not None:
            continue  # 已有，跳过

        ah, aa = int(ah), int(aa)
        actual_score_str = f"{ah}-{aa}"
        top = r.get("top_scorelines", [])
        r["score_exact"] = (r.get("predicted_score") == actual_score_str)
        ranks = [s["rank"] for s in top if s["score"] == actual_score_str]
        r["score_rank"] = ranks[0] if ranks else None

        matrix = _scoreline_matrix(float(lh), float(la))
        r["score_prob"]   = round(matrix.get((ah, aa), 0.0) * 100, 3)
        r["score_rps"]    = _score_rps(matrix, ah, aa)
        r["goals_mae"]    = round((abs(float(lh) - ah) + abs(float(la) - aa)) / 2, 3)
        r["goals_mae_h"]  = round(abs(float(lh) - ah), 3)
        r["goals_mae_a"]  = round(abs(float(la) - aa), 3)

        ranked_all = sorted(matrix.items(), key=lambda x: -x[1])
        for rank_idx, ((rh, ra), _) in enumerate(ranked_all):
            if rh == ah and ra == aa:
                r["score_rank_full"] = rank_idx + 1
                break
        else:
            r["score_rank_full"] = len(ranked_all)

        updated += 1

    _save_json(_PRED_PATH, records)
    return {"updated": updated, "skipped": len(records) - updated}


# ── 队名规范化（结算匹配用）──────────────────────────────────────────────────
# football-data.org 的官方队名与预测记录里的简称/中文常常不一致，
# 例如 API "Cape Verde Islands" vs 预测 "cape verde"，导致结算 ID 对不上，
# 模型永远学不到这场比赛。统一规范化后按「身份」匹配，而非字符串 ID。

_TEAM_ALIASES = {
    "cape verde islands": "cape verde",
    "korea republic": "south korea",
    "korea dpr": "north korea",
    "ir iran": "iran",
    "iran islamic republic": "iran",
    "cote d'ivoire": "ivory coast",
    "côte d'ivoire": "ivory coast",
    "usa": "united states",
    "united states of america": "united states",
    "curaçao": "curacao",
    "türkiye": "turkey",
    "turkiye": "turkey",
    "czech republic": "czechia",
    "china pr": "china",
}


def _canon(name: str) -> str:
    """Canonical team key: lowercase, alias-resolved, spaces→underscore."""
    s = (name or "").strip().lower().replace("_", " ")
    s = " ".join(s.split())
    s = _TEAM_ALIASES.get(s, s)
    return s.replace(" ", "_")


def _find_pred_record(records: List[Dict], home: str, away: str, date: str) -> Optional[Dict]:
    """Match a prediction record by canonical (home, away, date) identity.

    Tolerant of name variants (API vs short name vs alias) so name suffixes
    like 'Cape Verde Islands' settle against a 'cape verde' prediction.
    """
    ch, ca = _canon(home), _canon(away)
    for r in records:
        if r.get("match_date") != date:
            continue
        if _canon(r.get("home_team", "")) == ch and _canon(r.get("away_team", "")) == ca:
            return r
    return None


# ── 2. 自动 Elo 同步 ──────────────────────────────────────────────────────────

def sync_elo_from_wc(api_get_fn, competition_code: str = "WC") -> Dict:
    """
    从 football-data.org API 同步已结束的 WC 比赛到 Elo 系统。
    防重复处理：已同步的 match_id 记录在 elo_synced_matches.json。

    api_get_fn: football_data_client._get
    返回: {"synced": int, "skipped": int, "details": [...]}
    """
    from .elo import get_elo

    synced_ids: List[int] = _load_json(_SYNCED_PATH, [])
    synced_set = set(synced_ids)

    data = api_get_fn(f"/competitions/{competition_code}/matches", {"status": "FINISHED"})
    if not data:
        return {"synced": 0, "skipped": 0, "error": "API 无响应"}

    matches = data.get("matches", [])
    elo = get_elo()
    pred_records = _load_json(_PRED_PATH, [])
    results = []
    new_synced = 0
    newly_settled = 0

    for m in matches:
        mid = m.get("id")
        ht_name = m.get("homeTeam", {}).get("name", "").lower()
        at_name = m.get("awayTeam", {}).get("name", "").lower()
        ft = m.get("score", {}).get("fullTime", {})
        hg = ft.get("home")
        ag = ft.get("away")
        stage = m.get("stage", "GROUP_STAGE").lower()

        if hg is None or ag is None:
            continue

        winner = "home" if hg > ag else ("draw" if hg == ag else "away")
        date_str = m.get("utcDate", "")[:10]

        # ── Elo 更新：只对新比赛执行（防重复计分）──────────────────────────
        if mid not in synced_set:
            match_type = _stage_to_match_type(stage)
            da, db = elo.update(
                ht_name, at_name,
                int(hg), int(ag),
                match_type=match_type,
                neutral_venue=True,
            )
            synced_ids.append(mid)
            synced_set.add(mid)
            new_synced += 1
            results.append({
                "match":   f"{ht_name} {hg}-{ag} {at_name}",
                "type":    match_type,
                "elo_chg": f"{ht_name} {da:+.1f} / {at_name} {db:+.1f}",
            })

        # ── 结算预测：对所有已结束比赛执行（幂等），按规范化身份匹配 ────────
        # 与 Elo 同步解耦，确保名称变体（cape verde / cape verde islands）也能
        # 结算，否则模型永远学不到这些「大冷门」。
        rec = _find_pred_record(pred_records, ht_name, at_name, date_str)
        if rec and rec.get("result") is None:
            settled = record_result(rec["id"], winner, home_goals=int(hg), away_goals=int(ag))
            if settled:
                newly_settled += 1
                try:
                    from .calibrator import update_team_goal_bias
                    if settled.get("lambda_home"):
                        update_team_goal_bias(ht_name, settled["lambda_home"], int(hg))
                    if settled.get("lambda_away"):
                        update_team_goal_bias(at_name, settled["lambda_away"], int(ag))
                except Exception:
                    pass

    elo.save()
    _save_json(_SYNCED_PATH, synced_ids)

    return {
        "synced":        new_synced,
        "newly_settled": newly_settled,
        "skipped":       len(matches) - new_synced,
        "details":       results,
    }


def auto_calibrate(api_get_fn=None) -> Dict:
    """
    自动校准主函数 — 在 sync_elo_from_wc 后调用。

    触发条件（累积满足即执行）:
      ≥ 5  场已结算 → λ 偏差修正
      ≥ 10 场已结算 → 攻防斜率网格搜索
      有 api_get_fn  → ρ 重新校准

    返回校准报告 dict。
    """
    from .calibrator import (
        optimize_lambda_bias,
        optimize_slopes_from_outcomes,
        optimize_confidence_temp,
        save_calibration,
        get_calibrated_params,
    )

    records  = _load_json(_PRED_PATH, [])
    settled  = [r for r in records if r.get("result") and r.get("brier_score") is not None]
    n        = len(settled)
    report: Dict = {"settled_count": n, "actions": []}

    if n < 5:
        report["status"]  = "waiting"
        report["message"] = f"数据不足，当前 {n} 场（需 ≥5 场触发校准）"
        return report

    params = get_calibrated_params()

    # ── 队伍进球偏差更新（遍历所有已结算含实际进球的记录）────────────────────
    try:
        from .calibrator import update_team_goal_bias
        for r in settled:
            if r.get("actual_home_goals") is not None and r.get("lambda_home"):
                update_team_goal_bias(r["home_team"], r["lambda_home"], r["actual_home_goals"])
            if r.get("actual_away_goals") is not None and r.get("lambda_away"):
                update_team_goal_bias(r["away_team"], r["lambda_away"], r["actual_away_goals"])
    except Exception:
        pass

    # ── λ 偏差修正（双路径：比值法 + MAE 网格搜索，取 MAE 更优者）────────────
    bias = optimize_lambda_bias(settled)
    try:
        from .calibrator import optimize_lambda_bias_from_scores as _score_bias_fn
        score_bias = _score_bias_fn(settled)
    except Exception:
        score_bias = {"status": "error"}

    if score_bias.get("status") == "optimized":
        # 使用 MAE 网格搜索结果（更稳健，对极端比分不敏感）
        params["lambda_home_bias"] = score_bias["home_bias"]
        params["lambda_away_bias"] = score_bias["away_bias"]
        params["n_matches"]        = n
        report["actions"].append(
            f"λ 偏差(MAE网格): 主队×{score_bias['home_bias']:.3f}  "
            f"客队×{score_bias['away_bias']:.3f}  "
            f"goals_MAE={score_bias['goals_mae']:.3f}(n={score_bias['n']})"
        )
    elif bias["n_home"] >= 5 or bias["n_away"] >= 5:
        params["lambda_home_bias"] = bias["home_bias"]
        params["lambda_away_bias"] = bias["away_bias"]
        params["n_matches"]        = n
        report["actions"].append(
            f"λ 偏差(比值法): 主队×{bias['home_bias']:.3f}(n={bias['n_home']})  "
            f"客队×{bias['away_bias']:.3f}(n={bias['n_away']})"
        )

    # ── 斜率网格搜索（≥10 场）────────────────────────────────────────────────
    if n >= 10:
        slopes = optimize_slopes_from_outcomes(settled)
        if slopes.get("status") == "optimized":
            prev_brier = get_calibrated_params().get("avg_brier")
            new_brier  = slopes["avg_brier"]
            if prev_brier is None or new_brier < prev_brier - 0.002:
                params.update({k: slopes[k] for k in ("a1", "a2", "d1", "d2", "avg_brier")})
                report["actions"].append(
                    f"斜率优化: a1={slopes['a1']} a2={slopes['a2']}  "
                    f"Brier {prev_brier}→{new_brier}"
                )

    # ── 概率温度校准（对抗过度自信，≥8 场）──────────────────────────────────
    temp = optimize_confidence_temp(settled)
    if temp.get("status") == "optimized" and temp["temp"] < 1.0:
        params["conf_temp"] = temp["temp"]
        report["actions"].append(
            f"概率温度: ×{temp['temp']} "
            f"(Brier {temp['brier_before']}→{temp['brier_after']}, n={temp['n']})"
        )
    else:
        params.setdefault("conf_temp", 1.0)

    # ── ρ 重新校准 ────────────────────────────────────────────────────────────
    if api_get_fn:
        rho = fetch_wc_rho(api_get_fn)
        report["rho"] = rho
        report["actions"].append(f"ρ 校准: {rho:.3f}")

    save_calibration(params)
    report["status"]  = "ok"
    report["message"] = f"校准完成 ({n} 场数据)"
    report["params"]  = {k: params[k] for k in ("a1", "a2", "lambda_home_bias", "lambda_away_bias")}
    return report


def _stage_to_match_type(stage: str) -> str:
    if "final" in stage and "semi" not in stage and "quarter" not in stage:
        return "wc_final"
    if "semi" in stage:
        return "wc_semifinal"
    if "quarter" in stage:
        return "wc_quarterfinal"
    if "round_of_16" in stage or "r16" in stage:
        return "wc_r16"
    return "wc_group"


# ── 3. 动态场均进球 ───────────────────────────────────────────────────────────

_CACHE_TTL = 1800  # 30 min


def fetch_wc_rho(api_get_fn, competition_code: str = "WC") -> float:
    """
    从已结束 WC 比赛估计最优 ρ，保存到 ~/.arthera/wc_rho.json。
    需要 ≥20 场数据才校准，否则保持 -0.10。
    """
    from .dixon_coles import estimate_rho_from_results

    _RHO_PATH = Path.home() / ".arthera" / "wc_rho.json"
    cached = _load_json(_RHO_PATH, {})
    now = time.time()
    if cached.get("ts", 0) + _CACHE_TTL > now:
        return cached.get("rho", -0.10)

    data = api_get_fn(f"/competitions/{competition_code}/matches", {"status": "FINISHED"})
    if not data:
        return cached.get("rho", -0.10)

    results = []
    for m in data.get("matches", []):
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is not None and ag is not None:
            results.append((int(hg), int(ag)))

    if len(results) < 20:
        return -0.10

    rho = estimate_rho_from_results(results)
    _save_json(_RHO_PATH, {"rho": rho, "ts": now, "matches": len(results)})
    return rho


def fetch_wc_league_avg(api_get_fn, competition_code: str = "WC") -> float:
    """
    从 API 计算本届赛事实际场均进球，缓存 30 分钟。
    数据不足 5 场时返回默认值 1.35。
    """
    cached = _load_json(_AVG_PATH, {})
    now = time.time()
    if cached.get("ts", 0) + _CACHE_TTL > now:
        return cached.get("avg", 1.35)

    data = api_get_fn(f"/competitions/{competition_code}/matches", {"status": "FINISHED"})
    if not data:
        return cached.get("avg", 1.35)

    matches = data.get("matches", [])
    totals = []
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is not None and ag is not None:
            totals.append(hg + ag)

    if len(totals) < 5:
        return 1.35

    avg = sum(totals) / (len(totals) * 2)
    _save_json(_AVG_PATH, {"avg": round(avg, 3), "ts": now, "matches": len(totals)})
    return round(avg, 3)
