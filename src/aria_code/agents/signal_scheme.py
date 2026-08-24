"""
agents/signal_scheme.py — 领域可插拔的信号词汇表
====================================================
team.py 里的表决(_vote_signal)、辩论触发(_needs_debate)原本硬编码了金融的
BUY/HOLD/SELL/STRONG_BUY/STRONG_SELL 五级词汇。realty 的 9 个 agent 长期以来
借用同一套词汇表达完全不同的含义(BUY=推荐共创、流水真实、条款清晰、低风险……
每个文件的 BUY 意思都不一样，只在各自 docstring 里注明)，靠 aria_cli.py 里
一张 _SIGNAL_LABELS 翻译表在展示层补救。这意味着一旦有人把多个 realty agent
接进 AgentTeam 做 vote/debate（cashflow_verify 的 SELL=疑似造假 和
ops_optimize 的 SELL=经营不佳需干预 完全不是同一个维度），_vote_signal 会把
它们当同一根多空轴数值平均，产出一个语义上没有意义的"共识"。

SignalScheme 把这套词汇表和打分/辩论触发规则抽成领域可配置的对象，
AgentTeam 默认用 FINANCIAL_SCHEME（对现有金融调用方零行为变化），
其他领域注册自己的词汇表即可复用同一套并行/表决/辩论/synthesis 编排逻辑，
而不必借用金融的 BUY/SELL 语义或答案词不达意。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .base import AgentResult


@dataclass(frozen=True)
class SignalScheme:
    """一套领域信号词汇表：多空打分 + 辩论触发条件。

    upper_thresholds / lower_thresholds 特意分成两条腿而不是一条单向扫描的
    区间列表——原始金融判定逻辑本身就是两段非对称的边界（上方 >= 含右端点，
    下方 <= 含左端点，中间落空隙才算 neutral_default），一条单向 >= 扫描的
    区间列表没法同时正确表达"上方含端点"和"下方也含端点"这两种边界方向，
    会把 avg_score=0 这种中性分数错误地吃进最负的那个区间（已在 fuzz 测试里
    实测过这个 bug：单向列表版本对 avg_score=0 的输入返回了 STRONG_SELL）。
    """

    name: str
    # 信号 → 打分（用于表决时按置信度加权平均）；分数越高越"正面"。
    scores: Dict[str, int]
    # 从高到低排列；第一个满足 avg_score >= threshold 的胜出。
    upper_thresholds: List[Tuple[float, str]]
    # 按"最极端优先"排列（比如先检查 <=-1.5 再检查 <=-0.5）；
    # 第一个满足 avg_score <= threshold 的胜出。
    lower_thresholds: List[Tuple[float, str]]
    # 触发 debate 的两组信号（出现其一 + 另一组其一即触发，比如金融的"多"与"空"）
    positive_signals: frozenset
    negative_signals: frozenset
    neutral_default: str = "HOLD"

    def vote(self, results: List[AgentResult]) -> Tuple[str, float]:
        valid = [r for r in results if r.success and r.signal in self.scores]
        if not valid:
            return self.neutral_default, 0.0
        avg_score = sum(self.scores[r.signal] * r.confidence for r in valid) / len(valid)
        avg_conf = sum(r.confidence for r in valid) / len(valid)
        for threshold, label in self.upper_thresholds:
            if avg_score >= threshold:
                return label, avg_conf
        for threshold, label in self.lower_thresholds:
            if avg_score <= threshold:
                return label, avg_conf
        return self.neutral_default, avg_conf

    def needs_debate(self, results: List[AgentResult]) -> bool:
        signals = [r.signal for r in results if r.success and r.signal]
        has_positive = any(s in self.positive_signals for s in signals)
        has_negative = any(s in self.negative_signals for s in signals)
        return has_positive and has_negative


# ── 金融：既有行为，原样保留（upper/lower 拆分精确复刻原 if/elif 分支）────────
FINANCIAL_SCHEME = SignalScheme(
    name="financial",
    scores={"STRONG_BUY": 2, "BUY": 1, "HOLD": 0, "SELL": -1, "STRONG_SELL": -2},
    upper_thresholds=[(1.5, "STRONG_BUY"), (0.5, "BUY")],
    lower_thresholds=[(-1.5, "STRONG_SELL"), (-0.5, "SELL")],
    positive_signals=frozenset({"BUY", "STRONG_BUY"}),
    negative_signals=frozenset({"SELL", "STRONG_SELL"}),
    neutral_default="HOLD",
)

# ── 地产资管：健康度评估，不是交易动作 ──────────────────────────────────────
# GOOD/WATCH/CONCERN/SEVERE —— 这条轴描述"这个维度是否健康"，不是"要不要买"。
# 9 个 realty agent 各自评估不同维度（流水真实性、合同风险、能耗异常、履约风险
# 等），但共享同一个"健康 → 需关注 → 有问题 → 严重"的四级尺度，用领域自己的
# 词汇表述比借用股票的 BUY/SELL 更准确，也让 vote/debate 的语义站得住脚。
REALTY_SCHEME = SignalScheme(
    name="realty",
    scores={"GOOD": 2, "WATCH": 0, "CONCERN": -1, "SEVERE": -2},
    upper_thresholds=[(1.0, "GOOD")],
    lower_thresholds=[(-1.5, "SEVERE"), (-0.5, "CONCERN")],
    positive_signals=frozenset({"GOOD"}),
    negative_signals=frozenset({"CONCERN", "SEVERE"}),
    neutral_default="WATCH",
)

# ── 仓储 ERP：同样是运营健康度，不是交易动作 ────────────────────────────────
# Warehouse agents assess carrier synchronisation, inbound exceptions and stock
# health.  Keeping a named scheme prevents callers from accidentally applying
# the financial BUY/HOLD/SELL vote to these operational signals.
WAREHOUSE_SCHEME = SignalScheme(
    name="warehouse",
    scores={"GOOD": 2, "WATCH": 0, "CONCERN": -1, "SEVERE": -2},
    upper_thresholds=[(1.0, "GOOD")],
    lower_thresholds=[(-1.5, "SEVERE"), (-0.5, "CONCERN")],
    positive_signals=frozenset({"GOOD"}),
    negative_signals=frozenset({"CONCERN", "SEVERE"}),
    neutral_default="WATCH",
)
