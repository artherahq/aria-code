"""声明式自定义 provider：用户只写配置，不写 Python 代码。

registry.py 内置 12 个 provider，名字/base_url/环境变量全是硬编码。此前用户
要接一个未列出的服务（自建 vLLM、公司内网网关、Arthera 云端、任何 OpenAI
兼容端点），只能写 BaseLLMProvider 子类再调 register_provider()——对"只想
填个地址和 key"的人门槛过高。

设计对标 codex 的 model_providers（codex-rs/model-provider-info/src/lib.rs）：
配置里只写**环境变量名**，不写密钥值。codex 另有明文 experimental_bearer_token
字段，但其源码注释写着 "discouraged in favor of env_key for security reasons"，
所以这里干脆不提供明文入口。
"""

from __future__ import annotations

import pytest

yaml = pytest.importorskip("yaml", reason="需要 pyyaml")

from providers.llm.custom import (  # noqa: E402
    build_custom_provider,
    parse_custom_providers,
)

CONFIG = yaml.safe_load("""
llm:
  default: mycorp/qwen-72b
model_providers:
  mycorp:
    name: 内网网关
    base_url: https://llm.internal.example.com/v1
    env_key: MYCORP_LLM_TOKEN
    env_key_instructions: 找基础架构组申请
    model: qwen-72b
    http_headers:
      X-Tenant: research
    env_http_headers:
      X-Trace-Id: MYCORP_TRACE_ID
  no-base-url:
    env_key: WHATEVER
  inline-secret:
    base_url: https://x.example
    api_key: sk-plaintext
""")


def test_valid_provider_is_parsed():
    specs = parse_custom_providers(CONFIG)
    assert "mycorp" in specs
    assert specs["mycorp"].base_url == "https://llm.internal.example.com/v1"
    assert specs["mycorp"].model == "qwen-72b"


def test_inline_plaintext_secret_is_rejected():
    """配置文件常被备份/同步/误提交，明文密钥的暴露面比环境变量大一个量级。

    关键是**拒绝**而不是静默忽略该字段——静默忽略会让用户以为配好了，实际
    请求全部 401，排查成本更高。
    """
    specs = parse_custom_providers(CONFIG)
    assert "inline-secret" not in specs


def test_entry_without_base_url_is_skipped_not_fatal():
    """一个写错的条目不该让整个 CLI 起不来，其余 provider 必须照常可用。"""
    specs = parse_custom_providers(CONFIG)
    assert "no-base-url" not in specs
    assert "mycorp" in specs, "一个坏条目连累了好条目"


def test_api_key_comes_from_environment(monkeypatch):
    spec = parse_custom_providers(CONFIG)["mycorp"]
    monkeypatch.delenv("MYCORP_LLM_TOKEN", raising=False)
    assert spec.api_key() == ""
    assert "MYCORP_LLM_TOKEN" in spec.missing_key_hint()
    assert "找基础架构组申请" in spec.missing_key_hint(), "提示里应带上用户写的获取说明"

    monkeypatch.setenv("MYCORP_LLM_TOKEN", "tok_abc")
    assert spec.api_key() == "tok_abc"


def test_env_backed_headers_are_omitted_when_unset(monkeypatch):
    """环境变量缺失时不发该头，而不是发空值——空值头在很多网关那里会被当成
    '显式声明为空'，比缺失更难排查。"""
    spec = parse_custom_providers(CONFIG)["mycorp"]
    monkeypatch.delenv("MYCORP_TRACE_ID", raising=False)
    headers = spec.resolved_headers()
    assert headers == {"X-Tenant": "research"}
    assert "X-Trace-Id" not in headers

    monkeypatch.setenv("MYCORP_TRACE_ID", "trace-1")
    assert spec.resolved_headers()["X-Trace-Id"] == "trace-1"


def test_build_provider_uses_openai_compatible_transport(monkeypatch):
    monkeypatch.setenv("MYCORP_LLM_TOKEN", "tok_abc")
    spec = parse_custom_providers(CONFIG)["mycorp"]
    provider = build_custom_provider(spec, model="qwen-72b")
    assert provider.config.base_url == "https://llm.internal.example.com/v1"
    assert provider.config.model == "qwen-72b"
    assert provider.config.api_key == "tok_abc"
    assert provider.config.extra["http_headers"]["X-Tenant"] == "research"


def test_missing_model_providers_section_is_harmless():
    assert parse_custom_providers({"llm": {"default": "ollama/x"}}) == {}
    assert parse_custom_providers({}) == {}
