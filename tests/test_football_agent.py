"""FootballAgent must use the quant_engine predictor (Elo+Dixon-Coles), not
football_data_client.predict_match's static-table independent-Poisson model.

The two disagree materially — verified empirically before switching, not
assumed: on a real fixture (Germany vs Curaçao) the two models' home-win
probabilities differed by 13.1 percentage points. football_data_client's
model uses a fixed, hand-maintained attack/defense lookup table with a flat
1.25x home-advantage constant and no Dixon-Coles low-score correlation
correction; the quant_engine predictor has actual Elo ratings, Dixon-Coles
with a negative-binomial tail for lopsided matches, recency form, H2H, and
self-calibration.

football_data_client.py itself is intentionally untouched — aria_feishu_bot.py
calls predict_match directly with its own pinned tests, out of scope here.
"""

import asyncio

import pytest

from agents.sports.football_agent import FootballAgent


def _run(coro):
    return asyncio.run(coro)


def test_predict_uses_quant_engine_predictor_not_football_data_client(monkeypatch):
    """Regression guard: if someone reverts the import back to
    football_data_client.predict_match, this must fail loudly rather than
    silently degrade to the weaker model.

    football_agent.py imports quick_predict locally inside predict(), so the
    patch target is the predictor module itself (where the name is looked up
    at call time), not agents.sports.football_agent's module namespace."""
    import packages.quant_engine.sports.predictor as predictor_module

    called = {"quick_predict": False}
    original = predictor_module.quick_predict

    def spy(*args, **kwargs):
        called["quick_predict"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(predictor_module, "quick_predict", spy)
    agent = FootballAgent()
    _run(agent.predict("germany", "curacao", league="wc", with_llm=False))
    assert called["quick_predict"], "FootballAgent did not call the quant_engine predictor"


def test_probabilities_are_self_consistent():
    agent = FootballAgent()
    pred = _run(agent.predict("germany", "curacao", league="wc", with_llm=False))
    total = pred.home_win + pred.draw + pred.away_win
    # Each component is independently rounded to 4dp before being returned,
    # so exact-1.0 is not guaranteed — bound the rounding slack instead.
    assert abs(total - 1.0) < 2e-3
    assert 0.0 <= pred.btts <= 1.0
    assert pred.lambda_home > 0 and pred.lambda_away > 0


def test_most_likely_score_matches_top_scoreline():
    agent = FootballAgent()
    pred = _run(agent.predict("brazil", "japan", league="wc", with_llm=False))
    assert pred.most_likely == pred.top_scores[0]["score"]


def test_neutral_venue_removes_home_advantage():
    """Same fixture, only neutral_venue differs — home-win probability must
    drop when the home advantage multiplier is removed, not stay identical
    (which would mean the flag is silently ignored)."""
    agent = FootballAgent()
    home_advantage = _run(
        agent.predict("germany", "curacao", league="wc", with_llm=False, neutral_venue=False)
    )
    neutral = _run(
        agent.predict("germany", "curacao", league="wc", with_llm=False, neutral_venue=True)
    )
    assert home_advantage.home_win > neutral.home_win


def test_verdict_reflects_dominant_side():
    agent = FootballAgent()
    pred = _run(agent.predict("germany", "curacao", league="wc", with_llm=False))
    assert pred.home_win > 0.5
    assert "主队" in pred.verdict


def test_key_factors_report_the_model_used():
    """key_factors used to hardcode '主场优势系数: ×1.25 (泊松模型)' regardless
    of what actually ran — a leftover from the old model that would now be a
    lie. It must describe the real model and its Elo/Dixon-Coles mix weights."""
    agent = FootballAgent()
    pred = _run(agent.predict("germany", "curacao", league="wc", with_llm=False))
    combined = " ".join(pred.key_factors)
    assert "1.25" not in combined
    assert "Elo" in combined and "DC" in combined


def test_fallback_analysis_runs_without_llm():
    agent = FootballAgent()
    pred = _run(agent.predict("germany", "curacao", league="wc", with_llm=True))
    assert pred.analysis  # no LLM configured -> falls back to template, must not be empty
