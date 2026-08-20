"""花费上限：给能自主跑多轮的 agent 一个护栏。

run_agent() 默认可跑 30 轮，每轮都可能调云端模型；agents/team.py 还会并行拉起
多个 agent。这些循环在无人看管时（定时任务、飞书 bot、后台 subagent）一旦进入
"工具失败→重试→再失败"的死循环，会持续消耗真金白银。此前唯一的护栏是轮数
上限——而轮数管不住单轮消耗：一轮塞 200k token 的上下文和一轮塞 2k，成本差
两个数量级。
"""

from __future__ import annotations

from runtime.budget import (
    BudgetConfig,
    BudgetTracker,
    estimate_cost_usd,
    is_local_provider,
)


def test_local_providers_are_free():
    """本地模型边际成本为零，计入预算只会让本地用户莫名其妙被打断。"""
    for name in ("ollama", "lmstudio", "local", "OLLAMA"):
        assert is_local_provider(name)
        assert estimate_cost_usd(name, 1_000_000, 1_000_000) == 0.0


def test_unknown_provider_is_priced_conservatively():
    """用户自定义 provider 无从查价，宁可估高——估低会让预算形同虚设。"""
    unknown = estimate_cost_usd("mycorp-gateway", 1_000_000, 0)
    cheap = estimate_cost_usd("zhipu", 1_000_000, 0)
    assert unknown > cheap


def test_tracker_accumulates_and_reports_per_provider():
    t = BudgetTracker(BudgetConfig(max_usd=10.0))
    t.record("openai", 100_000, 10_000)
    t.record("deepseek", 100_000, 10_000)
    t.record("ollama", 500_000, 500_000)      # 本地：不计费

    assert t.state.billable_calls == 2, "本地调用不该计入计费次数"
    assert set(t.state.per_provider) == {"openai", "deepseek"}
    assert t.state.total_tokens == 1_220_000
    assert t.state.spent_usd > 0


def test_projected_cost_stops_before_spending_not_after():
    """预检查的意义：花完才发现超支，每次都会多花一轮，而这一轮恰恰可能是
    塞了满上下文的那一轮。"""
    t = BudgetTracker(BudgetConfig(max_usd=1.0))
    t.record("openai", 300_000, 20_000)        # ≈ $0.95
    assert t.should_continue(), "还没超，应当允许继续"
    assert not t.should_continue(projected_usd=0.20), "预计会超支时必须提前拦住"
    assert "预算上限" in t.paused_reason


def test_zero_limits_mean_unlimited():
    """全 0 = 不限制，是显式选择而非默认。"""
    t = BudgetTracker(BudgetConfig(max_usd=0, max_tokens=0, max_rounds=0))
    t.record("openai", 10_000_000, 10_000_000)
    assert t.config.unlimited
    assert t.should_continue()


def test_token_and_round_limits_are_independent():
    t = BudgetTracker(BudgetConfig(max_usd=0, max_tokens=1000, max_rounds=0))
    t.record("openai", 900, 0)
    assert t.should_continue()
    t.record("openai", 200, 0)
    assert not t.should_continue()
    assert "token 上限" in t.paused_reason

    r = BudgetTracker(BudgetConfig(max_usd=0, max_tokens=0, max_rounds=2))
    r.record_round(); assert r.should_continue()
    r.record_round(); assert not r.should_continue()
    assert "轮数上限" in r.paused_reason


def test_resume_without_extra_budget_pauses_again():
    """确认一次只放行一次——避免'确认后无限继续'。"""
    t = BudgetTracker(BudgetConfig(max_usd=1.0))
    t.record("openai", 400_000, 0)
    assert not t.should_continue()

    t.resume()
    assert not t.paused
    assert not t.should_continue(), "没追加额度就该在下一次检查时再次暂停"

    t.resume(additional_usd=5.0)
    assert t.should_continue(), "追加额度后应当放行"


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("ARIA_BUDGET_MAX_USD", "0.5")
    monkeypatch.setenv("ARIA_BUDGET_MAX_ROUNDS", "7")
    cfg = BudgetConfig.from_env()
    assert cfg.max_usd == 0.5 and cfg.max_rounds == 7

    monkeypatch.setenv("ARIA_BUDGET_MAX_USD", "not-a-number")
    assert BudgetConfig.from_env().max_usd == 2.0, "非法值应退回默认，而不是崩溃"


def test_summary_calls_out_all_local_sessions():
    t = BudgetTracker(BudgetConfig(max_usd=2.0))
    t.record("ollama", 50_000, 50_000)
    assert "零成本" in t.summary()


def test_agent_options_default_has_no_budget():
    """不传 budget 时必须与既有行为完全一致——这是能安全上线的前提。"""
    from runtime.agent_loop import AgentOptions
    assert AgentOptions().budget is None


def test_projection_prevents_overspending_the_last_round():
    """没有预测时闸门只能事后止损——超支的正是最后那一轮，而它恰恰可能是
    上下文最满、最贵的一轮。"""
    t = BudgetTracker(BudgetConfig(max_usd=1.0))
    t.record("openai", 150_000, 5_000); t.record_round()   # ≈ $0.425
    assert t.projected_next_round_usd() > 0.4

    t.record("openai", 150_000, 5_000); t.record_round()   # ≈ $0.85 累计
    # 再跑一轮会到 $1.275 > $1.00，预检查必须提前拦住
    assert not t.should_continue(projected_usd=t.projected_next_round_usd())


def test_first_round_is_never_blocked():
    """预算比单轮成本还低时会超支一次——有意为之：宁可放行第一轮，
    也不要因为一个凭空的猜测把正常会话堵死在起点。"""
    t = BudgetTracker(BudgetConfig(max_usd=0.01))
    assert t.projected_next_round_usd() == 0.0
    assert t.should_continue(projected_usd=t.projected_next_round_usd())
