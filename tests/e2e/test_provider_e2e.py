"""
Layer 3 — 端到端测试：真实 API 调用
=====================================
默认全部 skip，需 --e2e 标志。

用法：
    # SiliconFlow 免费 key（国内首选）
    SILICONFLOW_API_KEY=sk-xxx python3 -m pytest tests/e2e/ --e2e -v

    # providers.json 已有 key 时可直接：
    python3 -m pytest tests/e2e/ --e2e -v

每个测试会打印：provider / response 长度 / 延迟，便于肉眼核查。
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest

_CLI_DIR = str(pathlib.Path(__file__).parents[2])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)

from providers.llm.base import Message
from providers.llm.registry import _build_cfg, stream_cloud_fallback, _PROVIDER_CLASSES


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _has_key(provider_name: str) -> bool:
    """检查 provider 是否有可用 key（env 或 providers.json）。"""
    cfg = _build_cfg(provider_name)
    return cfg.is_configured() and bool(cfg.api_key)


def _provider_for_test(request: pytest.FixtureRequest) -> str | None:
    """返回 --provider 指定的 provider，若未指定返回 None（自动选）。"""
    return request.config.getoption("--provider")


async def _call_provider(provider_name: str, prompt: str) -> dict:
    """用指定 provider 发起一次 stream_cloud_fallback 调用，返回结果 dict。"""
    from providers.llm.registry import _load_provider_cfg_from_file
    from unittest.mock import patch

    # 仅让指定 provider 有 key（避免其他 provider 干扰顺序）
    real_cfg = _build_cfg(provider_name)
    if not real_cfg.api_key:
        pytest.skip(f"{provider_name} 没有可用 API key（env 或 providers.json）")

    def _fake_load(name):
        if name == provider_name:
            return {"api_key": real_cfg.api_key,
                    **({"base_url": real_cfg.base_url} if real_cfg.base_url else {})}
        return {}

    tokens: list[str] = []
    t0 = time.time()
    with patch("providers.llm.registry._load_provider_cfg_from_file",
               side_effect=_fake_load), \
         patch("providers.llm.registry._load_user_config", return_value={}):
        result = await stream_cloud_fallback(
            prompt, [],
            on_token=lambda t: tokens.append(t)
        )
    elapsed = time.time() - t0

    response = result.get("response", "")
    print(f"\n  provider={provider_name}  len={len(response)}  "
          f"latency={elapsed:.1f}s  success={result.get('success')}")
    if response:
        print(f"  preview: {response[:120].replace(chr(10), ' ')}")
    return result


# ── 单 Provider smoke tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deepseek_hello():
    """DeepSeek — 最小连通测试。"""
    if not _has_key("deepseek"):
        pytest.skip("无 DeepSeek API key")
    result = await _call_provider("deepseek", "你好，请用一句话自我介绍。")
    assert result["success"] is True
    assert len(result["response"]) > 5

@pytest.mark.asyncio
async def test_siliconflow_hello():
    """SiliconFlow — 中国大陆首选，免费 tier 可用。"""
    if not _has_key("siliconflow"):
        pytest.skip("无 SiliconFlow API key")
    result = await _call_provider("siliconflow", "你好，请用一句话自我介绍。")
    assert result["success"] is True
    assert len(result["response"]) > 5

@pytest.mark.asyncio
async def test_moonshot_hello():
    """Moonshot Kimi — 中国大陆可直连。"""
    if not _has_key("moonshot"):
        pytest.skip("无 Moonshot API key")
    result = await _call_provider("moonshot", "你好，请用一句话自我介绍。")
    assert result["success"] is True
    assert len(result["response"]) > 5


# ── 典型文案测试 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shenzhen_realty_prompt(request):
    """
    原始 Bug 验证：'深圳房价走势和折旧价' 这条 prompt 曾因无可用 provider
    而失败（"没有可用的 AI 模型"）。修复后应返回含"深圳"的 100+ 字回答。
    """
    provider = _provider_for_test(request) or _first_available()
    result = await _call_provider(provider, "深圳房价走势和折旧价")
    assert result["success"] is True, f"provider={provider} 失败: {result}"
    assert "深圳" in result["response"], "响应中应包含'深圳'"
    assert len(result["response"]) > 100, "响应过短，不足 100 字"

@pytest.mark.asyncio
async def test_fallback_chain_e2e(request):
    """stream_cloud_fallback 整体 fallback 链 — 至少一个 provider 成功。"""
    result = await stream_cloud_fallback("你好", [])
    assert result["success"] is True, f"全链 fallback 失败: {result}"
    assert result["provider"] != "none"

@pytest.mark.asyncio
async def test_stream_no_truncation(request):
    """请求 200 字以上回答，验证流式不截断。"""
    provider = _provider_for_test(request) or _first_available()
    result = await _call_provider(
        provider,
        "请详细分析深圳房地产市场的历史走势，包括2010年至今的主要阶段，"
        "不少于200字。"
    )
    assert result["success"] is True
    assert len(result["response"]) > 200, (
        f"响应仅 {len(result['response'])} 字，期望 >200"
    )

@pytest.mark.asyncio
async def test_coding_prompt(request):
    """编码 prompt — 响应应包含 Python 函数/import。"""
    provider = _provider_for_test(request) or _first_available()
    result = await _call_provider(
        provider,
        "用 Python 写一个简单的均线交叉策略，只需核心逻辑，约 20 行代码。"
    )
    assert result["success"] is True
    resp = result["response"]
    assert "def " in resp or "import " in resp, (
        "编码响应中未找到 def 或 import"
    )


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _first_available() -> str:
    """返回第一个有可用 key 的 provider 名。"""
    for name in ("siliconflow", "deepseek", "moonshot", "zhipu",
                 "dashscope", "openai", "anthropic"):
        if _has_key(name):
            return name
    pytest.skip("没有任何可用 API key，请先用 /apikey set 设置")
