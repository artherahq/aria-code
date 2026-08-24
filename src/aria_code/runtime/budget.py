"""Token / 成本预算：给能自主跑多轮的 agent 一个花费上限。

为什么需要：run_agent() 默认可以跑 30 轮，每轮都可能调云端模型；
agents/team.py 还会并行拉起多个 agent。这些循环在无人看管时（定时任务、
飞书 bot、后台 subagent）一旦进入一个"工具失败→重试→再失败"的死循环，
会持续消耗真金白银，而现有的唯一护栏是轮数上限——轮数管不住单轮消耗，
一轮塞进 200k token 的上下文和一轮塞进 2k，成本差两个数量级。

设计要点：

1. **本地模型不计费**。ollama / lmstudio / local 的边际成本是零，把它们计入
   预算只会让本地用户莫名其妙被打断。这跟 /cost 命令已有的判断保持一致。

2. **超限是暂停，不是抛异常**。异常会让调用方拿不到已经产出的中间结果；
   返回一个明确的"已暂停"状态，调用方可以决定是展示给用户确认后继续，
   还是就此收尾。这也是 pause/resume 语义能成立的前提。

3. **预检查而非事后补记**。在发起下一轮之前判断"这一轮预计会花多少"，
   而不是花完了才发现超了——后者每次都会超支一轮。

4. **默认值保守但不碍事**。默认 2.0 美元/会话，够正常交互用很久，又能在
   死循环烧掉几十美元之前叫停。设成 0 表示不限制（显式选择，不是默认）。

价格表按 2026-08 的公开价格，只用于**估算**：真实账单以各家为准。宁可估高
不估低——估低会让预算形同虚设。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

__all__ = [
    "BudgetConfig", "BudgetState", "BudgetTracker",
    "BudgetExceeded", "estimate_cost_usd", "is_local_provider",
]

# 零边际成本的 provider —— 与 /cost 命令的判断保持一致
_LOCAL_PROVIDERS = frozenset({"ollama", "ollama_cache", "local", "lmstudio"})

# (输入, 输出) 美元 / 每百万 token。取各家公开价格的较高档，宁可估高。
_PRICE_TABLE: Dict[str, Tuple[float, float]] = {
    "openai":      (2.50, 10.00),
    "anthropic":   (3.00, 15.00),
    "deepseek":    (0.27,  1.10),
    "groq":        (0.59,  0.79),
    "siliconflow": (0.27,  1.10),
    "dashscope":   (0.40,  1.20),
    "moonshot":    (1.70,  1.70),
    "zhipu":       (0.10,  0.10),
    "together":    (0.88,  0.88),
}
_DEFAULT_PRICE = (2.00, 8.00)   # 未知 provider（含用户自定义的）按较贵档估


def is_local_provider(provider: Optional[str]) -> bool:
    return (provider or "").strip().lower() in _LOCAL_PROVIDERS


def estimate_cost_usd(provider: Optional[str], input_tokens: int, output_tokens: int) -> float:
    """估算一次调用的美元成本。本地 provider 恒为 0。"""
    if is_local_provider(provider):
        return 0.0
    price_in, price_out = _PRICE_TABLE.get((provider or "").strip().lower(), _DEFAULT_PRICE)
    return (max(0, input_tokens) * price_in + max(0, output_tokens) * price_out) / 1_000_000


@dataclass(frozen=True)
class BudgetConfig:
    """预算上限。任一项设为 0 表示该项不限制。"""
    max_usd: float = 2.0
    max_tokens: int = 0
    max_rounds: int = 0

    @classmethod
    def from_env(cls) -> "BudgetConfig":
        """环境变量覆盖默认值，便于定时任务/容器里单独收紧或放开。"""
        def _num(name: str, default: float) -> float:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return max(0.0, float(raw))
            except ValueError:
                return default

        return cls(
            max_usd=_num("ARIA_BUDGET_MAX_USD", 2.0),
            max_tokens=int(_num("ARIA_BUDGET_MAX_TOKENS", 0)),
            max_rounds=int(_num("ARIA_BUDGET_MAX_ROUNDS", 0)),
        )

    @property
    def unlimited(self) -> bool:
        return not (self.max_usd or self.max_tokens or self.max_rounds)


@dataclass
class BudgetState:
    spent_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    rounds: int = 0
    billable_calls: int = 0        # 计费调用次数（本地调用不计）
    per_provider: Dict[str, float] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class BudgetExceeded(RuntimeError):
    """仅供确实需要硬中断的调用方使用。默认路径不抛这个——见模块文档第 2 点。"""

    def __init__(self, reason: str, state: BudgetState):
        super().__init__(reason)
        self.reason = reason
        self.state = state


class BudgetTracker:
    """累计消耗并回答"还能不能再跑一轮"。

    刻意不持有任何 provider 或循环对象：它只接收数字、返回判断，因此可以在
    CLI 会话、后台 subagent、agents.team 并行编排里用同一套逻辑，也容易测试。
    """

    def __init__(self, config: Optional[BudgetConfig] = None):
        self.config = config or BudgetConfig.from_env()
        self.state = BudgetState()
        self._paused_reason: Optional[str] = None

    # ── 记账 ──────────────────────────────────────────────────────────
    def record(self, provider: Optional[str], input_tokens: int, output_tokens: int) -> float:
        """记录一次模型调用，返回本次估算成本。"""
        cost = estimate_cost_usd(provider, input_tokens, output_tokens)
        self.state.input_tokens += max(0, input_tokens)
        self.state.output_tokens += max(0, output_tokens)
        self.state.spent_usd += cost
        if cost > 0:
            self.state.billable_calls += 1
            key = (provider or "unknown").strip().lower()
            self.state.per_provider[key] = self.state.per_provider.get(key, 0.0) + cost
        return cost

    def record_round(self) -> None:
        self.state.rounds += 1

    def projected_next_round_usd(self) -> float:
        """按已观测到的平均单轮成本，预测下一轮会花多少。

        没有这个预测，闸门只能事后止损：$1 的预算实测会花到 $1.275——超支的
        正是最后那一轮，而它恰恰可能是上下文最满、最贵的一轮。用历史均值做
        预测，能在**发起调用之前**就拦住。

        还没有任何计费轮次时返回 0（没有依据就不猜，第一轮总是放行）。

        因此**第一轮永远无法被预算拦住**——预算比单轮成本还低时会超支一次。
        这是有意的：宁可放行第一轮，也不要因为一个凭空的猜测把正常会话
        堵死在起点。真要限制到单轮以下，应该用更小的模型或更短的上下文，
        而不是指望预算闸门。
        """
        if self.state.rounds <= 0 or self.state.spent_usd <= 0:
            return 0.0
        return self.state.spent_usd / self.state.rounds

    # ── 判断 ──────────────────────────────────────────────────────────
    def check(self, *, projected_usd: float = 0.0, projected_tokens: int = 0) -> Optional[str]:
        """返回超限原因；未超限返回 None。

        projected_* 是"下一轮预计消耗"。传入它就变成**预检查**——在花掉之前
        拦住，而不是花完才发现超了（后者每次都会超支一轮）。
        """
        cfg = self.config
        if cfg.unlimited:
            return None

        if cfg.max_usd:
            projected = self.state.spent_usd + max(0.0, projected_usd)
            if projected >= cfg.max_usd:
                return (f"预算上限 ${cfg.max_usd:.2f} 已达到"
                        f"（已用 ${self.state.spent_usd:.4f}"
                        f"{f'，本轮预计 ${projected_usd:.4f}' if projected_usd else ''}）")

        if cfg.max_tokens:
            projected_tok = self.state.total_tokens + max(0, projected_tokens)
            if projected_tok >= cfg.max_tokens:
                return (f"token 上限 {cfg.max_tokens:,} 已达到"
                        f"（已用 {self.state.total_tokens:,}）")

        if cfg.max_rounds and self.state.rounds >= cfg.max_rounds:
            return f"轮数上限 {cfg.max_rounds} 已达到"

        return None

    def should_continue(self, *, projected_usd: float = 0.0, projected_tokens: int = 0) -> bool:
        reason = self.check(projected_usd=projected_usd, projected_tokens=projected_tokens)
        if reason:
            self._paused_reason = reason
        return reason is None

    # ── 状态 ──────────────────────────────────────────────────────────
    @property
    def paused(self) -> bool:
        return self._paused_reason is not None

    @property
    def paused_reason(self) -> Optional[str]:
        return self._paused_reason

    def resume(self, *, additional_usd: float = 0.0) -> None:
        """用户确认后继续。可选地追加额度——不追加就会在下一次检查时再次暂停，
        这是刻意的：确认一次只放行一次，避免"确认后无限继续"。
        """
        self._paused_reason = None
        if additional_usd > 0:
            self.config = BudgetConfig(
                max_usd=self.config.max_usd + additional_usd,
                max_tokens=self.config.max_tokens,
                max_rounds=self.config.max_rounds,
            )

    def summary(self) -> str:
        s = self.state
        parts = [f"${s.spent_usd:.4f}", f"{s.total_tokens:,} tokens", f"{s.rounds} 轮"]
        if self.config.max_usd:
            pct = (s.spent_usd / self.config.max_usd * 100) if self.config.max_usd else 0
            parts.append(f"用量 {pct:.0f}% / ${self.config.max_usd:.2f}")
        if not s.billable_calls and s.total_tokens:
            parts.append("（全部走本地模型，零成本）")
        return " · ".join(parts)
