"""
football_data_client.py — 足球数据客户端
==========================================
数据源:
  - football-data.org  (免费 API key: FOOTBALL_DATA_API_KEY)
  - understat           (无需 key, xG 数据, pip install understat)
  - ESPN/Sofascore      (备用爬虫)

支持联赛: EPL / Bundesliga / La Liga / Serie A / Ligue 1 / Champions League

配置:
  ~/.aria/.env  或环境变量:
    FOOTBALL_DATA_API_KEY=your_free_key  # 从 football-data.org 免费注册获取
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.football-data.org/v4"
_REQ_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 min

# ── League aliases ─────────────────────────────────────────────────────────────

LEAGUE_IDS: Dict[str, str] = {
    "pl": "PL", "epl": "PL", "premierleague": "PL", "英超": "PL",
    "bl": "BL1", "bl1": "BL1", "bundesliga": "BL1", "德甲": "BL1",
    "pd": "PD", "laliga": "PD", "ll": "PD", "西甲": "PD",
    "sa": "SA", "seriea": "SA", "意甲": "SA",
    "fl1": "FL1", "ligue1": "FL1", "l1": "FL1", "法甲": "FL1",
    "cl": "CL", "ucl": "CL", "champions": "CL", "欧冠": "CL",
    "el": "EL", "europaleague": "EL", "欧联": "EL",
    "dl": "DED", "eredivisie": "DED", "荷甲": "DED",
    "ppl": "PPL", "primeiraliga": "PPL", "葡超": "PPL",
}

LEAGUE_NAMES: Dict[str, str] = {
    "PL":  "英超 Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "BL1": "德甲 Bundesliga 🇩🇪",
    "PD":  "西甲 La Liga 🇪🇸",
    "SA":  "意甲 Serie A 🇮🇹",
    "FL1": "法甲 Ligue 1 🇫🇷",
    "CL":  "欧冠 Champions League 🏆",
    "EL":  "欧联杯 Europa League",
    "DED": "荷甲 Eredivisie 🇳🇱",
    "PPL": "葡超 Primeira Liga 🇵🇹",
}


def _resolve_league(raw: str) -> str:
    """Normalize league alias to football-data.org competition code."""
    key = raw.lower().replace(" ", "").replace("-", "")
    return LEAGUE_IDS.get(key, raw.upper())


# ── HTTP helpers ───────────────────────────────────────────────────────────────

_WARNED_NO_KEY = False  # only warn once per process


def _load_football_key() -> str:
    """Load football-data.org API key from env, .env files, or providers.json."""
    key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not key:
        import pathlib as _pl
        # Check ~/.aria/.env and ~/.arthera/.env
        for env_file in [
            _pl.Path.home() / ".aria" / ".env",
            _pl.Path.home() / ".arthera" / ".env",
        ]:
            if env_file.exists():
                try:
                    for line in env_file.read_text(encoding="utf-8").splitlines():
                        if line.startswith("FOOTBALL_DATA_API_KEY="):
                            key = line.split("=", 1)[1].strip()
                            break
                except Exception:
                    pass
            if key:
                break
    if not key:
        try:
            import pathlib as _pl
            p = _pl.Path.home() / ".arthera" / "providers.json"
            if p.exists():
                raw = json.loads(p.read_text(encoding="utf-8"))
                data = raw.get("data", {})
                key = (
                    data.get("football_data", {}).get("api_key", "")
                    or data.get("footballdata", {}).get("api_key", "")
                    or data.get("football_data_org", {}).get("api_key", "")
                )
        except Exception:
            pass
    return key


def _get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """GET from football-data.org API with simple cache."""
    global _WARNED_NO_KEY
    api_key = _load_football_key()
    cache_key = path + json.dumps(params or {}, sort_keys=True)
    now = time.time()
    if cache_key in _REQ_CACHE:
        ts, data = _REQ_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            return data

    headers = {"X-Auth-Token": api_key} if api_key else {}
    try:
        resp = requests.get(
            f"{_API_BASE}{path}",
            headers=headers,
            params=params,
            timeout=10,
        )
        if resp.status_code == 403:
            if not _WARNED_NO_KEY:
                _WARNED_NO_KEY = True
                logger.info("football-data.org: 未配置 API key，使用 FIFA 排名估算（预测仍有效）。"
                            " 免费注册: https://www.football-data.org/client/register")
            return None
        if resp.status_code == 429:
            logger.warning("football-data.org: 请求过于频繁 (免费版 10次/分钟)")
            return None
        resp.raise_for_status()
        data = resp.json()
        _REQ_CACHE[cache_key] = (now, data)
        return data
    except Exception as exc:
        logger.warning("football-data.org request failed: %s", exc)
        return None


# football-data.org national team IDs (2026 WC participants)
_WC_TEAM_IDS: Dict[str, int] = {
    "algeria": 778, "argentina": 762, "australia": 779, "austria": 816,
    "belgium": 805, "bosnia-herzegovina": 1060, "brazil": 764, "canada": 828,
    "cape verde islands": 1930, "colombia": 818, "congo dr": 1934, "croatia": 799,
    "curacao": 9460, "czechia": 798, "ecuador": 791, "egypt": 825,
    "england": 770, "france": 773, "germany": 759, "ghana": 763,
    "haiti": 836, "iran": 840, "iraq": 8062, "ivory coast": 1935,
    "japan": 766, "jordan": 8049, "mexico": 769, "morocco": 815,
    "netherlands": 8601, "new zealand": 783, "norway": 8872, "panama": 1836,
    "paraguay": 761, "portugal": 765, "qatar": 8030, "saudi arabia": 801,
    "scotland": 8873, "senegal": 804, "south africa": 774, "south korea": 772,
    "spain": 760, "sweden": 792, "switzerland": 788, "tunisia": 802,
    "turkey": 803, "united states": 771, "uruguay": 758, "uzbekistan": 8070,
}


def _resolve_team_id(team_name: str) -> Optional[int]:
    """Resolve a team name to its football-data.org team ID."""
    low = team_name.lower().strip()
    # Normalize accented chars for lookup
    import unicodedata
    low = unicodedata.normalize("NFKD", low).encode("ascii", "ignore").decode()
    if low in _WC_TEAM_IDS:
        return _WC_TEAM_IDS[low]
    # Partial match
    for key, tid in _WC_TEAM_IDS.items():
        if low in key or key in low:
            return tid
    return None


def _fetch_team_form(team_name: str, limit: int = 6) -> List[Dict]:
    """Fetch recent finished matches for a team (requires API key)."""
    if not _load_football_key():
        return []
    try:
        team_id = _resolve_team_id(team_name)
        if not team_id:
            return []
        matches_data = _get(f"/teams/{team_id}/matches", {
            "status": "FINISHED",
            "limit": str(limit),
        })
        if not matches_data:
            return []
        return matches_data.get("matches", [])
    except Exception as exc:
        logger.debug("_fetch_team_form(%s) failed: %s", team_name, exc)
        return []


def _fetch_h2h(team1: str, team2: str, limit: int = 10) -> List[Dict]:
    """Fetch H2H finished matches between two teams (requires API key)."""
    if not _load_football_key():
        return []
    try:
        team_id = _resolve_team_id(team1)
        if not team_id:
            return []
        h2h_data = _get(f"/teams/{team_id}/matches", {
            "competitions": "WC,CL,PL,BL1,SA,FL1,PD,EC",
            "status": "FINISHED",
            "limit": "50",
        })
        if not h2h_data:
            return []
        t2_low = team2.lower()
        matches = h2h_data.get("matches", [])
        filtered = [
            m for m in matches
            if t2_low in (m.get("homeTeam") or {}).get("name", "").lower()
            or t2_low in (m.get("awayTeam") or {}).get("name", "").lower()
        ]
        return filtered[:limit]
    except Exception as exc:
        logger.debug("_fetch_h2h(%s, %s) failed: %s", team1, team2, exc)
        return []


# ── Public API ─────────────────────────────────────────────────────────────────

def get_standings(league: str) -> Optional[Dict]:
    """
    Return league standings table.
    league: "pl" / "bl" / "ll" / "sa" / "fl1" / "cl" / ...
    """
    comp = _resolve_league(league)
    data = _get(f"/competitions/{comp}/standings")
    if not data:
        return None

    standings = data.get("standings", [])
    total_table = next((s for s in standings if s.get("type") == "TOTAL"), None)
    if not total_table:
        total_table = standings[0] if standings else None
    if not total_table:
        return None

    rows = []
    for entry in total_table.get("table", []):
        rows.append({
            "pos":    entry.get("position"),
            "team":   entry.get("team", {}).get("name", ""),
            "played": entry.get("playedGames"),
            "w":      entry.get("won"),
            "d":      entry.get("draw"),
            "l":      entry.get("lost"),
            "gf":     entry.get("goalsFor"),
            "ga":     entry.get("goalsAgainst"),
            "gd":     entry.get("goalDifference"),
            "pts":    entry.get("points"),
            "form":   entry.get("form", ""),
        })

    comp_name = data.get("competition", {}).get("name", LEAGUE_NAMES.get(comp, comp))
    season = data.get("season", {})
    return {
        "league": comp,
        "league_name": comp_name,
        "season_start": season.get("startDate", ""),
        "season_end":   season.get("endDate", ""),
        "table": rows,
    }


def get_fixtures(league: str, days_ahead: int = 7) -> Optional[List[Dict]]:
    """
    Return upcoming fixtures within the next `days_ahead` days.
    """
    comp = _resolve_league(league)
    date_from = datetime.utcnow().strftime("%Y-%m-%d")
    date_to   = (datetime.utcnow() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    data = _get(f"/competitions/{comp}/matches", {
        "status":    "SCHEDULED",
        "dateFrom":  date_from,
        "dateTo":    date_to,
    })
    if not data:
        return None

    matches = []
    for m in data.get("matches", []):
        utc_str = m.get("utcDate", "")
        try:
            utc_dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
            local_str = utc_dt.strftime("%m-%d %H:%M")
        except Exception:
            local_str = utc_str[:16]
        matches.append({
            "id":       m.get("id"),
            "date":     local_str,
            "home":     m.get("homeTeam", {}).get("name", ""),
            "away":     m.get("awayTeam", {}).get("name", ""),
            "matchday": m.get("matchday"),
            "stage":    m.get("stage", ""),
        })
    return matches


def get_recent_results(league: str, team_name: str, n: int = 10) -> Optional[List[Dict]]:
    """
    Return last N finished matches for a specific team in a league.
    """
    comp = _resolve_league(league)
    data = _get(f"/competitions/{comp}/matches", {"status": "FINISHED"})
    if not data:
        return None

    team_lower = team_name.lower()
    results = []
    for m in reversed(data.get("matches", [])):
        ht = m.get("homeTeam", {}).get("name", "")
        at = m.get("awayTeam", {}).get("name", "")
        if team_lower not in ht.lower() and team_lower not in at.lower():
            continue
        score = m.get("score", {}).get("fullTime", {})
        hg = score.get("home")
        ag = score.get("away")
        if hg is None or ag is None:
            continue

        is_home = team_lower in ht.lower()
        gf = hg if is_home else ag
        ga = ag if is_home else hg
        if gf > ga:
            result = "W"
        elif gf < ga:
            result = "L"
        else:
            result = "D"

        results.append({
            "date":     m.get("utcDate", "")[:10],
            "home":     ht,
            "away":     at,
            "score":    f"{hg}-{ag}",
            "result":   result,
            "is_home":  is_home,
            "gf":       gf,
            "ga":       ga,
        })
        if len(results) >= n:
            break

    return results


def get_team_stats(league: str, team_name: str) -> Optional[Dict]:
    """Return aggregated stats for a team from recent matches."""
    results = get_recent_results(league, team_name, n=10)
    if not results:
        return None

    total = len(results)
    wins   = sum(1 for r in results if r["result"] == "W")
    draws  = sum(1 for r in results if r["result"] == "D")
    losses = sum(1 for r in results if r["result"] == "L")
    gf     = sum(r["gf"] for r in results)
    ga     = sum(r["ga"] for r in results)

    home_results = [r for r in results if r["is_home"]]
    away_results = [r for r in results if not r["is_home"]]

    return {
        "team":     team_name,
        "league":   league,
        "last_n":   total,
        "w": wins, "d": draws, "l": losses,
        "gf": gf, "ga": ga,
        "avg_gf": round(gf / total, 2) if total else 0,
        "avg_ga": round(ga / total, 2) if total else 0,
        "home_avg_gf": round(sum(r["gf"] for r in home_results) / len(home_results), 2) if home_results else 0,
        "away_avg_gf": round(sum(r["gf"] for r in away_results) / len(away_results), 2) if away_results else 0,
        "form": "".join(r["result"] for r in results[:5]),
        "recent": results[:5],
    }


# ── Poisson Match Predictor ───────────────────────────────────────────────────

def _poisson_pmf(k: int, lam: float) -> float:
    """P(X=k) where X ~ Poisson(lam)"""
    import math
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def predict_match(
    home_team: str,
    away_team: str,
    league: str,
    home_attack: Optional[float] = None,
    away_attack: Optional[float] = None,
    home_defense: Optional[float] = None,
    away_defense: Optional[float] = None,
    home_adv: float = 1.25,
) -> Dict:
    """
    Poisson-model match prediction.

    If attack/defense params are None, fetches recent form data to estimate them.
    Returns win/draw/loss probabilities + most likely scorelines.
    """
    # -- fetch stats if not provided
    if home_attack is None or away_attack is None:
        comp = _resolve_league(league)
        h_data = _get(f"/competitions/{comp}/matches", {"status": "FINISHED"})
        league_avg_gf = 1.5  # global fallback

        if h_data:
            all_matches = h_data.get("matches", [])
            if all_matches:
                total_goals = sum(
                    (m.get("score", {}).get("fullTime", {}).get("home") or 0) +
                    (m.get("score", {}).get("fullTime", {}).get("away") or 0)
                    for m in all_matches
                    if m.get("score", {}).get("fullTime", {}).get("home") is not None
                )
                finished = sum(
                    1 for m in all_matches
                    if m.get("score", {}).get("fullTime", {}).get("home") is not None
                )
                if finished:
                    league_avg_gf = total_goals / (finished * 2)

        ht = get_team_stats(league, home_team)
        at = get_team_stats(league, away_team)

        home_attack   = (ht["home_avg_gf"] if ht else league_avg_gf) / league_avg_gf
        away_attack   = (at["away_avg_gf"] if at else league_avg_gf) / league_avg_gf
        home_defense  = (ht["avg_ga"]      if ht else league_avg_gf) / league_avg_gf
        away_defense  = (at["avg_ga"]      if at else league_avg_gf) / league_avg_gf
    else:
        league_avg_gf = 1.5

    # -- expected goals
    lambda_home = home_attack * away_defense * home_adv * league_avg_gf
    lambda_away = away_attack * home_defense * league_avg_gf

    lambda_home = max(0.3, min(lambda_home, 6.0))
    lambda_away = max(0.3, min(lambda_away, 6.0))

    # -- scoreline matrix (0-7 goals each)
    max_goals = 8
    score_probs: Dict[Tuple[int, int], float] = {}
    home_win = draw = away_win = 0.0

    for hg in range(max_goals):
        ph = _poisson_pmf(hg, lambda_home)
        for ag in range(max_goals):
            pa = _poisson_pmf(ag, lambda_away)
            p = ph * pa
            score_probs[(hg, ag)] = p
            if hg > ag:
                home_win += p
            elif hg == ag:
                draw += p
            else:
                away_win += p

    # -- top 5 most likely scorelines
    top_scores = sorted(score_probs.items(), key=lambda x: -x[1])[:5]

    # -- most likely individual scores
    ml_home = round(lambda_home)
    ml_away = round(lambda_away)

    # -- 1X2 odds (implied, decimal)
    def implied_odds(p: float) -> float:
        return round(1 / p, 2) if p > 0.01 else 99.0

    # -- btts (both teams to score)
    btts = 1 - _poisson_pmf(0, lambda_home) - _poisson_pmf(0, lambda_away) + _poisson_pmf(0, lambda_home) * _poisson_pmf(0, lambda_away)

    return {
        "home_team": home_team,
        "away_team": away_team,
        "league":    league,
        "lambda_home": round(lambda_home, 2),
        "lambda_away": round(lambda_away, 2),
        "home_win":  round(home_win, 3),
        "draw":      round(draw, 3),
        "away_win":  round(away_win, 3),
        "btts":      round(btts, 3),
        "most_likely_score": f"{ml_home}-{ml_away}",
        "top_scorelines": [
            {"score": f"{hg}-{ag}", "prob": round(p * 100, 1)}
            for (hg, ag), p in top_scores
        ],
        "implied_odds": {
            "home": implied_odds(home_win),
            "draw": implied_odds(draw),
            "away": implied_odds(away_win),
        },
    }


# ── Head-to-head ──────────────────────────────────────────────────────────────

def get_head_to_head(team1: str, team2: str, league: str, limit: int = 10) -> Optional[Dict]:
    """Return head-to-head record between two teams."""
    comp = _resolve_league(league)
    data = _get(f"/competitions/{comp}/matches", {"status": "FINISHED"})
    if not data:
        return None

    t1 = team1.lower()
    t2 = team2.lower()
    h2h = []

    for m in reversed(data.get("matches", [])):
        ht = m.get("homeTeam", {}).get("name", "")
        at = m.get("awayTeam", {}).get("name", "")
        if not (
            (t1 in ht.lower() and t2 in at.lower()) or
            (t2 in ht.lower() and t1 in at.lower())
        ):
            continue
        score = m.get("score", {}).get("fullTime", {})
        hg = score.get("home")
        ag = score.get("away")
        if hg is None:
            continue
        h2h.append({
            "date":  m.get("utcDate", "")[:10],
            "home":  ht,
            "away":  at,
            "score": f"{hg}-{ag}",
        })
        if len(h2h) >= limit:
            break

    if not h2h:
        return None

    t1_wins = sum(
        1 for m in h2h
        if (t1 in m["home"].lower() and int(m["score"][0]) > int(m["score"][-1])) or
           (t1 in m["away"].lower() and int(m["score"][0]) < int(m["score"][-1]))
    )
    draws = sum(1 for m in h2h if m["score"][0] == m["score"][-1])
    t2_wins = len(h2h) - t1_wins - draws

    return {
        "team1": team1,
        "team2": team2,
        "total": len(h2h),
        "team1_wins": t1_wins,
        "draws": draws,
        "team2_wins": t2_wins,
        "matches": h2h,
    }


# ── Live scores & today's matches ────────────────────────────────────────────

def get_live_scores() -> Optional[List[Dict]]:
    """Return currently live matches across all competitions."""
    data = _get("/matches", {"status": "IN_PLAY,PAUSED"})
    if not data:
        return None
    return _format_match_list(data.get("matches", []), include_score=True)


def get_todays_matches() -> Optional[List[Dict]]:
    """Return all matches scheduled or played today (UTC)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    data = _get("/matches", {"dateFrom": today, "dateTo": today})
    if not data:
        return None
    return _format_match_list(data.get("matches", []), include_score=True)


def get_matches_by_date(date_str: str) -> Optional[List[Dict]]:
    """Return matches for a specific date (YYYY-MM-DD)."""
    data = _get("/matches", {"dateFrom": date_str, "dateTo": date_str})
    if not data:
        return None
    return _format_match_list(data.get("matches", []), include_score=True)


def _format_match_list(matches: List[Dict], include_score: bool = False) -> List[Dict]:
    """Normalize a list of raw API match objects."""
    result = []
    for m in matches:
        score = m.get("score", {})
        ft = score.get("fullTime", {})
        ht_s = score.get("halfTime", {})
        status = m.get("status", "")
        utc_str = m.get("utcDate", "")
        try:
            utc_dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
            time_str = utc_dt.strftime("%m-%d %H:%M")
        except Exception:
            time_str = utc_str[:16]

        entry = {
            "id":          m.get("id"),
            "competition": m.get("competition", {}).get("name", ""),
            "comp_code":   m.get("competition", {}).get("code", ""),
            "date":        time_str,
            "status":      status,
            "home":        m.get("homeTeam", {}).get("name", ""),
            "away":        m.get("awayTeam", {}).get("name", ""),
            "matchday":    m.get("matchday"),
            "stage":       m.get("stage", ""),
        }
        if include_score:
            hg = ft.get("home")
            ag = ft.get("away")
            if hg is not None and ag is not None:
                entry["score"] = f"{hg}-{ag}"
                entry["ht_score"] = f"{ht_s.get('home','-')}-{ht_s.get('away','-')}"
            else:
                entry["score"] = None
                entry["ht_score"] = None
        result.append(entry)
    return result


# ── World Cup / tournament helpers ────────────────────────────────────────────

# football-data.org competition codes for major tournaments
TOURNAMENT_CODES = {
    "wc": "WC", "worldcup": "WC", "世界杯": "WC", "fifa": "WC",
    "ec": "EC", "euro": "EC", "欧洲杯": "EC",
    "ca": "CA", "copaamerica": "CA", "美洲杯": "CA",
    "afc": "AFC", "亚洲杯": "AFC",
}


def get_tournament_matches(tournament: str = "WC", stage: Optional[str] = None) -> Optional[List[Dict]]:
    """
    Return all matches for a major tournament (World Cup, Euros, etc.).
    stage: 'GROUP_STAGE' / 'ROUND_OF_16' / 'QUARTER_FINAL' / 'SEMI_FINAL' / 'FINAL'
    """
    code = TOURNAMENT_CODES.get(tournament.lower().replace(" ", ""), tournament.upper())
    params: Dict[str, str] = {}
    if stage:
        params["stage"] = stage
    data = _get(f"/competitions/{code}/matches", params)
    if not data:
        return None
    return _format_match_list(data.get("matches", []), include_score=True)


def get_tournament_standings(tournament: str = "WC") -> Optional[Dict]:
    """Return group standings for a tournament."""
    code = TOURNAMENT_CODES.get(tournament.lower().replace(" ", ""), tournament.upper())
    data = _get(f"/competitions/{code}/standings")
    if not data:
        return None

    groups = {}
    for group in data.get("standings", []):
        gtype = group.get("type", "")
        if gtype not in ("HOME", "AWAY"):  # skip home/away splits
            g_name = group.get("group", gtype)
            rows = []
            for entry in group.get("table", []):
                rows.append({
                    "pos":    entry.get("position"),
                    "team":   entry.get("team", {}).get("name", ""),
                    "played": entry.get("playedGames"),
                    "w":      entry.get("won"),
                    "d":      entry.get("draw"),
                    "l":      entry.get("lost"),
                    "gf":     entry.get("goalsFor"),
                    "ga":     entry.get("goalsAgainst"),
                    "pts":    entry.get("points"),
                })
            groups[g_name] = rows

    return {
        "tournament": data.get("competition", {}).get("name", tournament.upper()),
        "groups": groups,
    }


def find_team_matches(tournament: str, team_name: str) -> Optional[List[Dict]]:
    """Find all matches for a specific team in a tournament."""
    all_matches = get_tournament_matches(tournament)
    if not all_matches:
        return None
    tlow = team_name.lower()
    return [m for m in all_matches
            if tlow in m.get("home", "").lower() or tlow in m.get("away", "").lower()]


# ── FIFA ranking-based national team strength table (WC 2026) ─────────────────
# attack = avg goals scored per game (league avg = 1.0)
# defense = avg goals conceded per game (lower = better, league avg = 1.0)
# ranking = FIFA world ranking (approximate, early 2026)
_FIFA_RATINGS: Dict[str, Dict] = {
    "argentina":            {"attack": 1.90, "defense": 0.62, "ranking": 1,  "name": "阿根廷"},
    "france":               {"attack": 1.85, "defense": 0.65, "ranking": 2,  "name": "法国"},
    "england":              {"attack": 1.78, "defense": 0.68, "ranking": 3,  "name": "英格兰"},
    "brazil":               {"attack": 1.82, "defense": 0.67, "ranking": 4,  "name": "巴西"},
    "portugal":             {"attack": 1.80, "defense": 0.70, "ranking": 5,  "name": "葡萄牙"},
    "belgium":              {"attack": 1.68, "defense": 0.72, "ranking": 6,  "name": "比利时"},
    "spain":                {"attack": 1.72, "defense": 0.68, "ranking": 7,  "name": "西班牙"},
    "netherlands":          {"attack": 1.65, "defense": 0.72, "ranking": 8,  "name": "荷兰"},
    "croatia":              {"attack": 1.55, "defense": 0.74, "ranking": 9,  "name": "克罗地亚"},
    "italy":                {"attack": 1.55, "defense": 0.72, "ranking": 10, "name": "意大利"},
    "germany":              {"attack": 1.68, "defense": 0.73, "ranking": 11, "name": "德国"},
    "united states":        {"attack": 1.52, "defense": 0.77, "ranking": 13, "name": "美国"},
    "usa":                  {"attack": 1.52, "defense": 0.77, "ranking": 13, "name": "美国"},
    "mexico":               {"attack": 1.45, "defense": 0.80, "ranking": 14, "name": "墨西哥"},
    "colombia":             {"attack": 1.55, "defense": 0.78, "ranking": 15, "name": "哥伦比亚"},
    "morocco":              {"attack": 1.42, "defense": 0.76, "ranking": 16, "name": "摩洛哥"},
    "senegal":              {"attack": 1.38, "defense": 0.78, "ranking": 19, "name": "塞内加尔"},
    "uruguay":              {"attack": 1.50, "defense": 0.77, "ranking": 20, "name": "乌拉圭"},
    "denmark":              {"attack": 1.48, "defense": 0.74, "ranking": 22, "name": "丹麦"},
    "switzerland":          {"attack": 1.45, "defense": 0.74, "ranking": 23, "name": "瑞士"},
    "serbia":               {"attack": 1.45, "defense": 0.77, "ranking": 24, "name": "塞尔维亚"},
    "austria":              {"attack": 1.42, "defense": 0.78, "ranking": 25, "name": "奥地利"},
    "ukraine":              {"attack": 1.40, "defense": 0.78, "ranking": 27, "name": "乌克兰"},
    "turkey":               {"attack": 1.38, "defense": 0.80, "ranking": 28, "name": "土耳其"},
    "czechia":              {"attack": 1.38, "defense": 0.79, "ranking": 29, "name": "捷克"},
    "czech republic":       {"attack": 1.38, "defense": 0.79, "ranking": 29, "name": "捷克"},
    "poland":               {"attack": 1.35, "defense": 0.81, "ranking": 31, "name": "波兰"},
    "chile":                {"attack": 1.35, "defense": 0.82, "ranking": 32, "name": "智利"},
    "japan":                {"attack": 1.37, "defense": 0.80, "ranking": 33, "name": "日本"},
    "south korea":          {"attack": 1.33, "defense": 0.81, "ranking": 35, "name": "韩国"},
    "australia":            {"attack": 1.30, "defense": 0.83, "ranking": 37, "name": "澳大利亚"},
    "hungary":              {"attack": 1.30, "defense": 0.82, "ranking": 38, "name": "匈牙利"},
    "canada":               {"attack": 1.30, "defense": 0.84, "ranking": 40, "name": "加拿大"},
    "nigeria":              {"attack": 1.30, "defense": 0.84, "ranking": 41, "name": "尼日利亚"},
    "peru":                 {"attack": 1.28, "defense": 0.83, "ranking": 43, "name": "秘鲁"},
    "ivory coast":          {"attack": 1.28, "defense": 0.84, "ranking": 44, "name": "科特迪瓦"},
    "venezuela":            {"attack": 1.25, "defense": 0.85, "ranking": 46, "name": "委内瑞拉"},
    "iran":                 {"attack": 1.25, "defense": 0.86, "ranking": 47, "name": "伊朗"},
    "ecuador":              {"attack": 1.35, "defense": 0.82, "ranking": 47, "name": "厄瓜多尔"},
    "saudi arabia":         {"attack": 1.25, "defense": 0.85, "ranking": 48, "name": "沙特"},
    "paraguay":             {"attack": 1.22, "defense": 0.86, "ranking": 54, "name": "巴拉圭"},
    "cameroon":             {"attack": 1.22, "defense": 0.87, "ranking": 52, "name": "喀麦隆"},
    "ghana":                {"attack": 1.20, "defense": 0.87, "ranking": 55, "name": "加纳"},
    "bosnia-herzegovina":   {"attack": 1.25, "defense": 0.84, "ranking": 58, "name": "波黑"},
    "bosnia and herzegovina": {"attack": 1.25, "defense": 0.84, "ranking": 58, "name": "波黑"},
    "bosnia":               {"attack": 1.25, "defense": 0.84, "ranking": 58, "name": "波黑"},
    "algeria":              {"attack": 1.22, "defense": 0.86, "ranking": 53, "name": "阿尔及利亚"},
    "south africa":         {"attack": 1.18, "defense": 0.88, "ranking": 60, "name": "南非"},
    "romania":              {"attack": 1.28, "defense": 0.83, "ranking": 44, "name": "罗马尼亚"},
    "slovakia":             {"attack": 1.25, "defense": 0.84, "ranking": 46, "name": "斯洛伐克"},
    "scotland":             {"attack": 1.28, "defense": 0.83, "ranking": 40, "name": "苏格兰"},
    "wales":                {"attack": 1.28, "defense": 0.84, "ranking": 41, "name": "威尔士"},
    "tunisia":              {"attack": 1.18, "defense": 0.88, "ranking": 62, "name": "突尼斯"},
    "iraq":                 {"attack": 1.20, "defense": 0.87, "ranking": 68, "name": "伊拉克"},
    "honduras":             {"attack": 1.12, "defense": 0.89, "ranking": 74, "name": "洪都拉斯"},
    "jamaica":              {"attack": 1.12, "defense": 0.89, "ranking": 72, "name": "牙买加"},
    "panama":               {"attack": 1.12, "defense": 0.89, "ranking": 73, "name": "巴拿马"},
    "costa rica":           {"attack": 1.15, "defense": 0.88, "ranking": 69, "name": "哥斯达黎加"},
    "bolivia":              {"attack": 1.12, "defense": 0.90, "ranking": 77, "name": "玻利维亚"},
    "new zealand":          {"attack": 1.10, "defense": 0.91, "ranking": 80, "name": "新西兰"},
    "qatar":                {"attack": 1.10, "defense": 0.90, "ranking": 82, "name": "卡塔尔"},
    "cuba":                 {"attack": 1.05, "defense": 0.93, "ranking": 95, "name": "古巴"},
    "curacao":              {"attack": 1.10, "defense": 0.90, "ranking": 70, "name": "库拉索"},
    "curaçao":              {"attack": 1.10, "defense": 0.90, "ranking": 70, "name": "库拉索"},
    "trinidad":             {"attack": 1.12, "defense": 0.90, "ranking": 75, "name": "特多"},
    "trinidad and tobago":  {"attack": 1.12, "defense": 0.90, "ranking": 75, "name": "特多"},
    "haiti":                {"attack": 1.08, "defense": 0.92, "ranking": 85, "name": "海地"},
    "guatemala":            {"attack": 1.10, "defense": 0.91, "ranking": 78, "name": "危地马拉"},
    "el salvador":          {"attack": 1.08, "defense": 0.92, "ranking": 82, "name": "萨尔瓦多"},
    "egypt":                {"attack": 1.30, "defense": 0.82, "ranking": 36, "name": "埃及"},
    "china":                {"attack": 1.08, "defense": 0.92, "ranking": 88, "name": "中国"},
    "china pr":             {"attack": 1.08, "defense": 0.92, "ranking": 88, "name": "中国"},
    "north korea":          {"attack": 1.05, "defense": 0.93, "ranking": 112, "name": "朝鲜"},
    "vietnam":              {"attack": 1.05, "defense": 0.93, "ranking": 116, "name": "越南"},
    "cape verde":           {"attack": 1.18, "defense": 0.87, "ranking": 62, "name": "佛得角"},
    "mali":                 {"attack": 1.22, "defense": 0.86, "ranking": 54, "name": "马里"},
    "uzbekistan":           {"attack": 1.25, "defense": 0.85, "ranking": 63, "name": "乌兹别克斯坦"},
    "philippines":          {"attack": 1.05, "defense": 0.93, "ranking": 134, "name": "菲律宾"},
    "thailand":             {"attack": 1.08, "defense": 0.92, "ranking": 111, "name": "泰国"},
    "norway":               {"attack": 1.42, "defense": 0.78, "ranking": 26, "name": "挪威"},
    "sweden":               {"attack": 1.38, "defense": 0.79, "ranking": 30, "name": "瑞典"},
    "finland":              {"attack": 1.28, "defense": 0.83, "ranking": 43, "name": "芬兰"},
    "greece":               {"attack": 1.30, "defense": 0.82, "ranking": 46, "name": "希腊"},
    "russia":               {"attack": 1.40, "defense": 0.79, "ranking": 26, "name": "俄罗斯"},
}

# WC 2026 host nations (slight home-field advantage)
_WC_HOST_NATIONS = {"united states", "usa", "canada", "mexico"}


def _find_fifa_rating(team_name: str) -> Optional[Dict]:
    """Fuzzy-match team_name against _FIFA_RATINGS dict."""
    low = team_name.lower().strip()
    # exact match first
    if low in _FIFA_RATINGS:
        return {**_FIFA_RATINGS[low], "key": low}
    # partial match
    for key, val in _FIFA_RATINGS.items():
        if low in key or key in low:
            return {**val, "key": key}
    return None


def predict_wc_match(
    home_team: str,
    away_team: str,
    neutral_venue: bool = True,
) -> Dict:
    """
    WC 2026 match prediction.
    优先使用 Elo + Dixon-Coles 引擎（packages/quant_engine/sports），
    若模块不可用则回落到原 FIFA 静态表 + 纯泊松预测。
    """
    import math

    # ── 优先使用新量化引擎 ─────────────────────────────────────────────────────
    try:
        from packages.quant_engine.sports.predictor import get_predictor
        from packages.quant_engine.sports.tracker import (
            sync_elo_from_wc, fetch_wc_league_avg,
            record_prediction, fetch_wc_rho, auto_calibrate,
        )

        # 赛前自动同步：更新 Elo + 动态场均进球 + 校准 ρ + 自动优化参数
        sync_result = sync_elo_from_wc(_get)
        league_avg  = fetch_wc_league_avg(_get)
        fetch_wc_rho(_get)
        if sync_result.get("synced", 0) > 0:
            auto_calibrate(_get)

        # 拉取真实 form/H2H 数据
        form_home_raw  = _fetch_team_form(home_team, limit=6)
        form_away_raw  = _fetch_team_form(away_team, limit=6)
        h2h_matches    = _fetch_h2h(home_team, away_team, limit=10)

        predictor = get_predictor()
        result = predictor.predict(
            home_team, away_team,
            league="wc",
            neutral_venue=neutral_venue,
            form_home=form_home_raw or None,
            form_away=form_away_raw or None,
            h2h_matches=h2h_matches or None,
            league_avg_override=league_avg,
        )

        # 记录本次预测（含 λ + Elo，供 calibrator 使用）
        try:
            import time as _t
            today = _t.strftime("%Y-%m-%d", _t.gmtime())
            record_prediction(
                home_team, away_team,
                result["home_win"], result["draw"], result["away_win"],
                match_date=today, competition="WC",
                extra={
                    "lambda_home": result.get("lambda_home"),
                    "lambda_away": result.get("lambda_away"),
                    "home_elo":    result.get("home_elo"),
                    "away_elo":    result.get("away_elo"),
                    "league_avg":  result.get("league_avg_goals"),
                },
            )
        except Exception:
            pass
        # 补充 format_prediction_block 需要的旧格式字段
        hr = _find_fifa_rating(home_team) or {}
        ar = _find_fifa_rating(away_team) or {}
        result.setdefault("home_name_cn", hr.get("name", home_team))
        result.setdefault("away_name_cn", ar.get("name", away_team))
        result.setdefault("home_ranking",  hr.get("ranking", "?"))
        result.setdefault("away_ranking",  ar.get("ranking", "?"))
        result.setdefault("calibrated_matches", 0)
        result.setdefault("home_adv", 1.0 if neutral_venue else 1.12)
        result["implied_odds"] = result.get("implied_odds", {
            "home": round(1/result["home_win"], 2) if result["home_win"] > 0.01 else 99,
            "draw": round(1/result["draw"], 2) if result["draw"] > 0.01 else 99,
            "away": round(1/result["away_win"], 2) if result["away_win"] > 0.01 else 99,
        })
        return result
    except Exception as _e:
        logger.debug(f"[predict_wc_match] 新引擎不可用，回落到原模型: {_e}")

    # ── 回落：原 FIFA 静态表 + 纯泊松 ─────────────────────────────────────────
    hr = _find_fifa_rating(home_team)
    ar = _find_fifa_rating(away_team)

    # Default to "average team" if not in table
    default_r = {"attack": 1.20, "defense": 0.87, "ranking": 70, "name": home_team, "key": home_team}
    if not hr:
        hr = {**default_r, "name": home_team, "key": home_team.lower()}
    if not ar:
        ar = {**default_r, "name": away_team, "key": away_team.lower()}

    # Try to calibrate from actual WC results if available
    wc_data = _get("/competitions/WC/matches", {"status": "FINISHED"})
    league_avg = 1.35  # WC tends to be lower scoring than club football
    wc_finished = []
    if wc_data:
        for m in wc_data.get("matches", []):
            ft = m.get("score", {}).get("fullTime", {})
            hg = ft.get("home")
            ag = ft.get("away")
            if hg is not None and ag is not None:
                wc_finished.append((hg, ag))
        if wc_finished:
            total_g = sum(h + a for h, a in wc_finished)
            league_avg = total_g / (len(wc_finished) * 2)

    # Home advantage: hosts get 1.12, neutral venue = 1.0
    home_key_low = hr["key"].lower()
    if neutral_venue and home_key_low not in _WC_HOST_NATIONS:
        home_adv = 1.0
    elif home_key_low in _WC_HOST_NATIONS:
        home_adv = 1.12
    else:
        home_adv = 1.18  # non-WC club match

    # Expected goals
    lambda_home = hr["attack"] * ar["defense"] * home_adv * league_avg
    lambda_away = ar["attack"] * hr["defense"] * league_avg

    lambda_home = max(0.3, min(lambda_home, 6.0))
    lambda_away = max(0.3, min(lambda_away, 6.0))

    # Scoreline matrix
    max_goals = 9
    score_probs: Dict = {}
    home_win = draw = away_win = 0.0

    for hg in range(max_goals):
        ph = _poisson_pmf(hg, lambda_home)
        for ag in range(max_goals):
            pa = _poisson_pmf(ag, lambda_away)
            p = ph * pa
            score_probs[(hg, ag)] = p
            if hg > ag:
                home_win += p
            elif hg == ag:
                draw += p
            else:
                away_win += p

    top_scores = sorted(score_probs.items(), key=lambda x: -x[1])[:6]
    btts = 1.0 - _poisson_pmf(0, lambda_home) - _poisson_pmf(0, lambda_away) + _poisson_pmf(0, lambda_home) * _poisson_pmf(0, lambda_away)

    def implied(p: float) -> float:
        return round(1 / p, 2) if p > 0.01 else 99.0

    return {
        "home_team":    home_team,
        "away_team":    away_team,
        "home_name_cn": hr.get("name", home_team),
        "away_name_cn": ar.get("name", away_team),
        "home_ranking": hr.get("ranking", "?"),
        "away_ranking": ar.get("ranking", "?"),
        "home_attack":  round(hr["attack"], 2),
        "away_attack":  round(ar["attack"], 2),
        "home_defense": round(hr["defense"], 2),
        "away_defense": round(ar["defense"], 2),
        "lambda_home":  round(lambda_home, 2),
        "lambda_away":  round(lambda_away, 2),
        "home_win":     round(home_win, 3),
        "draw":         round(draw, 3),
        "away_win":     round(away_win, 3),
        "btts":         round(btts, 3),
        "league_avg_goals": round(league_avg, 2),
        "calibrated_matches": len(wc_finished),
        "home_adv":     home_adv,
        "top_scorelines": [
            {"score": f"{hg}-{ag}", "prob": round(p * 100, 1)}
            for (hg, ag), p in top_scores
        ],
        "implied_odds": {
            "home": implied(home_win),
            "draw": implied(draw),
            "away": implied(away_win),
        },
    }


def format_prediction_block(pred: Dict, match_info: Optional[Dict] = None) -> str:
    """
    Format a prediction dict (from predict_wc_match or predict_match)
    into a rich text block suitable for LLM context injection.
    """
    ht = pred["home_team"]
    at = pred["away_team"]
    ht_cn = pred.get("home_name_cn", ht)
    at_cn = pred.get("away_name_cn", at)
    lh = pred["lambda_home"]
    la = pred["lambda_away"]
    hw = pred["home_win"]
    dr = pred["draw"]
    aw = pred["away_win"]
    bt = pred.get("btts", 0)

    lines = []
    if match_info:
        ts = match_info.get("date", "")
        status = match_info.get("status", "")
        stage = match_info.get("stage", "")
        score = match_info.get("score")
        score_str = f" **{score}**" if score else (" [已完赛]" if status == "FINISHED" else " [待开赛]")
        lines.append(f"\n【比赛信息】{ts} | {stage}")
        lines.append(f"  {ht} vs {at}{score_str}")

    # Display-width-aware padding for CJK names (each CJK char = 2 terminal columns)
    def _disp_width(s: str) -> int:
        w = 0
        for c in s:
            w += 2 if '一' <= c <= '鿿' or '　' <= c <= '〿' else 1
        return w

    def _short(name: str, maxlen: int = 6) -> str:
        out, w = "", 0
        for c in name:
            cw = 2 if '一' <= c <= '鿿' or '　' <= c <= '〿' else 1
            if w + cw > maxlen * 2:
                break
            out += c; w += cw
        return out

    def _pad(s: str, target_cols: int) -> str:
        return s + " " * max(0, target_cols - _disp_width(s))

    ht_s = _short(ht_cn)
    at_s = _short(at_cn)

    # ── 近期状态 & 数据完整度 ──────────────────────────────────────────────────
    home_form     = pred.get("home_form", "?????")
    away_form     = pred.get("away_form", "?????")
    home_momentum = pred.get("home_momentum", "stable")
    away_momentum = pred.get("away_momentum", "stable")
    _MOM = {"rising": "↑上升", "declining": "↓下滑", "stable": "→平稳"}

    has_form = home_form and home_form != "?????"
    has_h2h  = bool(pred.get("h2h_advantage", 0) or pred.get("total_matches", 0))

    # ── 模型标签 ──────────────────────────────────────────────────────────────
    model_tag = pred.get("model", "Dixon-Coles+Poisson")
    if not has_form and not has_h2h:
        # strip absent modules from tag to avoid misleading claim
        model_tag = "Elo+Dixon-Coles"

    lines.append(f"\n【量化预测 — {ht_cn} vs {at_cn}】")
    lines.append(f"  模型: {model_tag}")
    if not has_form:
        lines.append("  ⚠ 无近期战绩数据，预测基于 Elo 排名强度估算")
    lines.append("")

    # ── 队伍强度行（含 Elo）────────────────────────────────────────────────────
    hr_num   = pred.get("home_ranking", "?")
    ar_num   = pred.get("away_ranking", "?")
    h_elo    = pred.get("home_elo")
    a_elo    = pred.get("away_elo")
    elo_h    = f"  Elo {h_elo:.0f}" if h_elo else ""
    elo_a    = f"  Elo {a_elo:.0f}" if a_elo else ""

    def _fmt_val(v) -> str:
        try:
            return f"{float(v):.2f}"
        except Exception:
            return str(v)

    lines.append(f"  主队 {_pad(ht_cn, 12)}  进攻 {_fmt_val(pred.get('home_attack','?'))}  防守 {_fmt_val(pred.get('home_defense','?'))}  FIFA #{hr_num}{elo_h}")
    lines.append(f"  客队 {_pad(at_cn, 12)}  进攻 {_fmt_val(pred.get('away_attack','?'))}  防守 {_fmt_val(pred.get('away_defense','?'))}  FIFA #{ar_num}{elo_a}")
    lines.append(f"  预期进球: {ht_cn} {lh:.2f} | {at_cn} {la:.2f}  (赛事场均 {pred.get('league_avg_goals', 1.35):.2f} 球)")
    lines.append("")

    # ── 赔率框 ────────────────────────────────────────────────────────────────
    _COL = 14
    lines.append(f"  ┌{'─'*48}┐")
    lines.append(f"  │  {_pad(ht_s, _COL)}获胜: {hw*100:5.1f}%   赔率: {pred['implied_odds']['home']:5.2f}   │")
    lines.append(f"  │  {_pad('平局',  _COL)}     {dr*100:5.1f}%   赔率: {pred['implied_odds']['draw']:5.2f}   │")
    lines.append(f"  │  {_pad(at_s, _COL)}获胜: {aw*100:5.1f}%   赔率: {pred['implied_odds']['away']:5.2f}   │")
    lines.append(f"  └{'─'*48}┘")
    lines.append("")

    # ── 比分概率条形图 ────────────────────────────────────────────────────────
    lines.append("  最可能比分:")
    top_prob = max((sc["prob"] for sc in pred["top_scorelines"]), default=1)
    for sc in pred["top_scorelines"]:
        bar_len = max(1, round(sc["prob"] / top_prob * 14))
        bar = "▓" * bar_len
        lines.append(f"    {sc['score']}  ({sc['prob']:5.1f}%)  {bar}")
    lines.append("")

    o25 = pred.get("over_2_5", 0)
    lines.append(f"  双方均进球 (BTTS): {bt*100:.1f}%   进球超 2.5: {o25*100:.1f}%")

    # ── H2H ──────────────────────────────────────────────────────────────────
    h2h_summary = pred.get("h2h_summary", "")
    lines.append("")
    if h2h_summary and "无历史数据" not in h2h_summary:
        # Replace English team keys with CN names for consistent display
        h2h_disp = h2h_summary.replace(ht, ht_cn).replace(at, at_cn)
        lines.append(f"  历史对阵: {h2h_disp}")
    else:
        lines.append(f"  历史对阵: {ht_cn} vs {at_cn} — 暂无历史交锋记录")

    # ── 近期状态 ──────────────────────────────────────────────────────────────
    lines.append("")
    if has_form:
        mom_h = _MOM.get(home_momentum, "→平稳")
        lines.append(f"  {ht_cn} 近期状态: {home_form}  {mom_h}")
    else:
        lines.append(f"  {ht_cn} 近期状态: 暂无数据")

    away_form_real = away_form and away_form != "?????"
    if away_form_real:
        mom_a = _MOM.get(away_momentum, "→平稳")
        lines.append(f"  {at_cn} 近期状态: {away_form}  {mom_a}")
    else:
        lines.append(f"  {at_cn} 近期状态: 暂无数据")

    # ── 结论 ──────────────────────────────────────────────────────────────────
    gap = abs(hw - aw)
    top_sc = pred["top_scorelines"][0]["score"] if pred.get("top_scorelines") else "?"
    if hw > aw and gap > 0.40:
        outlook = f"{ht_cn} 强势主导（胜率 {hw*100:.0f}%），最可能比分 {top_sc}。"
    elif hw > aw and gap > 0.15:
        outlook = f"{ht_cn} 占据优势（胜率 {hw*100:.0f}%），但平局概率 {dr*100:.0f}% 不可忽视。"
    elif aw > hw and gap > 0.40:
        outlook = f"{at_cn} 强势主导（胜率 {aw*100:.0f}%），最可能比分 {top_sc}。"
    elif aw > hw and gap > 0.15:
        outlook = f"{at_cn} 占据优势（胜率 {aw*100:.0f}%），但平局概率 {dr*100:.0f}% 不可忽视。"
    elif hw > aw:
        outlook = f"{ht_cn} 微弱优势，平局概率 {dr*100:.0f}% 最高，双方实力接近。"
    elif aw > hw:
        outlook = f"{at_cn} 微弱优势，平局概率 {dr*100:.0f}% 最高，比赛走势难以预判。"
    else:
        outlook = f"双方实力相当，平局概率最高（{dr*100:.0f}%）。"

    data_note = ""
    if not has_form:
        data_note = "（配置 football-data.org API key 可获取近期战绩以提升精度）"
    lines.append(f"\n  【预测结论】{outlook}")
    if data_note:
        lines.append(f"  {data_note}")
    lines.append("  △ 注：量化预测基于历史统计规律，实际结果受伤病/临场状态等影响。")

    return "\n".join(lines)


# Chinese → English team name mapping for World Cup teams
_CN_TEAM_MAP: Dict[str, str] = {
    # 亚洲
    "卡塔尔": "qatar", "카타르": "qatar",
    "日本": "japan", "韩国": "south korea", "朝鲜": "north korea",
    "沙特": "saudi arabia", "沙特阿拉伯": "saudi arabia",
    "伊朗": "iran", "伊拉克": "iraq", "约旦": "jordan",
    "澳大利亚": "australia", "中国": "china", "中国队": "china",
    "越南": "vietnam", "泰国": "thailand", "印尼": "indonesia",
    "巴林": "bahrain", "阿联酋": "united arab emirates", "阿曼": "oman",
    "科威特": "kuwait", "叙利亚": "syria",
    # 欧洲
    "英格兰": "england", "法国": "france", "德国": "germany",
    "西班牙": "spain", "意大利": "italy", "葡萄牙": "portugal",
    "荷兰": "netherlands", "比利时": "belgium", "丹麦": "denmark",
    "波兰": "poland", "克罗地亚": "croatia", "瑞士": "switzerland",
    "乌克兰": "ukraine", "塞尔维亚": "serbia", "匈牙利": "hungary",
    "奥地利": "austria", "苏格兰": "scotland", "威尔士": "wales",
    "北爱尔兰": "northern ireland", "瑞典": "sweden", "挪威": "norway",
    "芬兰": "finland", "捷克": "czechia", "斯洛伐克": "slovakia",
    "罗马尼亚": "romania", "保加利亚": "bulgaria", "希腊": "greece",
    "土耳其": "turkey", "俄罗斯": "russia", "乌兹别克斯坦": "uzbekistan",
    # 北中美洲
    "美国": "united states", "加拿大": "canada", "墨西哥": "mexico",
    "哥斯达黎加": "costa rica", "巴拿马": "panama", "洪都拉斯": "honduras",
    "牙买加": "jamaica", "特立尼达": "trinidad",
    # 南美洲
    "阿根廷": "argentina", "巴西": "brazil", "乌拉圭": "uruguay",
    "哥伦比亚": "colombia", "厄瓜多尔": "ecuador", "智利": "chile",
    "秘鲁": "peru", "巴拉圭": "paraguay", "玻利维亚": "bolivia",
    "委内瑞拉": "venezuela",
    # 非洲
    "摩洛哥": "morocco", "塞内加尔": "senegal", "尼日利亚": "nigeria",
    "加纳": "ghana", "喀麦隆": "cameroon", "科特迪瓦": "ivory coast",
    "突尼斯": "tunisia", "埃及": "egypt", "阿尔及利亚": "algeria",
    "南非": "south africa", "马里": "mali", "布基纳法索": "burkina faso",
    # 大洋洲
    "新西兰": "new zealand",
    # 波黑
    "波黑": "bosnia", "波斯尼亚": "bosnia",
    # 加勒比海 / 中北美
    "库拉索": "curacao", "库拉索岛": "curacao", "库拉所": "curacao",
    "特多": "trinidad", "特立尼达和多巴哥": "trinidad",
    "海地": "haiti", "古巴": "cuba", "百慕大": "bermuda",
    "格林纳达": "grenada", "安提瓜": "antigua",
    "圭亚那": "guyana", "苏里南": "suriname",
    "危地马拉": "guatemala", "萨尔瓦多": "el salvador",
    "尼加拉瓜": "nicaragua", "伯利兹": "belize",
    # 非洲补充
    "刚果": "dr congo", "刚果金": "dr congo", "刚果河": "congo",
    "科摩罗": "comoros", "厄立特里亚": "eritrea",
    "莫桑比克": "mozambique", "津巴布韦": "zimbabwe",
    "赞比亚": "zambia", "坦桑尼亚": "tanzania",
    "肯尼亚": "kenya", "埃塞俄比亚": "ethiopia",
    "利比亚": "libya", "苏丹": "sudan",
    "几内亚": "guinea", "几内亚比绍": "guinea-bissau",
    "佛得角": "cape verde",
    # 亚洲补充
    "菲律宾": "philippines", "马来西亚": "malaysia",
    "新加坡": "singapore", "缅甸": "myanmar",
    "黎巴嫩": "lebanon", "约旦": "jordan",
    "吉尔吉斯": "kyrgyzstan", "塔吉克斯坦": "tajikistan",
}

# Words that appear in Chinese football queries but are NOT team names
_TEAM_EXTRACTION_STOPWORDS = frozenset({
    "分析", "比赛", "预测", "开球", "谁先", "以及", "足球", "世界杯",
    "欧冠", "英超", "德甲", "西甲", "意甲", "法甲", "结果", "比分",
    "情况", "今天", "今日", "明天", "明日", "本场", "这场", "哪队",
    "赢球", "进球", "胜利", "失败", "赔率", "胜率", "概率",
    "谁会", "谁能", "谁将", "先进", "先开", "获胜", "谁赢",
    "预计", "推测", "如何", "怎么", "怎样", "多少", "几比几",
    "the", "and", "vs", "for", "who", "will", "win", "score",
    "predict", "analysis", "analyze", "match", "game", "today",
})


def get_sports_context_for_query(query: str) -> str:
    """
    Auto-detect sports query intent and fetch relevant live data + auto-run
    Poisson quantitative prediction when a match prediction intent is detected.
    Returns a formatted context string for LLM injection.
    """
    low = query.lower()
    lines = []

    # Intent detection
    is_wc = any(k in low for k in ("世界杯", "world cup", "worldcup", "wc"))
    is_live = any(k in low for k in ("直播", "实时", "live", "今天", "今日", "现在"))
    is_predict = any(k in low for k in (
        "预测", "分析", "谁赢", "谁会赢", "谁能赢", "胜率", "概率",
        "比分", "结果", "赔率", "predict", "analysis", "analyze",
        "who wins", "who will win", "odds", "preview",
    ))

    # Extract team names — priority: _CN_TEAM_MAP exact matches, then tokenized remainder
    team_hints: List[str] = []
    # 1. Dictionary-based extraction (most reliable)
    for cn, en in _CN_TEAM_MAP.items():
        if cn in query:
            team_hints.append(en)

    # 2. Tokenize remaining text with comprehensive Chinese separators
    _sep_query = query
    for sep in ("跟", "和", "与", "对", "对阵", "对战", "vs", "VS", "Vs",
                "pk", "PK", "versus", "对决", " "):
        _sep_query = _sep_query.replace(sep, " ")

    # 动词前缀：这些词粘在队名前面需要剥离，如"分析德国"→"德国"
    _VERB_PREFIXES = (
        "分析", "预测", "查看", "研究", "看看", "帮我", "帮忙", "比较",
        "看下", "看一下", "告诉我", "请问", "analyze", "predict", "check",
    )
    _INLINE_STOPWORDS = ("预测", "比分", "开球", "以及", "足球", "谁", "赢", "的", "会")

    for word in _sep_query.split():
        clean = word.strip("？！，。、《》（）[]【】:：'\"的")
        if len(clean) < 2 or len(clean) > 20:
            continue

        # 剥离动词前缀，如"分析德国" → "德国"
        for vp in _VERB_PREFIXES:
            if clean.startswith(vp) and len(clean) > len(vp) + 1:
                _stripped = clean[len(vp):]
                # 只有剥离后剩余部分是已知队名或可识别词才替换
                if _stripped in _CN_TEAM_MAP or len(_stripped) >= 2:
                    clean = _stripped
                break

        # 救援逻辑：token 包含内联停用词（如"库拉索比分谁赢"），尝试提取队名
        rescued = False
        if any(sw in clean for sw in _INLINE_STOPWORDS):
            for cn, en in _CN_TEAM_MAP.items():
                # 支持队名在 token 开头或结尾
                if (clean.startswith(cn) or clean.endswith(cn)) and en not in team_hints:
                    team_hints.append(en)
                    rescued = True
                    break
            if not rescued:
                # 没有找到队名，但 clean 本身如果是已知队名就保留
                if clean in _CN_TEAM_MAP:
                    en = _CN_TEAM_MAP[clean]
                    if en not in team_hints:
                        team_hints.append(en)
                continue

        if clean.lower() in _TEAM_EXTRACTION_STOPWORDS:
            continue

        # 已被字典提取覆盖则跳过（避免重复以中文/英文两种形式出现）
        _en = _CN_TEAM_MAP.get(clean, "")
        if _en and _en in team_hints:
            continue

        if clean not in team_hints and clean.lower() not in team_hints:
            team_hints.append(_en or clean.lower())

    team_hints = list(dict.fromkeys(team_hints))  # deduplicate, preserve order

    if is_wc:
        wc_matches = get_tournament_matches("WC")
        match_info: Optional[Dict] = None

        if wc_matches:
            lines.append("【FIFA 世界杯 2026 赛事数据】")

            # Find matches for mentioned teams
            relevant = []
            for hint in team_hints:
                hint_low = hint.lower()
                for m in wc_matches:
                    if (hint_low in (m.get("home") or "").lower() or
                            hint_low in (m.get("away") or "").lower()):
                        if m not in relevant:
                            relevant.append(m)

            if not relevant:
                # Show recent results + next few upcoming
                recent = [m for m in wc_matches if m.get("score")][-5:]
                scheduled = [m for m in wc_matches if not m.get("score")][:5]
                relevant = recent + scheduled

            for m in relevant[:8]:
                score_str = f" **{m['score']}**" if m.get("score") else ""
                ht_str = f" (半场 {m['ht_score']})" if m.get("ht_score") and m.get("score") else ""
                lines.append(f"  {m['date']} | {m.get('stage','')} | "
                              f"{m['home']} vs {m['away']}{score_str}{ht_str} [{m['status']}]")

            # pick match_info for the prediction block (prefer the most relevant upcoming)
            if relevant:
                upcoming = [m for m in relevant if m.get("status") not in ("FINISHED",)]
                match_info = upcoming[0] if upcoming else relevant[0]

        else:
            lines.append("【世界杯数据】football-data.org 暂未开放此赛事的免费访问。")

        # --- Auto quantitative prediction ---
        if is_predict and len(team_hints) >= 2:
            # Find the two most likely teams from query
            api_home = team_hints[0]
            api_away = team_hints[1]

            # Try to get display names from match data
            display_home = api_home
            display_away = api_away
            if match_info:
                display_home = match_info.get("home", api_home)
                display_away = match_info.get("away", api_away)
                # Re-map so prediction uses the actual API name
                api_home = display_home
                api_away = display_away

            try:
                pred = predict_wc_match(api_home, api_away, neutral_venue=True)
                block = format_prediction_block(pred, match_info=match_info)
                lines.append(block)
            except Exception as exc:
                logger.warning("WC predict_wc_match failed: %s", exc)
                # Fallback: try with just FIFA rating keys
                try:
                    pred = predict_wc_match(team_hints[0], team_hints[1], neutral_venue=True)
                    lines.append(format_prediction_block(pred))
                except Exception:
                    pass

    elif is_live:
        live = get_live_scores()
        if live:
            lines.append(f"【实时比分 — {len(live)} 场进行中】")
            for m in live[:10]:
                lines.append(f"  {m['competition']} | {m['home']} {m.get('score','')} {m['away']} [{m['status']}]")
        else:
            today = get_todays_matches()
            if today:
                lines.append(f"【今日赛程 ({datetime.utcnow().strftime('%Y-%m-%d')})】")
                for m in today[:10]:
                    score_str = f" {m['score']}" if m.get("score") else ""
                    lines.append(f"  {m['competition']} | {m['home']} vs {m['away']}{score_str} [{m['status']}]")

    # ── Unconditional Poisson prediction when predict intent + 2 teams found ──
    # Runs even when "世界杯" is NOT in the query (covers "预测今天加拿大跟波黑" etc.)
    _already_has_pred = any("泊松模型量化预测" in l for l in lines)
    if is_predict and len(team_hints) >= 2 and not _already_has_pred:
        _t1, _t2 = team_hints[0], team_hints[1]
        if _find_fifa_rating(_t1) or _find_fifa_rating(_t2):
            try:
                pred = predict_wc_match(_t1, _t2, neutral_venue=True)
                lines.append(format_prediction_block(pred))
            except Exception as _exc:
                logger.warning("fallback predict_wc_match failed: %s", _exc)

    return "\n".join(lines)


# ── understat xG data (no API key) ────────────────────────────────────────────

async def get_xg_data(team: str, league_name: str = "EPL") -> Optional[Dict]:
    """
    Fetch xG data via understat (async). Requires: pip install understat
    league_name: EPL / La_liga / Bundesliga / Serie_A / Ligue_1 / RFPL
    """
    try:
        import understat
        async with understat.Understat() as us:
            league_map = {
                "epl": "EPL", "pl": "EPL", "英超": "EPL",
                "bundesliga": "Bundesliga", "bl": "Bundesliga", "德甲": "Bundesliga",
                "laliga": "La_liga", "pd": "La_liga", "西甲": "La_liga",
                "seriea": "Serie_A", "sa": "Serie_A", "意甲": "Serie_A",
                "ligue1": "Ligue_1", "fl1": "Ligue_1", "法甲": "Ligue_1",
            }
            ul = league_map.get(league_name.lower(), league_name)
            teams = await us.get_teams(ul, 2024)
            target = next((t for t in teams if team.lower() in t["title"].lower()), None)
            if not target:
                return None
            team_data = await us.get_team_results(target["id"], 2024)
            xg_list = [
                {
                    "date":    m.get("datetime", "")[:10],
                    "h":       m.get("h", {}).get("title"),
                    "a":       m.get("a", {}).get("title"),
                    "xg_h":    round(float(m.get("xG", {}).get("h", 0)), 2),
                    "xg_a":    round(float(m.get("xG", {}).get("a", 0)), 2),
                    "goals_h": m.get("goals", {}).get("h"),
                    "goals_a": m.get("goals", {}).get("a"),
                }
                for m in team_data[:10]
            ]
            avg_xg = round(sum(x["xg_h"] for x in xg_list) / len(xg_list), 2) if xg_list else None
            return {"team": team, "xg_matches": xg_list, "avg_xg_10": avg_xg}
    except ImportError:
        logger.info("understat not installed: pip install understat (xG数据不可用)")
        return None
    except Exception as exc:
        logger.warning("understat xG fetch failed: %s", exc)
        return None
