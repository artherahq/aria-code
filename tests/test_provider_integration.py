"""
Layer 2 — 集成测试：Provider Registry 运行时逻辑
=================================================
• stream_cloud_fallback  — fallback 链选择、回调、历史截断
• _try_provider          — 单 provider 流式尝试

全部使用 mock stream，无真实网络请求。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import unittest
from typing import AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)

import providers.llm.registry as _reg
from providers.llm.base import Message, ProviderConfig
from providers.llm.openai_compat import DeepSeekProvider, SiliconFlowProvider
from providers.llm.registry import _try_provider, stream_cloud_fallback
from packages.aria_services.provider_health import ProviderIssue, ProviderHealthRegistry


@pytest.fixture(autouse=True)
def _reset_provider_health():
    old = _reg.GLOBAL_PROVIDER_HEALTH
    _reg.GLOBAL_PROVIDER_HEALTH = ProviderHealthRegistry()
    try:
        yield
    finally:
        _reg.GLOBAL_PROVIDER_HEALTH = old

# 所有云端 provider env keys
_ALL_KEY_ENV_VARS = [
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GROQ_API_KEY", "TOGETHER_API_KEY", "DASHSCOPE_API_KEY",
    "SILICONFLOW_API_KEY", "MOONSHOT_API_KEY", "ZHIPUAI_API_KEY",
]


# ── 通用 mock 流生成器 ────────────────────────────────────────────────────────

async def _token_stream(self, messages, **kwargs) -> AsyncIterator[Dict]:
    """3 个 token 后 done。作为 stream() 的 patch 目标。"""
    yield {"type": "token", "text": "深圳"}
    yield {"type": "token", "text": "房价"}
    yield {"type": "token", "text": "走势"}
    yield {"type": "done"}


async def _error_stream(self, messages, **kwargs) -> AsyncIterator[Dict]:
    """立即 yield error 事件。"""
    yield {"type": "error", "message": "HTTP 401"}


async def _thinking_then_token_stream(self, messages, **kwargs) -> AsyncIterator[Dict]:
    """先 thinking，再 token，再 done。"""
    yield {"type": "thinking", "text": "...思考中..."}
    yield {"type": "token",   "text": "回答内容"}
    yield {"type": "done"}


# ── TestStreamCloudFallback ───────────────────────────────────────────────────

class TestStreamCloudFallback:

    def _no_env_keys(self):
        return patch.dict(os.environ, {k: "" for k in _ALL_KEY_ENV_VARS})

    @pytest.mark.asyncio
    async def test_file_key_enables_provider(self):
        """providers.json 有 deepseek key → deepseek 进入 cloud_specs，并成功调用。"""
        def _fake_load(name):
            return {"api_key": "sk-ds"} if name == "deepseek" else {}

        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", side_effect=_fake_load), \
             patch.object(_reg, "_load_user_config", return_value={}), \
             patch.object(DeepSeekProvider, "stream", _token_stream), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=True)):
            result = await stream_cloud_fallback("test", [])

        assert result["success"] is True
        assert result["provider"] == "deepseek"

    @pytest.mark.asyncio
    async def test_no_key_returns_no_cloud_provider(self):
        """没有任何 key → 返回 error=no_cloud_provider。"""
        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", return_value={}), \
             patch.object(_reg, "_load_user_config", return_value={}):
            result = await stream_cloud_fallback("test", [])

        assert result["success"] is False
        assert result["error"] == "no_cloud_provider"

    @pytest.mark.asyncio
    async def test_all_fail_returns_error(self):
        """所有 provider 的 _try_provider 都返回 None → all_providers_failed。"""
        def _fake_load(name):
            return {"api_key": "sk-x"}  # 所有都有 key

        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", side_effect=_fake_load), \
             patch.object(_reg, "_load_user_config", return_value={}), \
             patch.object(_reg, "_try_provider", AsyncMock(return_value=None)):
            result = await stream_cloud_fallback("test", [])

        assert result["success"] is False
        assert result["error"] == "all_providers_failed"

    @pytest.mark.asyncio
    async def test_first_success_wins(self):
        """deepseek 成功 → 不再尝试后续 provider，返回 deepseek 结果。"""
        deepseek_result = {
            "success": True, "response": "ok",
            "provider": "deepseek", "model": "deepseek-chat",
        }
        call_order: List[str] = []

        async def _mock_try(spec, msgs, **kwargs):
            call_order.append(spec.split("/")[0])
            if "deepseek" in spec:
                return deepseek_result
            return None

        def _fake_load(name):
            return {"api_key": "sk-x"}

        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", side_effect=_fake_load), \
             patch.object(_reg, "_load_user_config", return_value={}), \
             patch.object(_reg, "_try_provider", side_effect=_mock_try):
            result = await stream_cloud_fallback("test", [])

        assert result["provider"] == "deepseek"
        assert "siliconflow" not in call_order   # 第一个成功后不再继续

    @pytest.mark.asyncio
    async def test_on_token_callback_invoked(self):
        """on_token 回调应对每个 token 被调用一次。"""
        def _fake_load(name):
            return {"api_key": "sk-x"} if name == "deepseek" else {}

        tokens_received: List[str] = []

        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", side_effect=_fake_load), \
             patch.object(_reg, "_load_user_config", return_value={}), \
             patch.object(DeepSeekProvider, "stream", _token_stream), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=True)):
            await stream_cloud_fallback(
                "test", [],
                on_token=lambda t: tokens_received.append(t)
            )

        assert tokens_received == ["深圳", "房价", "走势"]

    @pytest.mark.asyncio
    async def test_history_truncated_to_12(self):
        """传入 20 条历史，消息列表中历史最多保留 12 条。"""
        history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(20)
        ]

        captured_msgs: List = []

        async def _capture_try(spec, msgs, **kwargs):
            captured_msgs.extend(msgs)
            return {"success": True, "response": "ok", "provider": "x", "model": "y"}

        def _fake_load(name):
            return {"api_key": "sk-x"} if name == "deepseek" else {}

        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", side_effect=_fake_load), \
             patch.object(_reg, "_load_user_config", return_value={}), \
             patch.object(_reg, "_try_provider", side_effect=_capture_try):
            await stream_cloud_fallback("new question", history)

        # 系统 msg(1) + 最多 12 条历史 + 当前 user msg(1) = 最多 14
        assert len(captured_msgs) <= 14

    @pytest.mark.asyncio
    async def test_cancel_event_stops_stream(self):
        """cancel_event 已设置 → _try_provider 被调用但 cancel 被传递。"""
        cancel_ev = asyncio.Event()
        cancel_ev.set()

        received_cancel = []

        async def _capture_try(spec, msgs, on_token=None, cancel_event=None, **kwargs):
            received_cancel.append(cancel_event)
            return None  # 模拟取消后无结果

        def _fake_load(name):
            return {"api_key": "sk-x"} if name == "deepseek" else {}

        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", side_effect=_fake_load), \
             patch.object(_reg, "_load_user_config", return_value={}), \
             patch.object(_reg, "_try_provider", side_effect=_capture_try):
            await stream_cloud_fallback("test", [], cancel_event=cancel_ev)

        assert received_cancel[0] is cancel_ev

    @pytest.mark.asyncio
    async def test_user_fallback_config_respected(self):
        """用户在 providers.yaml 里配置了自定义 fallback 链，应优先使用。"""
        user_cfg = {"fallback": ["siliconflow/deepseek-ai/DeepSeek-V3"]}
        call_order: List[str] = []

        async def _capture_try(spec, msgs, **kwargs):
            call_order.append(spec)
            return {"success": True, "response": "ok", "provider": "siliconflow", "model": "x"}

        def _fake_load(name):
            return {"api_key": "sk-sf"} if name == "siliconflow" else {}

        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", side_effect=_fake_load), \
             patch.object(_reg, "_load_user_config", return_value=user_cfg), \
             patch.object(_reg, "_try_provider", side_effect=_capture_try):
            result = await stream_cloud_fallback("test", [])

        assert call_order[0].startswith("siliconflow")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_cooldown_skips_failing_provider(self):
        """provider 在 cooldown 中时应被跳过，避免重复撞同一个坏端点。"""
        health = ProviderHealthRegistry()
        health.mark_issue(ProviderIssue("deepseek", "network", "down", True, cooldown_seconds=60))

        def _fake_load(name):
            return {"api_key": "sk-x"} if name == "deepseek" else {}

        with self._no_env_keys(), \
             patch.object(_reg, "_load_provider_cfg_from_file", side_effect=_fake_load), \
             patch.object(_reg, "_load_user_config", return_value={}), \
             patch.object(_reg, "_try_provider", AsyncMock(side_effect=AssertionError("should not call"))):
            result = await stream_cloud_fallback("test", [], health=health)

        assert result["success"] is False
        assert result["error"] == "no_cloud_provider"


# ── TestTryProvider ───────────────────────────────────────────────────────────

class TestTryProvider:

    @pytest.mark.asyncio
    async def test_success_returns_result(self):
        msgs = [Message(role="user", content="test")]

        with patch.object(_reg, "_load_provider_cfg_from_file",
                          return_value={"api_key": "sk-x"}), \
             patch.object(DeepSeekProvider, "stream", _token_stream), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=True)):
            result = await _try_provider("deepseek/deepseek-chat", msgs)

        assert result is not None
        assert result["success"] is True
        assert result["response"] == "深圳房价走势"
        assert result["provider"] == "deepseek"

    @pytest.mark.asyncio
    async def test_error_event_returns_none(self):
        msgs = [Message(role="user", content="test")]

        with patch.object(_reg, "_load_provider_cfg_from_file",
                          return_value={"api_key": "sk-x"}), \
             patch.object(DeepSeekProvider, "stream", _error_stream), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=True)):
            result = await _try_provider("deepseek/deepseek-chat", msgs)

        assert result is None

    @pytest.mark.asyncio
    async def test_not_available_returns_none(self):
        msgs = [Message(role="user", content="test")]

        with patch.object(_reg, "_load_provider_cfg_from_file",
                          return_value={"api_key": "sk-x"}), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=False)):
            result = await _try_provider("deepseek/deepseek-chat", msgs)

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self):
        """流完成但无文本 token（只有 done）→ None。"""

        async def _empty_stream(self, messages, **kwargs):
            yield {"type": "done"}

        msgs = [Message(role="user", content="test")]

        with patch.object(_reg, "_load_provider_cfg_from_file",
                          return_value={"api_key": "sk-x"}), \
             patch.object(DeepSeekProvider, "stream", _empty_stream), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=True)):
            result = await _try_provider("deepseek/deepseek-chat", msgs)

        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_provider_returns_none(self):
        msgs = [Message(role="user", content="test")]
        result = await _try_provider("unknown_provider_xyz/model", msgs)
        assert result is None

    @pytest.mark.asyncio
    async def test_on_token_called_correctly(self):
        """3 个 token 事件 → on_token 被调用 3 次，顺序正确。"""
        received: List[str] = []
        msgs = [Message(role="user", content="test")]

        with patch.object(_reg, "_load_provider_cfg_from_file",
                          return_value={"api_key": "sk-x"}), \
             patch.object(DeepSeekProvider, "stream", _token_stream), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=True)):
            await _try_provider("deepseek/deepseek-chat", msgs,
                                on_token=lambda t: received.append(t),
                                health=ProviderHealthRegistry())

        assert received == ["深圳", "房价", "走势"]

    @pytest.mark.asyncio
    async def test_thinking_not_in_on_token(self):
        """thinking 事件不应触发 on_token 回调。"""
        received: List[str] = []
        msgs = [Message(role="user", content="test")]

        with patch.object(_reg, "_load_provider_cfg_from_file",
                          return_value={"api_key": "sk-x"}), \
             patch.object(DeepSeekProvider, "stream", _thinking_then_token_stream), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=True)):
            await _try_provider("deepseek/deepseek-chat", msgs,
                                on_token=lambda t: received.append(t),
                                health=ProviderHealthRegistry())

        assert received == ["回答内容"]   # thinking 未进 on_token

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """provider 抛异常 → 返回 None，不传播。"""
        import aiohttp

        async def _raise_stream(self, messages, **kwargs):
            raise aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError("connection refused")
            )
            yield  # make it a generator

        msgs = [Message(role="user", content="test")]

        with patch.object(_reg, "_load_provider_cfg_from_file",
                          return_value={"api_key": "sk-x"}), \
             patch.object(DeepSeekProvider, "stream", _raise_stream), \
             patch.object(DeepSeekProvider, "is_available", AsyncMock(return_value=True)):
            result = await _try_provider("deepseek/deepseek-chat", msgs)

        assert result is None
