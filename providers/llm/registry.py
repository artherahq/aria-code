"""
providers/llm/registry.py — LLM Provider 注册中心
==================================================
• 从 ~/.aria/providers.yaml 或 .aria.json 加载用户配置
• 按优先级自动路由：本地 Ollama → DeepSeek → OpenAI → Anthropic → Groq
• 提供 stream_cloud_fallback() 供 aria_cli.py 调用

用户配置示例 (~/.aria/providers.yaml):
    llm:
      default: ollama/qwen2.5:7b
      fallback:
        - deepseek/deepseek-chat
        - openai/gpt-4o-mini
        - anthropic/claude-3-5-haiku-latest
      code_tasks: ollama/qwen2.5-coder:7b
      heavy_analysis: anthropic/claude-3-5-sonnet-20241022
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple, Type

import yaml

from .base import BaseLLMProvider, Message, ProviderConfig
from .ollama import OllamaProvider
from .openai_compat import (
    DeepSeekProvider, OpenAIProvider, GroqProvider,
    TogetherProvider, DashScopeProvider, LMStudioProvider,
    SiliconFlowProvider, MoonshotProvider, ZhiPuProvider,
)
from .anthropic import AnthropicProvider

logger = logging.getLogger(__name__)

# ── Provider 目录：name → class ──────────────────────────────────────────────
_PROVIDER_CLASSES: Dict[str, Type[BaseLLMProvider]] = {
    "ollama":       OllamaProvider,
    "deepseek":     DeepSeekProvider,
    "openai":       OpenAIProvider,
    "anthropic":    AnthropicProvider,
    "groq":         GroqProvider,
    "together":     TogetherProvider,
    "dashscope":    DashScopeProvider,
    "lmstudio":     LMStudioProvider,
    # 国内可访问
    "siliconflow":  SiliconFlowProvider,
    "moonshot":     MoonshotProvider,
    "zhipu":        ZhiPuProvider,
}

# ── 默认 fallback 优先级（无用户配置时）────────────────────────────────────
# 国内环境优先走 DeepSeek / SiliconFlow / DashScope，再尝试 OpenAI / Groq
_DEFAULT_FALLBACK_CHAIN = [
    ("ollama",       None,                    None),
    ("deepseek",     "DEEPSEEK_API_KEY",      "deepseek-chat"),
    ("siliconflow",  "SILICONFLOW_API_KEY",   "deepseek-ai/DeepSeek-V3"),
    ("dashscope",    "DASHSCOPE_API_KEY",     "qwen-plus"),
    ("moonshot",     "MOONSHOT_API_KEY",      "moonshot-v1-8k"),
    ("zhipu",        "ZHIPUAI_API_KEY",       "glm-4-flash"),
    ("openai",       "OPENAI_API_KEY",        "gpt-4o-mini"),
    ("anthropic",    "ANTHROPIC_API_KEY",     "claude-3-5-haiku-latest"),
    ("groq",         "GROQ_API_KEY",          "llama-3.3-70b-versatile"),
]

# ── 用户可注册自定义 provider ─────────────────────────────────────────────────
def register_provider(name: str, cls: Type[BaseLLMProvider]) -> None:
    """注册自定义 provider 类（供插件/用户扩展使用）"""
    _PROVIDER_CLASSES[name.lower()] = cls
    logger.info(f"✓ 注册自定义 provider: {name}")


# ── 配置加载 ──────────────────────────────────────────────────────────────────
_CONFIG_PATHS = [
    # ~/.arthera/providers.json is the primary path used by the aria-code CLI (/apikey command)
    Path.home() / ".arthera" / "providers.json",
    # Legacy / alternative paths
    Path.home() / ".aria" / "providers.yaml",
    Path.home() / ".aria" / "providers.json",
    Path(".aria.json"),
    Path(".aria.yaml"),
]

def _load_user_config() -> Dict:
    for p in _CONFIG_PATHS:
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    data = yaml.safe_load(f) if p.suffix in (".yaml",".yml") \
                           else __import__("json").load(f)
                    return data.get("llm", data) if isinstance(data, dict) else {}
            except Exception as e:
                logger.debug(f"加载配置 {p} 失败: {e}")
    return {}


def _load_provider_cfg_from_file(name: str) -> Dict[str, str]:
    """
    从 ~/.arthera/providers.json 的 llm 节读取指定 provider 的 api_key / base_url。
    这是 /apikey set 命令写入的位置；ProviderConfig.from_env() 只读环境变量，
    此函数补足文件侧的配置，让两者合并后才能正确工作。
    """
    import json as _json
    primary = Path.home() / ".arthera" / "providers.json"
    for p in [primary] + _CONFIG_PATHS:
        if not p.exists():
            continue
        try:
            raw  = _json.loads(p.read_text(encoding="utf-8")) if p.suffix == ".json" \
                   else yaml.safe_load(p.read_text(encoding="utf-8"))
            llm  = raw.get("llm", raw) if isinstance(raw, dict) else {}
            entry = llm.get(name.lower(), {})
            if entry:
                return {k: v for k, v in entry.items() if v}
        except Exception:
            pass
    return {}


def _parse_provider_spec(spec: str) -> Tuple[str, Optional[str]]:
    """
    解析 'deepseek/deepseek-chat' → ('deepseek', 'deepseek-chat')
    解析 'ollama'                  → ('ollama', None)
    """
    if "/" in spec:
        name, model = spec.split("/", 1)
        return name.strip().lower(), model.strip()
    return spec.strip().lower(), None


def get_provider(
    spec: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> BaseLLMProvider:
    """
    按 spec 字符串实例化 provider。

    Examples:
        get_provider("ollama/qwen2.5:7b")
        get_provider("deepseek/deepseek-chat")
        get_provider("anthropic/claude-3-5-haiku-latest")
    """
    name, model = _parse_provider_spec(spec)
    cls = _PROVIDER_CLASSES.get(name)
    if not cls:
        raise ValueError(
            f"未知 provider: '{name}'。"
            f"可用: {', '.join(_PROVIDER_CLASSES)}"
        )
    cfg = _build_cfg(name, model)
    # 调用方显式传入的参数优先级最高
    if api_key:
        cfg.api_key = api_key
    if base_url:
        cfg.base_url = base_url
    return cls(cfg)


def list_available_providers() -> List[Dict[str, Any]]:
    """返回所有 provider 及其可用状态（同步，用于 /config 命令显示）"""
    result = []
    for name, cls in _PROVIDER_CLASSES.items():
        cfg       = _build_cfg(name)          # 合并环境变量 + providers.json
        available = cfg.is_configured()
        result.append({
            "name":      name,
            "available": available,
            "local":     cls.local,
            "tools":     cls.supports_tools,
            "thinking":  cls.supports_thinking,
        })
    return result


def _build_cfg(name: str, model: Optional[str] = None) -> ProviderConfig:
    """
    构建 ProviderConfig：环境变量优先，再回落到 providers.json，
    确保 /apikey set 保存的 key 能被实际使用。
    """
    cfg = ProviderConfig.from_env(name)
    file_cfg = _load_provider_cfg_from_file(name)

    # 补充 api_key（文件里的）— 环境变量已在 from_env() 中优先读取；
    # 文件是后备：提示用户改用环境变量以避免明文存储 key。
    if not cfg.api_key and file_cfg.get("api_key"):
        cfg.api_key = file_cfg["api_key"]
        _env_names = {
            "deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY", "groq": "GROQ_API_KEY",
            "siliconflow": "SILICONFLOW_API_KEY", "moonshot": "MOONSHOT_API_KEY",
            "zhipu": "ZHIPUAI_API_KEY", "dashscope": "DASHSCOPE_API_KEY",
        }
        if name.lower() in _env_names:
            logger.warning(
                "⚠ API key for '%s' loaded from ~/.arthera/providers.json (plaintext). "
                "Migrate to env var: export %s=<key>  then remove api_key from providers.json.",
                name, _env_names[name.lower()],
            )
    # 补充 base_url（支持用户自定义端点 / 代理）
    if not cfg.base_url and file_cfg.get("base_url"):
        cfg.base_url = file_cfg["base_url"]

    if model:
        cfg.model = model
    return cfg


async def _try_provider(
    spec: str,
    messages: List[Message],
    on_token: Optional[Callable] = None,
    cancel_event=None,
) -> Optional[Dict[str, Any]]:
    """尝试用指定 provider 完成对话，失败返回 None。"""
    try:
        name, model = _parse_provider_spec(spec)
        cls = _PROVIDER_CLASSES.get(name)
        if not cls:
            return None

        cfg      = _build_cfg(name, model)
        provider = cls(cfg)

        if not await provider.is_available():
            logger.debug(f"[{name}] 不可用，跳过")
            return None

        logger.info(f"[{name}] 尝试生成响应 (model={cfg.model})")
        full_text = ""
        async for event in provider.stream(
            messages, cancel_event=cancel_event
        ):
            t = event.get("type")
            if t == "token":
                tok = event.get("text", "")
                full_text += tok
                if on_token:
                    on_token(tok)
            elif t == "error":
                logger.warning(f"[{name}] 流式错误: {event.get('message')}")
                return None
            elif t == "done":
                break

        if not full_text.strip():
            return None

        return {
            "success":  True,
            "response": full_text,
            "provider": name,
            "model":    cfg.model or "unknown",
        }
    except Exception as e:
        logger.debug(f"[{spec}] 异常: {e}")
        return None


async def stream_cloud_fallback(
    message: str,
    history: List[Dict],
    on_token: Optional[Callable] = None,
    cancel_event=None,
) -> Dict[str, Any]:
    """
    CLI fallback 入口：当 Ollama 不可用时调用。
    按优先级依次尝试云端 provider，首个成功的直接返回。

    优先级:
      1. 用户 ~/.aria/providers.yaml 里的 fallback 列表
      2. 内置默认链: DeepSeek → OpenAI → Anthropic → Groq → DashScope
    """
    # 构建消息列表
    msgs: List[Message] = [
        Message(role="system", content=(
            "You are Aria, an AI-native quantitative investment assistant. "
            "Answer concisely and accurately. If asked about real-time data "
            "you cannot access, say so clearly."
        ))
    ]
    for h in (history or [])[-12:]:
        role = h.get("role", "user")
        if role in ("user", "assistant"):
            msgs.append(Message(role=role, content=h.get("content", "")))
    msgs.append(Message(role="user", content=message))

    # 加载用户配置的 fallback 链
    user_cfg = _load_user_config()
    user_fallback: List[str] = user_cfg.get("fallback", [])

    # 云端 provider 列表（跳过本地）
    cloud_specs: List[str] = []
    for spec in user_fallback:
        name, _ = _parse_provider_spec(spec)
        cls = _PROVIDER_CLASSES.get(name)
        if cls and not cls.local:
            cloud_specs.append(spec)

    # 补充内置默认链中未出现的
    for name, env_var, model in _DEFAULT_FALLBACK_CHAIN:
        cls = _PROVIDER_CLASSES.get(name)
        if not cls or cls.local:
            continue
        spec = f"{name}/{model}" if model else name
        if not any(s.startswith(name) for s in cloud_specs):
            # 环境变量 OR providers.json 任一有 key 即可
            has_key = (env_var and os.getenv(env_var)) or \
                      bool(_load_provider_cfg_from_file(name).get("api_key"))
            if has_key:
                cloud_specs.append(spec)

    if not cloud_specs:
        return {
            "success": False,
            "error":   "no_cloud_provider",
            "response": "",
            "provider": "none",
        }

    for spec in cloud_specs:
        result = await _try_provider(spec, msgs, on_token=on_token,
                                     cancel_event=cancel_event)
        if result:
            return result

    return {
        "success":  False,
        "error":    "all_providers_failed",
        "response": "",
        "provider": "none",
    }
