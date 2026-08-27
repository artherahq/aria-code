"""
tests/test_coder_tester_self_healing.py — Tests for Coder, Tester, and Self-Healing Engine
"""

import asyncio
import json
import pathlib
import sys
import tempfile
import pytest

from aria_code.agents.engineering.coder import CoderAgent
from aria_code.agents.engineering.tester import TesterAgent, TesterSelfHealingAgent
from aria_code.agents.financial.strategist import StrategistAgent
from aria_code.agents.registry import get_registry
from aria_code.runtime.self_healing import SelfHealingEngine, TracebackInfo


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
        }
        
        async def _run():
            res = await coder.analyze("NVDA", {"rule_tree": rule_tree})
            assert res.success is True
            assert res.data_used["workspace"] == str(out_dir)

        asyncio.run(_run())


def test_self_healing_engine_syntax_and_execution():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = pathlib.Path(tmpdir)
        coder = CoderAgent(output_dir=out_dir)

        async def _run():
            res = await coder.analyze("AAPL", {})
            assert res.success is True
            assert res.data_used["workspace"] == str(out_dir)

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
            
            # Mock script for testing
            script_path = str(out_dir / "backtest.py")
            with open(script_path, "w") as f:
                f.write('import json\nprint(json.dumps({"symbol": "NVDA", "annual_return_pct": 10.0, "sharpe_ratio": 1.5, "max_drawdown_pct": 5.0, "win_rate_pct": 60.0}))')

            # 3. Tester runs pre-flight, dry-run, and self-healing
            tester = TesterAgent()
            test_res = await tester.analyze("NVDA", {"script_path": script_path})
            assert test_res.success is True
            assert "年化收益率" in test_res.analysis
            assert "夏普比率" in test_res.analysis
            assert "最大回撤" in test_res.analysis
            assert test_res.data_used["metrics"]["symbol"] == "NVDA"

        asyncio.run(_run())
