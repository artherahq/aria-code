import asyncio
import pathlib
import sys


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


async def _capture_agent_prompt(monkeypatch, agent_func, *args):
    import financial_agents

    captured = {}

    async def fake_chat(_url, _model, messages, **_kwargs):
        captured["prompt"] = messages[-1]["content"]
        return "ok"

    monkeypatch.setattr(financial_agents, "_ollama_chat", fake_chat)
    result = await agent_func(*args)
    return result, captured["prompt"]


def test_fundamental_agent_prompt_omits_na_placeholders(monkeypatch):
    import financial_agents

    market_data = {
        "quote": {"success": True, "price": 12.3},
        "fundamentals": {"success": True, "market_cap": 1_000_000_000, "pe_ratio": 18.2},
        "technicals": {"success": True, "rsi": 55.1},
    }

    result, prompt = asyncio.run(_capture_agent_prompt(
        monkeypatch,
        financial_agents._fundamental_agent,
        "AAPL", market_data, "http://ollama", "test-model", None,
    ))

    assert result.success
    assert "N/A" not in prompt
    assert "PE(TTM)" in prompt


def test_technical_agent_prompt_handles_partial_indicators(monkeypatch):
    import financial_agents

    market_data = {
        "quote": {"success": True, "price": 12.3},
        "history": {"success": False},
        "technicals": {"success": True, "price": 12.3, "macd": 0.1, "macd_signal": 0.05},
    }

    result, prompt = asyncio.run(_capture_agent_prompt(
        monkeypatch,
        financial_agents._technical_agent,
        "AAPL", market_data, "http://ollama", "test-model", None,
    ))

    assert result.success
    assert "N/A" not in prompt
    assert "技术指标" in prompt


def test_risk_agent_prompt_omits_na_placeholders(monkeypatch):
    import financial_agents

    market_data = {
        "quote": {"success": True, "price": 12.3},
        "fundamentals": {"success": False},
        "technicals": {"success": False},
        "history": {"success": False},
    }

    result, prompt = asyncio.run(_capture_agent_prompt(
        monkeypatch,
        financial_agents._risk_agent,
        "AAPL", market_data, [], "http://ollama", "test-model", None,
    ))

    assert result.success
    assert "N/A" not in prompt
    assert "当前价格" in prompt


def test_synthesis_agent_prompt_omits_na_placeholders(monkeypatch):
    import financial_agents

    agent_result = financial_agents.AgentResult(
        name="MacroAgent",
        role="宏观策略分析师",
        analysis="宏观环境中性。",
    )
    market_data = {"quote": {"success": True, "name": "Apple"}}

    result, prompt = asyncio.run(_capture_agent_prompt(
        monkeypatch,
        financial_agents._synthesis_agent,
        "AAPL", [agent_result], market_data, "http://ollama", "test-model", None, None,
    ))

    assert result.success
    assert "N/A" not in prompt
    assert "今日涨跌: —" in prompt
