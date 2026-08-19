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
from typing import Any, Callable, Dict, List, Optional, Generator, AsyncGenerator

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

    # Time-sensitive claims must come from a tool result with a timestamp, not
    # from a hard-coded prompt.  This is appended to every agent because a
    # wrong IPO/symbol/event claim can otherwise contaminate all specialisms.
    _TIME_SENSITIVE_FACT_POLICY = (
        "\n\n## Time-sensitive fact policy\n"
        "- Do not treat examples, aliases, model memory, or internal notes as current facts.\n"
        "- Verify ticker identity, IPO/listing status, prices, corporate events, sports results, "
        "and dates with an appropriate tool or source before stating them as facts.\n"
        "- If a current fact cannot be verified, say that it is unverified instead of guessing.\n"
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
        on_thought: Optional[Callable[[str], None]] = None, # 思考过程回调
        on_tool_start: Optional[Callable[[str, Dict], None]] = None, # 工具调用开始回调
        on_tool_end: Optional[Callable[[str, Any], None]] = None, # 工具调用结束回调
        config: Optional[Dict] = None,
        lang: str = "zh",          # user language: "zh" | "en"
    ):
        self.llm      = llm_provider
        self.data     = data_router
        self.on_token = on_token
        self.on_thought = on_thought
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end
        self.config   = config or {}
        self.lang     = lang
        self.memory   = []         # Short-term conversation history for multi-turn context

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

    async def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        执行本地工具或 MCP 工具（可被子类重写扩展）。
        实际项目中可将 aria-skills 暴露为工具。
        """
        if self.on_tool_start:
            self.on_tool_start(tool_name, tool_args)

        result = None
        try:
            # 路由到数据层或其他 MCP Client 执行
            if self.data and hasattr(self.data, "execute_tool"):
                result = await self.data.execute_tool(tool_name, tool_args)
            else:
                result = f"Error: Tool {tool_name} not implemented or data router unavailable."
        except Exception as e:
            result = f"Error executing {tool_name}: {e}"

        if self.on_tool_end:
            self.on_tool_end(tool_name, result)

        import json
        return json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result

    async def _call_llm(
        self,
        system: str,
        user: str,
        max_tokens: int = 800,
        quote: Optional[Dict[str, Any]] = None,
        max_tool_loops: int = 5,
    ) -> str:
        """调用 LLM 生成分析文本，内置 Tool Calling 的执行循环"""
        if not self.llm:
            return ""
        # Inject language rule + time-sensitive fact policy + data guard.
        _lang_rule = self._LANG_RULES.get(self.lang, self._LANG_RULES["zh"])
        _data_warn = self._data_guard(quote or {})
        system = system + self._TIME_SENSITIVE_FACT_POLICY + _lang_rule + _data_warn
        from providers.llm.base import Message

        # 自动注入 DAG pipeline 中的 upstream context
        if hasattr(self, "_current_data") and self._current_data:
            upstream = self._current_data.get("upstream_context")
            if upstream:
                user += "\n\n## ⬆️ 团队/上游 Agent 提供的背景上下文 (Upstream Insights)\n"
                user += "请在分析中参考以下来自其他专家 Agent 的核心观点：\n"
                user += "\n".join(upstream)

        # 融入历史记忆
        if not self.memory:
            messages = [
                Message(role="system", content=system),
                Message(role="user",   content=user),
            ]
        else:
            messages = list(self.memory)
            messages.append(Message(role="user", content=user))

        full_text = ""
        loop_count = 0

        while loop_count < max_tool_loops:
            loop_count += 1
            current_text = ""
            tool_calls = []

            try:
                async for event in self.llm.stream(
                    messages, max_tokens=max_tokens
                ):
                    t = event.get("type")
                    if t == "token":
                        tok = event.get("text", "")
                        current_text += tok
                        full_text += tok
                        if self.on_token:
                            self.on_token(tok)
                    elif t == "thought" and self.on_thought:
                        self.on_thought(event.get("text", ""))
                    elif t == "tool_call":
                        # 记录工具调用意图
                        tool_calls.append({
                            "name": event.get("name"),
                            "args": event.get("args", {})
                        })
                    elif t == "tool_start" and self.on_tool_start:
                        self.on_tool_start(event.get("tool_name", ""), event.get("tool_args", {}))
                    elif t == "tool_end" and self.on_tool_end:
                        self.on_tool_end(event.get("tool_name", ""), event.get("tool_result", None))
                    elif t == "error":
                        logger.warning(f"[{self.name}] LLM 错误: {event.get('message')}")
                        break
            except Exception as e:
                logger.warning(f"[{self.name}] LLM 调用失败: {e}")
                break

            # 将当前 Assistant 的回复加入消息流
            messages.append(Message(role="assistant", content=current_text))

            # 如果没有工具调用，说明 LLM 完成了最终回答，跳出循环
            if not tool_calls:
                break

            # 有工具调用，则逐一执行，并把结果喂回 LLM
            for tc in tool_calls:
                tool_res = await self._execute_tool(tc["name"], tc["args"])
                # 注意：实际 provider 中 Message 结构可能需要 tool_call_id
                # 这里假设通用 Message 能够携带 role="tool"
                messages.append(Message(role="tool", name=tc["name"], content=tool_res))

        # 更新短期记忆
        self.memory = messages

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
