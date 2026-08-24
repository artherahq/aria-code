"""按用户的**实际环境**生成 providers.yaml，而不是发一份通用模板。

为什么不是写死一份文档让用户照抄：通用模板对每个人都是"差不多但不对"——
装了 Ollama 的人不需要被告知怎么配 OpenAI，配了 DEEPSEEK_API_KEY 的人不该看到
一堆用不上的占位符，而真正卡住他的（本地服务没起来 / key 名字拼错）模板里
一个字都不会提。

这里的做法是先探测再生成：
  - 本地推理服务（Ollama / LM Studio）是否在跑、跑着哪些模型
  - 哪些 provider 的环境变量已经设了
  - 用户已有的配置文件里声明过什么

然后产出一份**只包含这台机器上真的能用的东西**的配置，并把"还差什么、
去哪拿"作为注释写进同一个文件——用户不必在文档和配置之间来回对照。

探测全部是只读的：不发请求到任何云端 provider（那需要消耗用户的额度，也会
把 key 泄给一次纯粹的探活），只查本地端口和环境变量。云端 provider 的判定
标准就是"环境变量在不在"，这跟 registry 实际取 key 的方式完全一致。
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

__all__ = ["Finding", "probe_environment", "render_providers_yaml", "suggest_default_chain"]

# (provider id, 环境变量, 默认模型, 去哪拿 key)
_CLOUD_PROVIDERS: List[tuple] = [
    ("deepseek",    "DEEPSEEK_API_KEY",    "deepseek-chat",                  "https://platform.deepseek.com/api_keys"),
    ("siliconflow", "SILICONFLOW_API_KEY", "deepseek-ai/DeepSeek-V3",        "https://cloud.siliconflow.cn/account/ak"),
    ("dashscope",   "DASHSCOPE_API_KEY",   "qwen-plus",                      "https://bailian.console.aliyun.com/"),
    ("moonshot",    "MOONSHOT_API_KEY",    "moonshot-v1-8k",                 "https://platform.moonshot.cn/console/api-keys"),
    ("zhipu",       "ZHIPUAI_API_KEY",     "glm-4-flash",                    "https://open.bigmodel.cn/usercenter/apikeys"),
    ("openai",      "OPENAI_API_KEY",      "gpt-4o-mini",                    "https://platform.openai.com/api-keys"),
    ("anthropic",   "ANTHROPIC_API_KEY",   "claude-3-5-haiku-latest",        "https://console.anthropic.com/settings/keys"),
    ("groq",        "GROQ_API_KEY",        "llama-3.3-70b-versatile",        "https://console.groq.com/keys"),
    ("together",    "TOGETHER_API_KEY",    "meta-llama/Llama-3.3-70B-Instruct-Turbo", "https://api.together.xyz/settings/api-keys"),
]

# 本地推理服务：(provider id, 默认地址, 列模型的路径)
_LOCAL_PROVIDERS: List[tuple] = [
    ("ollama",   os.environ.get("OLLAMA_BASE_URL",   "http://localhost:11434"), "/api/tags"),
    ("lmstudio", os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234"),  "/v1/models"),
]


@dataclass
class Finding:
    """一个 provider 的探测结果。"""
    provider: str
    kind: str                       # "local" | "cloud"
    available: bool
    detail: str = ""
    models: List[str] = field(default_factory=list)
    env_key: Optional[str] = None
    signup_url: str = ""
    base_url: str = ""


def _port_open(url: str, timeout: float = 0.4) -> bool:
    """只做 TCP 探活。比发 HTTP 请求快得多，且本地服务没起来时能立刻返回，
    不会让整个探测卡在连接超时上。"""
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _fetch_json(url: str, timeout: float = 1.5) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def _local_models(provider: str, base_url: str, path: str) -> List[str]:
    payload = _fetch_json(base_url.rstrip("/") + path)
    if not payload:
        return []
    if provider == "ollama":
        return [m.get("name", "") for m in payload.get("models", []) if m.get("name")]
    return [m.get("id", "") for m in payload.get("data", []) if m.get("id")]


def probe_environment(*, check_local: bool = True) -> List[Finding]:
    """探测这台机器上有什么。只读、不消耗任何额度。"""
    findings: List[Finding] = []

    if check_local:
        for provider, base_url, path in _LOCAL_PROVIDERS:
            if not _port_open(base_url):
                findings.append(Finding(
                    provider=provider, kind="local", available=False,
                    base_url=base_url,
                    detail=f"{base_url} 未监听（服务没启动，或端口不是默认值）",
                ))
                continue
            models = _local_models(provider, base_url, path)
            findings.append(Finding(
                provider=provider, kind="local", available=bool(models),
                base_url=base_url, models=models,
                detail=(f"在跑，{len(models)} 个模型" if models
                        else "端口通但没列出模型（可能还没 pull 过任何模型）"),
            ))

    for provider, env_key, _model, signup in _CLOUD_PROVIDERS:
        has_key = bool(os.environ.get(env_key, "").strip())
        findings.append(Finding(
            provider=provider, kind="cloud", available=has_key,
            env_key=env_key, signup_url=signup,
            detail=(f"{env_key} 已设置" if has_key else f"{env_key} 未设置"),
        ))

    return findings


def suggest_default_chain(findings: List[Finding]) -> tuple[Optional[str], List[str]]:
    """按探测结果给出 default 与 fallback 链。

    排序原则：本地优先（零成本、不出网、隐私最好），其次是已配好 key 的云端。
    没配 key 的一律不进链——把用不了的东西写进 fallback 只会在真正需要兜底时
    连续失败几次才走到能用的那个。
    """
    local_ready = [f for f in findings if f.kind == "local" and f.available]
    cloud_ready = [f for f in findings if f.kind == "cloud" and f.available]

    chain: List[str] = []
    for f in local_ready:
        model = f.models[0] if f.models else ""
        chain.append(f"{f.provider}/{model}" if model else f.provider)

    cloud_default = {p: m for p, _e, m, _s in _CLOUD_PROVIDERS}
    for f in cloud_ready:
        chain.append(f"{f.provider}/{cloud_default.get(f.provider, '')}".rstrip("/"))

    return (chain[0] if chain else None), chain[1:]


def render_providers_yaml(findings: List[Finding]) -> str:
    """生成配置文本。缺什么、去哪拿，作为注释写在同一个文件里。"""
    default, fallback = suggest_default_chain(findings)
    lines: List[str] = [
        "# ~/.aria/providers.yaml",
        "# 由 `aria-code /providers init` 按本机实际环境生成。",
        "# 密钥一律不写在这里——只写环境变量名，值放 ~/.aria/.env。",
        "",
    ]

    if not default:
        lines += [
            "# ⚠️ 这台机器上暂时没有可用的模型来源。最快的两条路：",
            "#   1. 本地（免费、离线可用）：",
            "#        brew install ollama && ollama serve && ollama pull qwen2.5:7b",
            "#   2. 云端：拿一个 key 写进 ~/.aria/.env，然后重跑 /providers init",
            "",
            "llm:",
            "  default: ollama/qwen2.5:7b   # 装好 Ollama 后即可用",
            "",
        ]
    else:
        lines += ["llm:", f"  default: {default}"]
        if fallback:
            lines.append("  fallback:")
            lines += [f"    - {item}" for item in fallback]
        else:
            lines += [
                "  # 只探测到一个可用来源，暂无 fallback。多配一个 key 可以让",
                "  # 默认来源故障时自动切换。",
            ]
        lines.append("")

    ready_local = [f for f in findings if f.kind == "local" and f.available]
    if ready_local:
        lines.append("# ── 本机检测到的本地模型 ─────────────────────────────")
        for f in ready_local:
            preview = ", ".join(f.models[:6]) + (" …" if len(f.models) > 6 else "")
            lines.append(f"#   {f.provider} @ {f.base_url} → {preview}")
        lines.append("")

    missing = [f for f in findings if f.kind == "cloud" and not f.available]
    if missing:
        lines.append("# ── 还没配的云端 provider（想用哪个就取哪个 key）────────")
        lines.append("#   把值写进 ~/.aria/.env，格式 KEY=值（不要加引号）")
        for f in missing:
            lines.append(f"#   {f.env_key:<22} {f.signup_url}")
        lines.append("")

    lines += [
        "# ── 接入未列出的服务（自建 vLLM / 内网网关 / 任意 OpenAI 兼容端点）──",
        "# 取消注释并改成你自己的地址即可；之后 mycorp/<model> 就能用在上面的",
        "# default 和 fallback 里。",
        "#",
        "# model_providers:",
        "#   mycorp:",
        "#     base_url: http://192.168.1.50:8000/v1",
        "#     env_key: MYCORP_LLM_TOKEN        # 只写变量名，不写值",
        "#     env_key_instructions: 找基础架构组申请",
        "#     model: qwen-72b",
        "",
    ]
    return "\n".join(lines)
