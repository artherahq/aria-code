"""
Layer 4 — 流式管道测试：SSE 解析 & Token 聚合
==============================================
直接测试 OpenAICompatProvider.stream() 和 complete()，
用 conftest.py 的 FakeSSESession mock aiohttp，无真实网络。

覆盖：
• SSE 事件解析（token / thinking / done / error / tool_call）
• 中文 token 拼接正确性
• complete() 聚合行为
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Dict, List
from unittest.mock import patch

import aiohttp
import pytest

_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from conftest import FakeSSESession, FakeSSEResp, make_sse_mock  # noqa: E402

from providers.llm.base import Message, ProviderConfig
from providers.llm.openai_compat import SiliconFlowProvider, DeepSeekProvider


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _make_sse(content: str, finish: str | None = None) -> str:
    """构造单条 SSE data 行（content token）。"""
    payload = {
        "choices": [{
            "delta": {"content": content},
            "finish_reason": finish,
        }]
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}"


def _make_sse_thinking(reasoning: str) -> str:
    """构造 reasoning_content SSE 行（DeepSeek-R1 thinking）。"""
    payload = {
        "choices": [{
            "delta": {"reasoning_content": reasoning},
            "finish_reason": None,
        }]
    }
    return f"data: {json.dumps(payload)}"


def _make_sse_tool_chunk(name: str = "", args: str = "", idx: int = 0) -> str:
    tc: Dict = {"index": idx, "function": {}}
    if name:
        tc["function"]["name"] = name
    if args:
        tc["function"]["arguments"] = args
    payload = {"choices": [{"delta": {"tool_calls": [tc]}, "finish_reason": None}]}
    return f"data: {json.dumps(payload)}"


def _provider(api_key: str = "sk-test") -> SiliconFlowProvider:
    cfg = ProviderConfig(name="siliconflow", api_key=api_key)
    return SiliconFlowProvider(cfg)


def _deepseek_provider(api_key: str = "sk-test") -> DeepSeekProvider:
    cfg = ProviderConfig(name="deepseek", api_key=api_key)
    return DeepSeekProvider(cfg)


async def _collect_stream(provider, lines: List[str]) -> List[Dict]:
    """运行 provider.stream() with fake SSE，收集所有事件。"""
    msgs = [Message(role="user", content="test")]
    fake_session = FakeSSESession(FakeSSEResp(lines))
    events: List[Dict] = []
    with patch.object(aiohttp, "ClientSession", return_value=fake_session):
        async for ev in provider.stream(msgs):
            events.append(ev)
    return events


# ── TestSSEEventParsing ───────────────────────────────────────────────────────

class TestSSEEventParsing:

    @pytest.mark.asyncio
    async def test_content_yields_token_event(self):
        """content delta → {"type": "token", "text": ...}。"""
        lines = [_make_sse("Hello"), _make_sse("!", "stop"), "data: [DONE]"]
        events = await _collect_stream(_provider(), lines)
        tokens = [e for e in events if e["type"] == "token"]
        assert len(tokens) == 2
        assert tokens[0]["text"] == "Hello"
        assert tokens[1]["text"] == "!"

    @pytest.mark.asyncio
    async def test_reasoning_content_yields_thinking(self):
        """reasoning_content delta → {"type": "thinking", "text": ...}。"""
        lines = [
            _make_sse_thinking("思考过程..."),
            _make_sse("答案", "stop"),
            "data: [DONE]",
        ]
        events = await _collect_stream(_deepseek_provider(), lines)
        thinking = [e for e in events if e["type"] == "thinking"]
        assert len(thinking) == 1
        assert "思考过程" in thinking[0]["text"]

    @pytest.mark.asyncio
    async def test_done_line_skipped(self):
        """data: [DONE] 行不产生任何事件（或终止流）。"""
        lines = [_make_sse("ok", "stop"), "data: [DONE]"]
        events = await _collect_stream(_provider(), lines)
        # [DONE] 本身不被 yield 为事件
        assert not any(e.get("text") == "[DONE]" for e in events)

    @pytest.mark.asyncio
    async def test_http_401_yields_error(self):
        """HTTP 401 响应 → {"type": "error", "message": "HTTP 401: ..."}。"""
        msgs = [Message(role="user", content="test")]
        fake_session = FakeSSESession(FakeSSEResp(["Unauthorized"], status=401))
        events: List[Dict] = []
        with patch.object(aiohttp, "ClientSession", return_value=fake_session):
            async for ev in _provider().stream(msgs):
                events.append(ev)
        assert events[0]["type"] == "error"
        assert "401" in events[0]["message"]

    @pytest.mark.asyncio
    async def test_finish_reason_stop_yields_done(self):
        """finish_reason=stop → 流中出现 done 事件。"""
        lines = [_make_sse("final", "stop"), "data: [DONE]"]
        events = await _collect_stream(_provider(), lines)
        assert any(e["type"] == "done" for e in events)

    @pytest.mark.asyncio
    async def test_tool_call_chunks_assembled(self):
        """工具调用多 chunk → 拼接后 yield {"type": "tool_call", ...}。"""
        lines = [
            _make_sse_tool_chunk(name="get_price",    args='{"sym'),
            _make_sse_tool_chunk(name="",             args='bol":"AAPL"}'),
            # finish_reason=tool_calls triggers flush
            f'data: {json.dumps({"choices":[{"delta":{},"finish_reason":"tool_calls"}]})}',
            "data: [DONE]",
        ]
        events = await _collect_stream(_provider(), lines)
        tool_events = [e for e in events if e["type"] == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0]["name"] == "get_price"
        assert tool_events[0]["arguments"]["symbol"] == "AAPL"


# ── TestTokenAccumulation ─────────────────────────────────────────────────────

class TestTokenAccumulation:

    @pytest.mark.asyncio
    async def test_chinese_chars_no_corruption(self):
        """中文 token 逐字分块后拼接不乱码。"""
        chars = ["深", "圳", "房", "价", "走", "势"]
        lines = [_make_sse(c) for c in chars]
        lines[-1] = _make_sse(chars[-1], "stop")   # 最后一个带 stop
        lines.append("data: [DONE]")

        events = await _collect_stream(_provider(), lines)
        full = "".join(e["text"] for e in events if e["type"] == "token")
        assert full == "深圳房价走势"

    @pytest.mark.asyncio
    async def test_empty_content_no_token_event(self):
        """content 为空字符串的 delta 不应产生 token 事件。"""
        # 空 content delta（有时出现在 OpenAI 流首条）
        empty_line = f'data: {json.dumps({"choices":[{"delta":{"content":""},"finish_reason":None}]})}'
        lines = [empty_line, _make_sse("real", "stop"), "data: [DONE]"]
        events = await _collect_stream(_provider(), lines)
        tokens = [e for e in events if e["type"] == "token"]
        assert all(t["text"] != "" for t in tokens)

    @pytest.mark.asyncio
    async def test_multi_chunk_accumulates_in_order(self):
        """多 token chunk 按顺序累积，响应文本顺序正确。"""
        words = ["Hello", " ", "world", " from", " SiliconFlow"]
        lines = [_make_sse(w) for w in words]
        lines[-1] = _make_sse(words[-1], "stop")
        lines.append("data: [DONE]")

        events = await _collect_stream(_provider(), lines)
        text = "".join(e["text"] for e in events if e["type"] == "token")
        assert text == "Hello world from SiliconFlow"


# ── TestResponseTextIntegrity ─────────────────────────────────────────────────

class TestResponseTextIntegrity:

    @pytest.mark.asyncio
    async def test_complete_aggregates_tokens(self):
        """complete() 聚合所有 token 事件 → response 字段包含完整文本。"""
        lines = [
            _make_sse("深圳"),
            _make_sse("房价"),
            _make_sse("走势分析", "stop"),
            "data: [DONE]",
        ]
        msgs = [Message(role="user", content="test")]
        prov = _provider()
        fake_session = FakeSSESession(FakeSSEResp(lines))
        with patch.object(aiohttp, "ClientSession", return_value=fake_session):
            result = await prov.complete(msgs)

        assert result["success"] is True
        assert result["response"] == "深圳房价走势分析"

    @pytest.mark.asyncio
    async def test_tool_call_not_in_response_text(self):
        """工具调用事件不污染 response 文本字段。"""
        lines = [
            _make_sse_tool_chunk(name="read_file", args='{"path":"/tmp/x"}'),
            f'data: {json.dumps({"choices":[{"delta":{},"finish_reason":"tool_calls"}]})}',
            "data: [DONE]",
        ]
        msgs = [Message(role="user", content="test")]
        prov = _provider()
        fake_session = FakeSSESession(FakeSSEResp(lines))
        with patch.object(aiohttp, "ClientSession", return_value=fake_session):
            result = await prov.complete(msgs)

        assert result["success"] is True
        assert result["response"] == ""         # 无文本 token
        assert len(result["tool_calls"]) == 1   # 工具调用被记录

    @pytest.mark.asyncio
    async def test_http_error_returns_failure(self):
        """HTTP 错误 → complete() 返回 success=False。"""
        msgs = [Message(role="user", content="test")]
        prov = _provider()
        fake_session = FakeSSESession(FakeSSEResp(["Unauthorized"], status=401))
        with patch.object(aiohttp, "ClientSession", return_value=fake_session):
            result = await prov.complete(msgs)

        assert result["success"] is False
        assert "error" in result
