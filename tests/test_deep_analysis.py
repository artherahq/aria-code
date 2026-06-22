"""Deterministic tests for the deep analysis pipeline (no LLM / no network)."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from agents.base import AgentResult
from agents.deep.calibration_loop import (
    PredictionLog, correctness, evaluate_due, evaluate_from_ledger)
from agents.deep.deepen import deepen_agentic
from agents.deep.themes import group_by_theme, theme_of
from agents.deep.quant_fusion import (
    gather_quant_evidence, calibrate_confidence, agreement, CalibrationStore,
)
from agents.deep.critic import critique, soften_signal
from agents.deep.deepen import deepen
from agents.deep.tiers import render_brief, render_standard, render_deep
from agents.deep.pipeline import DeepAnalysisPipeline


def _r(agent, signal, conf, pts=None, err=None):
    return AgentResult(agent=agent, symbol="TEST", analysis="x", confidence=conf,
                       signal=signal, key_points=pts or [], error=err)


def _bull_team():
    return [
        _r("technical", "STRONG_BUY", 0.8, ["突破60日均线", "RSI 上行"]),
        _r("fundamental", "BUY", 0.7, ["PE 低于行业"]),
        _r("macro", "BUY", 0.6, ["流动性宽松"]),
        _r("risk", "HOLD", 0.5, ["波动温和"]),
    ]


def _bull_quant(symbol):
    return {"ai": {"success": True, "action": "BUY", "confidence": 0.7, "provider": "test"},
            "risk": {"success": True, "sharpe": 1.2, "max_drawdown": -0.15}}


def _bear_quant(symbol):
    return {"ai": {"success": True, "action": "SELL", "confidence": 0.8, "provider": "test"}}


def _tool_runner(tool, params):
    return {
        "_get_risk_metrics": {"success": True, "var_daily": -0.03, "sharpe": 1.1, "max_drawdown": -0.2},
        "_calculate_factors": {"success": True, "momentum": 0.12, "volatility": 0.21},
        "_analyze_news": {"success": True, "sentiment": "positive"},
        "_backtest_strategy": {"success": True, "total_return": 0.18, "sharpe": 1.0},
    }.get(tool)


class ThemeTests(unittest.TestCase):
    def test_theme_of(self):
        self.assertEqual(theme_of("technical"), "momentum")
        self.assertEqual(theme_of("risk"), "risk")
        self.assertEqual(theme_of("unknown_x"), "other")

    def test_grouping_and_vote(self):
        groups = group_by_theme(_bull_team())
        themes = {g.theme: g for g in groups}
        self.assertIn("估值/基本面", themes)
        self.assertIn("风险", themes)
        # momentum theme (technical STRONG_BUY) should be bullish
        mom = next(g for g in groups if g.theme.startswith("动量"))
        self.assertIn(mom.signal, ("BUY", "STRONG_BUY"))


class QuantTests(unittest.TestCase):
    def test_gather_and_verdict(self):
        ev, prov = gather_quant_evidence("TEST", _bull_quant)
        self.assertTrue(ev.available)
        self.assertEqual(ev.verdict(), "BULLISH")
        self.assertEqual(ev.sharpe, 1.2)
        self.assertTrue(any(p.field == "ai_signal" for p in prov))

    def test_agreement(self):
        self.assertEqual(agreement("BUY", "BULLISH"), "agree")
        self.assertEqual(agreement("BUY", "BEARISH"), "disagree")
        self.assertEqual(agreement("HOLD", "BULLISH"), "neutral")

    def test_calibration_boost_and_damp(self):
        ev_bull, _ = gather_quant_evidence("TEST", _bull_quant)
        cal, ag = calibrate_confidence(0.60, "BUY", ev_bull)
        self.assertEqual(ag, "agree")
        self.assertGreater(cal, 0.60)            # agreement boosts

        ev_bear, _ = gather_quant_evidence("TEST", _bear_quant)
        cal2, ag2 = calibrate_confidence(0.60, "BUY", ev_bear)
        self.assertEqual(ag2, "disagree")
        self.assertLess(cal2, 0.60)              # conflict damps

    def test_store_reliability(self):
        with tempfile.TemporaryDirectory() as d:
            store = CalibrationStore(Path(d) / "cal.json")
            self.assertEqual(store.reliability(0.8, "BUY"), 1.0)   # no history → neutral
            for _ in range(10):                                    # over-confident "hi" bucket
                store.record_outcome("BUY", 0.8, correct=False)
            self.assertLess(store.reliability(0.8, "BUY"), 1.0)    # damped after misses
            # reload from disk persists
            store2 = CalibrationStore(Path(d) / "cal.json")
            self.assertLess(store2.reliability(0.8, "BUY"), 1.0)


class CriticTests(unittest.TestCase):
    def test_thin_coverage_high(self):
        c = critique([_r("technical", "BUY", 0.6)], "BUY", 0.6)
        self.assertFalse(c.passed)
        self.assertTrue(any(i.kind == "thin_coverage" and i.severity == "high" for i in c.issues))

    def test_missing_risk(self):
        team = [_r("technical", "BUY", 0.7, ["a"]), _r("fundamental", "BUY", 0.6, ["b"])]
        c = critique(team, "BUY", 0.6)
        self.assertTrue(any(i.kind == "missing_risk" for i in c.issues))

    def test_quant_conflict_fails(self):
        ev, _ = gather_quant_evidence("TEST", _bear_quant)
        c = critique(_bull_team(), "BUY", 0.4, ev, agreement="disagree", key_point_count=4)
        self.assertFalse(c.passed)
        self.assertTrue(any(i.kind == "conflict" for i in c.issues))

    def test_soften(self):
        self.assertEqual(soften_signal("STRONG_BUY"), "BUY")
        self.assertEqual(soften_signal("BUY"), "HOLD")


class DeepenTests(unittest.TestCase):
    def test_deepen_gathers_evidence(self):
        groups = group_by_theme([_r("technical", "HOLD", 0.5, ["平台整理"])])  # momentum HOLD, no risk
        notes, prov = deepen("TEST", groups, None, tool_runner=_tool_runner, max_steps=4)
        self.assertTrue(notes)
        self.assertTrue(any("下行风险" in n for n in notes))   # risk gap filled
        self.assertEqual(len(prov), len(notes))


class TierTests(unittest.TestCase):
    def _result(self):
        pipe = DeepAnalysisPipeline(store=CalibrationStore(Path(tempfile.mkdtemp()) / "c.json"))
        return pipe.analyze("TEST", _bull_team(), quant_provider=_bull_quant,
                            tool_runner=_tool_runner)

    def test_tiers(self):
        r = self._result()
        self.assertIn("TEST", render_brief(r))
        self.assertIn("分主题", render_standard(r))
        deep = render_deep(r)
        self.assertIn("数据血缘", deep)
        self.assertIn("自检", deep)
        self.assertIn("量化", deep)


class PipelineTests(unittest.TestCase):
    def _pipe(self):
        return DeepAnalysisPipeline(store=CalibrationStore(Path(tempfile.mkdtemp()) / "c.json"))

    def test_analyze_agree_calibrates_up(self):
        r = self._pipe().analyze("TEST", _bull_team(), quant_provider=_bull_quant,
                                 tool_runner=_tool_runner)
        self.assertEqual(r.symbol, "TEST")
        self.assertTrue(r.quant.available)
        self.assertGreater(r.calibrated_confidence, r.raw_confidence)  # agreement boost
        self.assertTrue(r.themes)
        self.assertIsNotNone(r.critique)

    def test_analyze_conflict_softens_signal(self):
        r = self._pipe().analyze("TEST", _bull_team(), quant_provider=_bear_quant,
                                 tool_runner=_tool_runner)
        self.assertEqual(agreement(r.final_signal, r.quant.verdict()), "neutral")  # softened toward HOLD
        self.assertFalse(r.critique.passed)
        self.assertLess(r.calibrated_confidence, r.raw_confidence)

    def test_empty_results_error(self):
        r = self._pipe().analyze("TEST", [])
        self.assertEqual(r.error, "no_agent_results")


class CalibrationLoopTests(unittest.TestCase):
    def test_correctness(self):
        self.assertTrue(correctness("BUY", 0.05))
        self.assertFalse(correctness("BUY", -0.05))
        self.assertTrue(correctness("SELL", -0.05))
        self.assertTrue(correctness("HOLD", 0.01))
        self.assertFalse(correctness("HOLD", 0.05))

    def test_evaluate_due_updates_calibration(self):
        with tempfile.TemporaryDirectory() as d:
            store = CalibrationStore(Path(d) / "cal.json")
            log = PredictionLog(Path(d) / "pred.json")
            now = time.time()
            log.log("AAA", "BUY", 0.8, 100.0, ts=now - 6 * 86400)   # due (6d > 5d)
            log.log("BBB", "BUY", 0.8, 100.0, ts=now - 1 * 86400)   # too recent
            res = evaluate_due(store, log, lambda s: {"AAA": 110.0}.get(s),
                               horizon_days=5, threshold=0.02, now=now)
            self.assertEqual(res["evaluated"], 1)
            self.assertEqual(res["hits"], 1)              # +10% confirms BUY
            self.assertEqual(len(log.pending(5, now=now)), 0)
            # a wrong call should be recorded as a miss and survive reload
            log.log("CCC", "BUY", 0.8, 100.0, ts=now - 6 * 86400)
            evaluate_due(store, log, lambda s: {"CCC": 90.0}.get(s),
                         horizon_days=5, threshold=0.02, now=now)
            store2 = CalibrationStore(Path(d) / "cal.json")
            self.assertEqual(store2._data["bull:hi"]["n"], 2)
            self.assertEqual(store2._data["bull:hi"]["hit"], 1)


class _FakeLedger:
    def __init__(self, realized):
        self._r = realized

    def get_realized_pnl(self):
        return self._r


class LedgerCalibrationTests(unittest.TestCase):
    def test_evaluate_from_ledger_scores_by_realised_pnl(self):
        with tempfile.TemporaryDirectory() as d:
            store = CalibrationStore(Path(d) / "cal.json")
            log = PredictionLog(Path(d) / "pred.json")
            log.log("WIN", "BUY", 0.7, 100.0)    # bought & profited → correct
            log.log("LOSE", "BUY", 0.7, 100.0)   # bought & lost → wrong
            log.log("HOLDX", "HOLD", 0.7, 100.0) # HOLD skipped (no % basis)
            ledger = _FakeLedger([
                {"symbol": "WIN", "realized_pnl": 250.0, "has_open": False},
                {"symbol": "LOSE", "realized_pnl": -80.0, "has_open": False},
                {"symbol": "HOLDX", "realized_pnl": 5.0, "has_open": False},
            ])
            res = evaluate_from_ledger(store, log, ledger)
            self.assertEqual(res["evaluated"], 2)   # WIN + LOSE; HOLD skipped
            self.assertEqual(res["hits"], 1)        # only WIN correct
            # marked with source=ledger, survives reload
            reloaded = PredictionLog(Path(d) / "pred.json")
            srcs = {p["symbol"]: p.get("source") for p in reloaded._items if p.get("evaluated")}
            self.assertEqual(srcs.get("WIN"), "ledger")

    def test_ledger_failure_is_graceful(self):
        with tempfile.TemporaryDirectory() as d:
            store = CalibrationStore(Path(d) / "cal.json")
            log = PredictionLog(Path(d) / "pred.json")

            class _Boom:
                def get_realized_pnl(self):
                    raise RuntimeError("db locked")

            res = evaluate_from_ledger(store, log, _Boom())
            self.assertEqual(res["evaluated"], 0)


class _FakeLLM:
    """Yields one scripted reply per stream() call."""
    def __init__(self, replies):
        self.replies = list(replies)

    async def stream(self, messages, max_tokens=120):
        reply = self.replies.pop(0) if self.replies else "DONE"
        yield {"type": "token", "text": reply}


class AgenticDeepenTests(unittest.TestCase):
    def test_llm_picks_tool_then_done(self):
        groups = group_by_theme([_r("technical", "HOLD", 0.5, ["盘整"])])
        llm = _FakeLLM(["_get_risk_metrics", "DONE"])
        notes, prov = asyncio.run(deepen_agentic(
            "TEST", groups, None, llm=llm, tool_runner=_tool_runner, max_steps=3))
        self.assertTrue(any("Sharpe" in n or "VaR" in n or "MaxDD" in n for n in notes))
        self.assertEqual(len(prov), len(notes))

    def test_no_llm_falls_back_to_deterministic(self):
        groups = group_by_theme([_r("technical", "HOLD", 0.5, ["盘整"])])
        notes, _ = asyncio.run(deepen_agentic(
            "TEST", groups, None, llm=None, tool_runner=_tool_runner))
        self.assertTrue(notes)   # deterministic planner still fills gaps


class LLMCriticTests(unittest.TestCase):
    def test_parse_llm_issues(self):
        from agents.deep.critic import parse_llm_issues
        issues = parse_llm_issues("高|估值无现金流支撑\n中｜未提汇率风险\nOK")
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].severity, "high")
        self.assertEqual(issues[0].kind, "llm_review")
        self.assertEqual(issues[1].severity, "medium")

    def test_parse_ok_means_clean(self):
        from agents.deep.critic import parse_llm_issues
        self.assertEqual(parse_llm_issues("OK"), [])

    def test_llm_critique_with_fake_llm(self):
        from agents.deep.critic import llm_critique
        llm = _FakeLLM(["高|过度自信：置信度与论据不符"])
        issues = asyncio.run(llm_critique("X", "结论：强烈看多", "动量:BUY", llm))
        self.assertTrue(issues and issues[0].severity == "high")

    def test_llm_critique_no_llm_is_noop(self):
        from agents.deep.critic import llm_critique
        self.assertEqual(asyncio.run(llm_critique("X", "syn", "t", None)), [])


def test_pipeline_run_orchestrates_team_to_deep_result(monkeypatch):
    """End-to-end: run() drives team → deepen → themes → quant → critic into a
    structured DeepAnalysisResult. Fully mocked (no LLM, no network)."""
    import agents.team as _team
    from agents.team import TeamResult
    from agents.deep import DeepAnalysisPipeline

    class _FakeTeam:
        def __init__(self, **kw):
            pass

        async def run(self, symbol, agents=None):
            results = [
                AgentResult(agent="technical", symbol=symbol, analysis="上行",
                            confidence=0.8, signal="BUY", key_points=["突破均线"]),
                AgentResult(agent="risk", symbol=symbol, analysis="风险可控",
                            confidence=0.7, signal="HOLD", key_points=["回撤有限"]),
            ]
            return TeamResult(symbol=symbol, agents_run=["technical", "risk"],
                              results=results, synthesis="综合：偏多",
                              final_signal="BUY", confidence=0.75)

    monkeypatch.setattr(_team, "AgentTeam", _FakeTeam)

    pipe = DeepAnalysisPipeline(llm_provider=None)   # deterministic deepen, no LLM critic
    res = asyncio.run(pipe.run(
        "TEST", quant_provider=lambda s: {}, tool_runner=lambda t, p: None))

    assert res.symbol == "TEST"
    assert res.final_signal in {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"}
    assert res.themes                        # P1 theme grouping ran
    assert res.critique is not None          # P1 self-check ran
    assert res.calibrated_confidence >= 0.0  # P2 calibration produced a value
    assert res.elapsed_sec >= 0.0


if __name__ == "__main__":
    unittest.main()
