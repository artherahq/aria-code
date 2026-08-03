"""
agents/base.py — Agent 统一抽象基类
=====================================
所有 agent 继承 BaseAgent，实现 analyze() 方法。
LLM provider 和数据源从外部注入，agent 本身不关心底层实现。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Agent 分析结果，统一输出格式"""
    agent:       str                    # agent 名称
    symbol:      str                    # 分析标的
    analysis:    str                    # 核心分析文本
    confidence:  float                  # 置信度 0.0-1.0
    signal:      str = "HOLD"           # BUY / HOLD / SELL / STRONG_BUY / STRONG_SELL
    key_points:  List[str] = field(default_factory=list)   # 关键结论（用于 synthesis）
    data_used:   Dict[str, Any] = field(default_factory=dict)  # 使用的原始数据
    error:       Optional[str] = None   # 失败时的错误信息
    degraded:    bool = False           # True when a deterministic fallback was used
    provenance:  List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.error is None

    def to_dict(self) -> Dict:
        return {
            "agent":      self.agent,
            "symbol":     self.symbol,
            "analysis":   self.analysis,
            "confidence": self.confidence,
            "signal":     self.signal,
            "key_points": self.key_points,
            "error":      self.error,
            "degraded":   self.degraded,
            "provenance": list(self.provenance),
            "limitations": list(self.limitations),
        }


class BaseAgent(ABC):
    """
    所有 Agent 的抽象基类。

    子类必须声明:
        name:        str   — 唯一标识（用于 /team --agents macro,fundamental）
        description: str   — 简短描述（显示在 /help 中）

    子类必须实现:
        analyze(symbol, data) → AgentResult

    可选覆盖:
        fetch_data(symbol) → dict  — 自定义数据获取逻辑
    """

    name:        str = "base"
    description: str = "基础 Agent"

    # Post-training IPO facts injected into every agent's system prompt
    _POST_TRAINING_FACTS = (
        "\n\n## ⚡ Critical post-training facts (override training memory)\n"
        "- **SPCX** = SpaceX (Space Exploration Technologies Corp.) — IPO'd June 12, 2026 "
        "on NASDAQ. It is NOT an ETF. ⛔ Never say 'SPCX is a China ETF' or 'SPCX is private'.\n"
        "- **2026 FIFA World Cup** opened June 11, 2026 in USA/Canada/Mexico.\n"
    )

    # Language rule injected per-call based on detected user language
    _LANG_RULES = {
        "zh": "\n\n## Language rule\nRespond in Chinese (中文). Technical terms (RSI, MACD, P/E, EPS) may stay in English.\n",
        "en": "\n\n## Language rule\nRespond in English.\n",
    }

    def __init__(
        self,
        llm_provider=None,         # BaseLLMProvider 实例（可选，None 则用模板生成）
        data_router=None,          # DataRouter 实例（可选）
        on_token: Optional[Callable[[str], None]] = None,  # 流式 token 回调
        config: Optional[Dict] = None,
        lang: str = "zh",          # user language: "zh" | "en"
    ):
        self.llm      = llm_provider
        self.data     = data_router
        self.on_token = on_token
        self.config   = config or {}
        self.lang     = lang

    async def fetch_data(self, symbol: str) -> Dict[str, Any]:
        """
        从数据路由器获取分析所需数据。
        子类可覆盖此方法以自定义数据获取逻辑。
        """
        if not self.data:
            return {}
        result = {}
        try:
            q = self.data.quote(symbol)
            if q:
                result["quote"] = q.to_dict()
        except Exception as e:
            logger.debug(f"[{self.name}] fetch quote {symbol}: {e}")
        return result

    def _data_guard(self, quote: Dict[str, Any]) -> str:
        """Return a warning string if real data is unavailable; empty string if data is present."""
        price = quote.get("price") if quote else None
        if not price or float(price) == 0:
            return (
                "\n\n## ⛔ DATA UNAVAILABLE — STRICT RULES\n"
                "Real market data could not be fetched (price=0 or missing).\n"
                "You MUST:\n"
                "1. State clearly that no real data is available.\n"
                "2. NEVER invent specific prices, P/E ratios, EPS, revenue, RSI, MACD, or any numbers.\n"
                "3. NEVER give specific price targets, stop-loss levels, or entry prices.\n"
                "4. Give only qualitative analysis based on publicly known company characteristics.\n"
                "5. End with the signal word (BUY/HOLD/SELL) but with low confidence (≤40%).\n"
            )
        return ""

    async def _call_llm(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        quote: Optional[Dict[str, Any]] = None,
    ) -> str:
        """调用 LLM 生成分析文本（无 LLM 时返回空字符串）"""
        if not self.llm:
            return ""
        # Inject language rule + post-training facts + data guard into system prompt
        _lang_rule = self._LANG_RULES.get(self.lang, self._LANG_RULES["zh"])
        _data_warn = self._data_guard(quote or {})
        system = system + self._POST_TRAINING_FACTS + _lang_rule + _data_warn
        from providers.llm.base import Message
        messages = [
            Message(role="system", content=system),
            Message(role="user",   content=user),
        ]
        full_text = ""
        try:
            async for event in self.llm.stream(
                messages, max_tokens=max_tokens
            ):
                t = event.get("type")
                if t == "token":
                    tok = event.get("text", "")
                    full_text += tok
                    if self.on_token:
                        self.on_token(tok)
                elif t == "error":
                    logger.warning(f"[{self.name}] LLM 错误: {event.get('message')}")
                    break
        except Exception as e:
            logger.warning(f"[{self.name}] LLM 调用失败: {e}")
        return full_text.strip()

    @abstractmethod
    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        """
        核心分析方法。

        Args:
            symbol: 股票/资产代码
            data:   由 fetch_data() 预取的数据字典

        Returns:
            AgentResult
        """
        ...

    async def run(self, symbol: str) -> AgentResult:
        """完整执行：fetch_data → analyze，异常自动捕获。"""
        try:
            data   = await self.fetch_data(symbol)
            result = await self.analyze(symbol, data)
            return result
        except Exception as e:
            logger.error(f"[{self.name}] run({symbol}) 失败: {e}", exc_info=True)
            return AgentResult(
                agent=self.name, symbol=symbol,
                analysis="", confidence=0.0,
                error=str(e),
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
