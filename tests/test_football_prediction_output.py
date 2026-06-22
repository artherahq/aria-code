from football_data_client import format_prediction_block, predict_match
from apps.cli.providers.llm.ollama_stream import _recent_sports_quant_context


def test_predict_match_most_likely_uses_top_scoreline_not_rounded_lambdas():
    pred = predict_match(
        "New Zealand",
        "Egypt",
        "pl",
        home_attack=0.72,
        away_attack=1.1333333333,
        home_defense=1.0,
        away_defense=1.0,
        home_adv=1.0,
    )

    assert pred["lambda_home"] == 1.08
    assert pred["lambda_away"] == 1.7
    assert pred["most_likely_score"] == pred["top_scorelines"][0]["score"]
    assert pred["most_likely_score"] == "1-1"


def test_format_prediction_block_marks_poisson_quant_context():
    block = format_prediction_block({
        "home_team": "New Zealand",
        "away_team": "Egypt",
        "home_name_cn": "新西兰",
        "away_name_cn": "埃及",
        "home_ranking": 80,
        "away_ranking": 36,
        "home_attack": 1.02,
        "away_attack": 1.64,
        "home_defense": 0.94,
        "away_defense": 0.74,
        "lambda_home": 1.08,
        "lambda_away": 1.70,
        "home_win": 0.23,
        "draw": 0.23,
        "away_win": 0.54,
        "btts": 0.53,
        "league_avg_goals": 1.35,
        "top_scorelines": [
            {"score": "1-1", "prob": 11.86},
            {"score": "0-1", "prob": 9.76},
        ],
        "implied_odds": {"home": 4.31, "draw": 4.4, "away": 1.85},
    })

    assert "【泊松模型量化预测" in block
    assert "1-1" in block
    assert "提示：准确比分概率通常较分散" in block


def test_recent_sports_quant_context_supports_scoreline_followups():
    history = [
        {"role": "assistant", "content": "普通回答"},
        {
            "role": "assistant",
            "content": "【泊松模型量化预测 — 新西兰 vs 埃及】\n"
                       "  预期进球: 新西兰 1.08 | 埃及 1.70\n"
                       "  最可能比分:\n"
                       "    1-1  (11.86%)\n"
                       "    0-1  (9.76%)\n",
        },
    ]

    ctx = _recent_sports_quant_context(history)

    assert "新西兰 vs 埃及" in ctx
    assert "1-1" in ctx
