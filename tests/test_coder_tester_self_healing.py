"""
tests/test_coder_tester_self_healing.py — Tests for Coder, Tester, and Self-Healing Engine
"""

import asyncio
import json
import pathlib
import sys
import tempfile
import pytest

from agents.engineering.coder import CoderAgent
from agents.engineering.tester import TesterAgent, TesterSelfHealingAgent
from agents.financial.strategist import StrategistAgent
from agents.registry import get_registry
from runtime.self_healing import SelfHealingEngine, TracebackInfo


def test_agent_registry_discovery():
    registry = get_registry()
    coder_cls = registry.get("coder")
    tester_cls = registry.get("tester")
    debugger_cls = registry.get("debugger")
    strategist_cls = registry.get("strategist")

    assert coder_cls is not None
    assert tester_cls is not None
    assert debugger_cls is not None
    assert strategist_cls is not None


def test_coder_agent_generate_self_contained_script():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = pathlib.Path(tmpdir)
        coder = CoderAgent(output_dir=out_dir)

        rule_tree = {
            "symbol": "NVDA",
            "strategy_name": "NVDA Dual Moving Average",
            "factors": [
                {"name": "SMA_Fast", "params": {"period": 10}},
                {"name": "SMA_Slow", "params": {"period": 30}},
            ],
            "risk_constraints": {
                "stop_loss_pct": 7.0,
                "slippage_pct": 0.05,
                "commission_pct": 0.05,
            },
        }

        script_path, code = coder.generate_backtest_script("NVDA", rule_tree=rule_tree)
        assert script_path.exists()
        assert "SYMBOL = \"NVDA\"" in code
        assert "FAST_PERIOD = 10" in code
        assert "SLOW_PERIOD = 30" in code
        assert "SLIPPAGE_PCT = 0.05" in code

        # Verify syntax
        engine = SelfHealingEngine()
        valid, err, line = engine.verify_syntax(script_path)
        assert valid is True
        assert err is None


def test_self_healing_engine_syntax_and_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = pathlib.Path(tmpdir)
        coder = CoderAgent(output_dir=out_dir)
        script_path, _ = coder.generate_backtest_script("AAPL")

        engine = SelfHealingEngine(python_executable=sys.executable)
        async def _run():
            res = await engine.execute_and_heal(script_path)
            assert res.success is True
            assert res.metrics.get("symbol") == "AAPL"
            assert "total_return_pct" in res.metrics
            assert "sharpe_ratio" in res.metrics
            assert "max_drawdown_pct" in res.metrics
            assert len(res.artifact_paths) >= 1

        asyncio.run(_run())


def test_self_healing_engine_patch_repair_on_zero_division():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = pathlib.Path(tmpdir) / "buggy_calc.py"
        buggy_code = """
import json
def calculate():
    x = 100
    y = 0
    sharpe = x / y
    return {"sharpe_ratio": sharpe, "symbol": "TEST"}

if __name__ == "__main__":
    res = calculate()
    print(json.dumps(res))
"""
        test_file.write_text(buggy_code.strip(), encoding="utf-8")

        engine = SelfHealingEngine(python_executable=sys.executable, max_retries=3)
        async def _run():
            res = await engine.execute_and_heal(test_file)
            assert res.success is True
            assert len(res.patches_applied) > 0
            assert res.metrics.get("symbol") == "TEST"

        asyncio.run(_run())


def test_full_pipeline_strategist_coder_tester():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = pathlib.Path(tmpdir)

        async def _run():
            # 1. Strategist designs rule tree
            strategist = StrategistAgent()
            strat_res = await strategist.analyze("NVDA", {"goal": "Double MA Trend Following", "style": "dual_ma"})
            assert strat_res.success is True
            rule_tree = strat_res.data_used.get("rule_tree")
            assert rule_tree is not None

            # 2. Coder writes self-contained script
            coder = CoderAgent(output_dir=out_dir)
            code_res = await coder.analyze("NVDA", {"rule_tree": rule_tree})
            assert code_res.success is True
            script_path = code_res.data_used.get("script_path")
            assert script_path is not None
            assert pathlib.Path(script_path).exists()

            # 3. Tester runs pre-flight, dry-run, and self-healing
            tester = TesterAgent()
            test_res = await tester.analyze("NVDA", {"script_path": script_path})
            assert test_res.success is True
            assert "年化收益率" in test_res.analysis
            assert "夏普比率" in test_res.analysis
            assert "最大回撤" in test_res.analysis
            assert test_res.data_used["metrics"]["symbol"] == "NVDA"

        asyncio.run(_run())
