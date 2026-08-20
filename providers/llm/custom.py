"""声明式自定义 provider —— 用户只写配置文件，不写 Python 代码。

背景：registry.py 里内置了 12 个 provider，但它们的名字、base_url、环境变量
都是**硬编码**的。用户要接一个未列出的服务（自建 vLLM、公司内网网关、
Arthera 自己的云端 API、任何 OpenAI 兼容端点），此前唯一的办法是写一个
BaseLLMProvider 子类再调 register_provider()——对"只想填个地址和 key"的
用户门槛太高。

设计对标 codex 的 `model_providers`（codex-rs/model-provider-info/src/lib.rs）：
配置里**只写环境变量名，不写密钥值**。codex 那边同时提供了明文
`experimental_bearer_token` 字段，但源码注释明确写着 "Use of this config is
discouraged in favor of `env_key` for security reasons"——这里不提供明文字段，
只认 env_key，理由见下面 build_custom_provider 的说明。

用户配置示例（~/.aria/providers.yaml 或 ~/.arthera/providers.json）：

    llm:
      default: mycorp/qwen-72b

    model_providers:
      mycorp:
        name: 公司内网推理网关
        base_url: https://llm.internal.example.com/v1
        env_key: MYCORP_LLM_TOKEN
        env_key_instructions: 找基础架构组申请，写进 ~/.aria/.env
        model: qwen-72b
        http_headers:
          X-Tenant: research
        env_http_headers:
          X-Trace-Id: MYCORP_TRACE_ID

      arthera-cloud:
        name: Arthera 云端（Cloud Run）
        base_url: https://api.arthera.example/api/v2
        env_key: ARTHERA_API_TOKEN

声明后即可像内置 provider 一样使用：default / fallback / code_tasks 里都能写
`mycorp/<model>`。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .base import ProviderConfig
from .openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)

__all__ = ["CustomProviderSpec", "parse_custom_providers", "build_custom_provider"]

# 配置里出现这些键一律拒绝：它们意味着用户把密钥明文写进了配置文件。
# 配置文件常被连同项目一起备份、同步、甚至误提交，明文密钥的暴露面比环境
# 变量大一个量级。codex 提供了明文字段但自己在注释里劝退；这里直接不提供，
# 并在检测到时给出可操作的替代方案，而不是静默忽略——静默忽略会让用户以为
# 配好了，实际请求全部 401，排查成本更高。
_FORBIDDEN_INLINE_SECRET_KEYS = ("api_key", "apikey", "token", "bearer_token", "secret")


class CustomProviderSpec:
    """一个用户声明的 provider。"""

    __slots__ = ("id", "name", "base_url", "env_key", "env_key_instructions",
                 "model", "http_headers", "env_http_headers", "timeout")

    def __init__(self, provider_id: str, raw: Dict[str, Any]):
        self.id = provider_id
        self.name = str(raw.get("name") or provider_id)
        self.base_url = str(raw.get("base_url") or "").rstrip("/")
        self.env_key = raw.get("env_key") or None
        self.env_key_instructions = raw.get("env_key_instructions") or ""
        self.model = raw.get("model") or None
        self.http_headers = dict(raw.get("http_headers") or {})
        self.env_http_headers = dict(raw.get("env_http_headers") or {})
        self.timeout = int(raw.get("timeout") or 120)

    def resolved_headers(self) -> Dict[str, str]:
        """静态头 + 从环境变量取值的头。环境变量缺失时**不发**该头，
        而不是发一个空值——空值头在很多网关那里会被当成"显式声明为空"，
        比缺失更难排查。"""
        headers = dict(self.http_headers)
        for header_name, env_name in self.env_http_headers.items():
            value = os.environ.get(str(env_name), "").strip()
            if value:
                headers[str(header_name)] = value
        return headers

    def api_key(self) -> str:
        return os.environ.get(str(self.env_key), "").strip() if self.env_key else ""

    def missing_key_hint(self) -> str:
        base = f"provider '{self.id}' 需要环境变量 {self.env_key}，当前未设置或为空。"
        return f"{base} {self.env_key_instructions}".strip()


def parse_custom_providers(cfg: Dict[str, Any]) -> Dict[str, CustomProviderSpec]:
    """从用户配置解析 model_providers 段。

    配置有问题时记录 warning 并跳过该条，不抛异常——一个写错的自定义 provider
    不该让整个 CLI 起不来，其余 provider 应当照常可用。
    """
    raw_section = cfg.get("model_providers") if isinstance(cfg, dict) else None
    if not isinstance(raw_section, dict):
        return {}

    specs: Dict[str, CustomProviderSpec] = {}
    for provider_id, raw in raw_section.items():
        pid = str(provider_id).strip().lower()
        if not pid or not isinstance(raw, dict):
            logger.warning("model_providers 中跳过无效条目: %r", provider_id)
            continue

        inline = [k for k in raw if str(k).lower() in _FORBIDDEN_INLINE_SECRET_KEYS]
        if inline:
            logger.warning(
                "model_providers['%s'] 含明文密钥字段 %s —— 已忽略该 provider。"
                "请改用 env_key 指向环境变量名，把值写进 ~/.aria/.env："
                "\n  %s:\n    base_url: ...\n    env_key: %s_API_KEY",
                pid, inline, pid, pid.upper().replace("-", "_"),
            )
            continue

        if not raw.get("base_url"):
            logger.warning("model_providers['%s'] 缺少 base_url —— 已跳过", pid)
            continue

        specs[pid] = CustomProviderSpec(pid, raw)

    if specs:
        logger.info("✓ 已加载 %d 个自定义 provider: %s", len(specs), ", ".join(sorted(specs)))
    return specs


def build_custom_provider(spec: CustomProviderSpec, model: Optional[str] = None):
    """按 spec 构造一个 OpenAI 兼容 provider 实例。

    复用 OpenAICompatProvider 而不是新写一套 HTTP 客户端：绝大多数第三方与
    自建推理服务（vLLM / LM Studio / Ollama 的 OpenAI 兼容层 / 各家云厂商网关）
    都实现了 /chat/completions，重写一遍只会多一处要维护的重试与流式解析。
    """
    config = ProviderConfig(
        name=spec.id,
        api_key=spec.api_key() or None,
        base_url=spec.base_url,
        model=model or spec.model,
        timeout=spec.timeout,
        extra={"http_headers": spec.resolved_headers(), "custom": True},
    )

    class _DeclaredProvider(OpenAICompatProvider):
        provider_name = spec.id
        DEFAULT_BASE_URL = spec.base_url
        DEFAULT_MODEL = spec.model or ""

    _DeclaredProvider.__name__ = f"Declared_{spec.id.replace('-', '_')}"
    return _DeclaredProvider(config)
