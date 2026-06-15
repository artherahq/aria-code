#!/usr/bin/env python3
"""
setup_wizard.py — Aria Code 首次配置向导
==========================================
运行方式:
  python3 setup_wizard.py          # 完整向导
  python3 setup_wizard.py --model  # 仅配置模型
  python3 setup_wizard.py --feishu # 仅配置飞书

向导完成后会生成:
  ~/.aria/.env          环境变量配置
  ~/.aria/config.json   Aria Code 配置
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ── 尝试用 rich 渲染，回落到纯文本 ──────────────────────────────────────────

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
    from rich import box as rbox
    _rich = True
    console = Console()
except ImportError:
    _rich = False
    class _FakeConsole:
        def print(self, *a, **k): print(*a)
        def rule(self, *a, **k): print("─" * 60)
    console = _FakeConsole()  # type: ignore

_ARIA_DIR = Path.home() / ".aria"
_ENV_FILE = _ARIA_DIR / ".env"
_CFG_FILE = _ARIA_DIR / "config.json"

# ── System language detection ─────────────────────────────────────────────────

try:
    from apps.cli.i18n import detect_system_lang as _detect_lang
    _SYS_LANG = _detect_lang()
except Exception:
    _SYS_LANG = "en"

# Bilingual wizard strings — keyed by (_SYS_LANG, key)
_WZ: dict[str, dict[str, str]] = {
    "setup_model_title":     {"zh": "本地模型配置", "en": "Local Model Setup"},
    "ollama_not_found":      {"zh": "未检测到 Ollama", "en": "Ollama not found"},
    "install_ollama_q":      {"zh": "是否自动安装 Ollama？", "en": "Auto-install Ollama?"},
    "install_ollama_ing":    {"zh": "正在安装 Ollama...", "en": "Installing Ollama..."},
    "install_ollama_fail":   {"zh": "Ollama 安装失败，请手动安装: https://ollama.com",
                              "en": "Ollama install failed. Visit https://ollama.com"},
    "install_ollama_ok":     {"zh": "Ollama 安装完成", "en": "Ollama installed"},
    "skip_ollama":           {"zh": "跳过 Ollama 安装。你也可以配置 API 模型（下方步骤）。",
                              "en": "Skipping Ollama. You can configure a cloud API model below."},
    "win_ollama_hint":       {"zh": "请访问 https://ollama.com/download 下载 Windows 版本后重新运行向导。",
                              "en": "Download the Windows installer from https://ollama.com/download and re-run."},
    "download_model":        {"zh": "正在下载模型 {name}，可能需要几分钟...",
                              "en": "Downloading {name}, this may take a few minutes..."},
    "select_model_prompt":   {"zh": "选择模型（输入序号/名称，直接回车使用 {default}）",
                              "en": "Select model (number/name, Enter to use {default})"},
    "select_model_no_local": {"zh": "选择模型（输入序号 1-6 或完整名称）",
                              "en": "Select model (enter number 1-6 or full name)"},
    "model_set":             {"zh": "已选择模型: {name}", "en": "Model set: {name}"},
    "pull_model_q":          {"zh": "是否立即下载？", "en": "Download now?"},
    "pull_skip":             {"zh": "跳过下载，请稍后运行: ollama pull {name}",
                              "en": "Skipping download. Run later: ollama pull {name}"},
    "lang_detected":         {"zh": "检测到系统语言：中文（可用 /config set ui_lang=en 切换）",
                              "en": "Detected system language: English (use /config set ui_lang=zh to switch)"},
    "api_keys_section":      {"zh": "API 密钥配置（可选）", "en": "API Keys (Optional)"},
    "api_keys_info":         {"zh": "配置后可使用云端模型（Claude / GPT-4 / DeepSeek 等）",
                              "en": "Configure to use cloud models (Claude / GPT-4 / DeepSeek etc.)"},
    "api_key_prompt":        {"zh": "配置 {name} API Key{suffix}？",
                              "en": "Configure {name} API Key{suffix}?"},
    "api_key_saved":         {"zh": "{name} API Key 已保存", "en": "{name} API Key saved"},
    "api_keys_done":         {"zh": "共配置 {count} 个 API Key",
                              "en": "{count} API key(s) configured"},
}


def _wz(key: str, **kwargs) -> str:
    """Look up wizard string in detected system language."""
    entry = _WZ.get(key, {})
    tmpl = entry.get(_SYS_LANG) or entry.get("en") or key
    return tmpl.format(**kwargs) if kwargs else tmpl


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ask(prompt: str, default: str = "", password: bool = False) -> str:
    if _rich:
        return Prompt.ask(prompt, default=default, password=password)
    d = f" [{default}]" if default else ""
    val = input(f"{prompt}{d}: ").strip()
    return val or default


def _confirm(prompt: str, default: bool = True) -> bool:
    if _rich:
        return Confirm.ask(prompt, default=default)
    ans = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return (ans != "n") if default else (ans == "y")


def _section(title: str) -> None:
    if _rich:
        console.rule(f"[bold cyan]{title}[/bold cyan]")
    else:
        print(f"\n{'─'*20} {title} {'─'*20}")


def _ok(msg: str)   -> None: console.print(f"[green]✓[/green] {msg}" if _rich else f"✓ {msg}")
def _warn(msg: str) -> None: console.print(f"[yellow]⚠[/yellow] {msg}" if _rich else f"⚠ {msg}")
def _err(msg: str)  -> None: console.print(f"[red]✗[/red] {msg}" if _rich else f"✗ {msg}")
def _info(msg: str) -> None: console.print(f"[dim]{msg}[/dim]" if _rich else msg)


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _save_env(env: dict[str, str]) -> None:
    _ARIA_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Aria Code 配置 — 由 setup_wizard.py 生成\n"]
    for k, v in sorted(env.items()):
        lines.append(f"{k}={v}\n")
    _ENV_FILE.write_text("".join(lines))
    _ENV_FILE.chmod(0o600)


# ── Step 1: Ollama check + model selection ───────────────────────────────────

def _ollama_installed() -> bool:
    return subprocess.run(["which", "ollama"], capture_output=True).returncode == 0


def _ollama_models() -> list[str]:
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().splitlines()[1:]  # skip header
        return [l.split()[0] for l in lines if l.strip()]
    except Exception:
        return []


def _install_ollama() -> bool:
    _info(_wz("install_ollama_ing"))
    system = platform.system()
    if system == "Darwin":
        if subprocess.run(["which", "brew"], capture_output=True).returncode == 0:
            ret = subprocess.run(["brew", "install", "ollama"]).returncode
            return ret == 0
        cmd = 'curl -fsSL https://ollama.com/install.sh | sh'
    elif system == "Linux":
        cmd = 'curl -fsSL https://ollama.com/install.sh | sh'
    elif system == "Windows":
        _warn(_wz("win_ollama_hint"))
        return False
    else:
        _warn(f"Unknown system {system}. Install manually: https://ollama.com")
        return False
    return subprocess.run(cmd, shell=True).returncode == 0


def _pull_model(name: str) -> bool:
    _info(_wz("download_model", name=name))
    return subprocess.run(["ollama", "pull", name]).returncode == 0


_RECOMMENDED_MODELS_ZH = [
    ("qwen2.5:7b",           "阿里通义千问 7B  ·  中文最强 · ~4GB  · 推荐"),
    ("deepseek-r1:7b",       "DeepSeek R1 7B   ·  推理强   · ~4GB"),
    ("llama3.2:3b",          "Meta Llama 3.2 3B ·  速度快   · ~2GB"),
    ("mistral:7b",           "Mistral 7B        ·  均衡     · ~4GB"),
    ("qwen2.5:14b",          "通义千问 14B      ·  质量高   · ~8GB  · 需 16GB RAM"),
    ("deepseek-r1:14b",      "DeepSeek R1 14B   ·  推理最强 · ~8GB  · 需 16GB RAM"),
]
_RECOMMENDED_MODELS_EN = [
    ("qwen2.5:7b",           "Qwen2.5 7B  ·  Best Chinese+English · ~4GB  · Recommended"),
    ("deepseek-r1:7b",       "DeepSeek R1 7B   ·  Strong reasoning · ~4GB"),
    ("llama3.2:3b",          "Meta Llama 3.2 3B ·  Fast, lightweight · ~2GB"),
    ("mistral:7b",           "Mistral 7B        ·  Balanced · ~4GB"),
    ("qwen2.5:14b",          "Qwen2.5 14B       ·  High quality · ~8GB  · Needs 16GB RAM"),
    ("deepseek-r1:14b",      "DeepSeek R1 14B   ·  Best reasoning · ~8GB · Needs 16GB RAM"),
]
_RECOMMENDED_MODELS = _RECOMMENDED_MODELS_ZH if _SYS_LANG == "zh" else _RECOMMENDED_MODELS_EN


def setup_model(env: dict[str, str]) -> None:
    _section(_wz("setup_model_title"))

    if not _ollama_installed():
        _warn(_wz("ollama_not_found"))
        if _confirm(_wz("install_ollama_q")):
            if not _install_ollama():
                _err(_wz("install_ollama_fail"))
                return
            _ok(_wz("install_ollama_ok"))
        else:
            _info(_wz("skip_ollama"))
            return

    existing = _ollama_models()
    _installed_label = "✓ installed" if _SYS_LANG != "zh" else "✓ 已安装"
    _other_label = "Other installed model" if _SYS_LANG != "zh" else "已安装的其他模型"

    if _rich:
        _col_model  = "Model" if _SYS_LANG != "zh" else "模型"
        _col_desc   = "Description" if _SYS_LANG != "zh" else "说明"
        t = Table(box=rbox.SIMPLE, show_header=True)
        t.add_column("#", style="dim", width=3)
        t.add_column(_col_model, style="cyan")
        t.add_column(_col_desc, style="dim")
        for i, (name, desc) in enumerate(_RECOMMENDED_MODELS, 1):
            marker = f" [green]{_installed_label}[/green]" if name in existing else ""
            t.add_row(str(i), name + marker, desc)
        if existing:
            for m in existing:
                if not any(m == n for n, _ in _RECOMMENDED_MODELS):
                    t.add_row("*", m, _other_label)
        console.print(t)
    else:
        for i, (name, desc) in enumerate(_RECOMMENDED_MODELS, 1):
            installed = f" [{_installed_label}]" if name in existing else ""
            print(f"  {i}. {name}{installed} — {desc}")

    if existing:
        default_model = existing[0]
        choice = _ask(_wz("select_model_prompt", default=default_model), default=default_model)
    else:
        choice = _ask(_wz("select_model_no_local"), default="1")

    # Resolve number → model name
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(_RECOMMENDED_MODELS):
            model_name = _RECOMMENDED_MODELS[idx][0]
        else:
            model_name = choice
    except ValueError:
        model_name = choice

    if model_name not in existing:
        _pull_q = (f"模型 {model_name} 未安装，是否立即下载？" if _SYS_LANG == "zh"
                   else f"Model {model_name} not installed. Download now?")
        if _confirm(_pull_q):
            if _pull_model(model_name):
                _ok(f"模型 {model_name} 下载完成" if _SYS_LANG == "zh"
                    else f"Model {model_name} downloaded")
            else:
                _err(_wz("pull_skip", name=model_name))
        else:
            _warn(_wz("pull_skip", name=model_name))

    env["ARIA_DEFAULT_MODEL"] = model_name
    env["OLLAMA_BASE_URL"] = "http://localhost:11434"
    _ok(_wz("model_set", name=model_name))


# ── Step 2: API keys (optional) ──────────────────────────────────────────────

def setup_api_keys(env: dict[str, str]) -> None:
    _section(_wz("api_keys_section"))
    _info(_wz("api_keys_info"))

    # (display_name, env_var, hint)
    _LLM_PROVIDERS = [
        # ── 国际
        ("Anthropic (Claude)",         "ANTHROPIC_API_KEY",     "claude.ai/settings/keys"),
        ("OpenAI (GPT-4o / Whisper)",  "OPENAI_API_KEY",        "platform.openai.com/api-keys"),
        ("DeepSeek",                   "DEEPSEEK_API_KEY",       "platform.deepseek.com"),
        ("Google Gemini",              "GOOGLE_API_KEY",         "aistudio.google.com/apikey"),
        ("xAI Grok",                   "XAI_API_KEY",            "console.x.ai"),
        ("Groq (fast inference)",      "GROQ_API_KEY",           "console.groq.com/keys"),
        ("Mistral AI",                 "MISTRAL_API_KEY",        "console.mistral.ai/api-keys"),
        ("Cohere",                     "COHERE_API_KEY",         "dashboard.cohere.com/api-keys"),
        ("Perplexity",                 "PERPLEXITY_API_KEY",     "perplexity.ai/settings/api"),
        ("Together AI",                "TOGETHER_API_KEY",       "api.together.xyz/settings/api-keys"),
        # ── 国内
        ("SiliconFlow 硅基流动",        "SILICONFLOW_API_KEY",   "cloud.siliconflow.cn"),
        ("DashScope 阿里云百炼",         "DASHSCOPE_API_KEY",     "dashscope.aliyuncs.com"),
        ("Moonshot Kimi 月之暗面",       "MOONSHOT_API_KEY",      "platform.moonshot.cn/api-keys"),
        ("Zhipu GLM 智谱",              "ZHIPU_API_KEY",          "open.bigmodel.cn"),
        ("Baidu ERNIE 百度千帆",         "QIANFAN_ACCESS_KEY",    "console.bce.baidu.com/iam/#/iam/accesslist"),
        ("ByteDance Doubao 字节豆包",    "ARK_API_KEY",            "console.volcengine.com/ark"),
        ("MiniMax",                    "MINIMAX_API_KEY",        "platform.minimaxi.com"),
        ("StepFun 阶跃星辰",            "STEPFUN_API_KEY",        "platform.stepfun.com"),
        ("01.AI Yi 零一万物",            "ONEAI_API_KEY",          "platform.lingyiwanwu.com"),
    ]

    _configured = []
    for name, env_var, hint in _LLM_PROVIDERS:
        already = bool(env.get(env_var) or __import__("os").getenv(env_var))
        suffix = f" [已配置]" if already else ""
        prompt = _wz("api_key_prompt", name=name, suffix=suffix)
        if _confirm(prompt, default=False):
            _info(f"  → {hint}")
            key = _ask(env_var, password=True)
            if key:
                env[env_var] = key
                _configured.append(name)
                _ok(_wz("api_key_saved", name=name))

    if _configured:
        _ok(_wz("api_keys_done", count=len(_configured)))


# ── Step 3: Feishu connection ─────────────────────────────────────────────────

def setup_feishu(env: dict[str, str]) -> None:
    _section("飞书连接配置")

    if _rich:
        console.print(Panel(
            "[bold]两种连接方式：[/bold]\n\n"
            "[cyan]1. Aria 中继服务[/cyan]  [dim]（推荐）[/dim]\n"
            "   • 无需创建飞书应用\n"
            "   • 在飞书向 Aria 官方 Bot 发送绑定码即可\n"
            "   • 消息经中继服务器转发到你的本机\n\n"
            "[cyan]2. 自建飞书应用[/cyan]  [dim]（完全自主）[/dim]\n"
            "   • 需要飞书开发者账号\n"
            "   • 消息直接推送到你的服务器\n"
            "   • 适合有公网 IP / 服务器的用户",
            title="飞书连接方式",
            border_style="cyan",
        ))
    else:
        print("  1. Aria 中继服务（推荐，无需创建飞书应用）")
        print("  2. 自建飞书应用（需要飞书开发者账号）")

    choice = _ask("选择方式", default="1")

    if choice == "1":
        _setup_feishu_relay(env)
    else:
        _setup_feishu_own_app(env)


def _setup_feishu_relay(env: dict[str, str]) -> None:
    """Connect via Aria Relay Server — no Feishu developer account needed."""
    relay_url = env.get("ARIA_RELAY_URL", "wss://relay.aria.ai")
    relay_url = _ask("中继服务器地址", default=relay_url)
    env["ARIA_RELAY_URL"] = relay_url
    env["ARIA_RELAY_MODE"] = "relay"

    # Generate or reuse client_id
    client_id = env.get("ARIA_RELAY_CLIENT_ID") or f"aria-{uuid.uuid4().hex[:12]}"
    env["ARIA_RELAY_CLIENT_ID"] = client_id

    if _rich:
        console.print(Panel(
            f"[bold]你的绑定码：[/bold]\n\n"
            f"[bold yellow on black]  ARIA-BIND-{client_id.upper()}  [/bold yellow on black]\n\n"
            f"[dim]操作步骤：[/dim]\n"
            f"1. 在飞书搜索 [cyan]Aria Bot[/cyan] 并添加好友\n"
            f"2. 发送：[cyan]/bind ARIA-BIND-{client_id.upper()}[/cyan]\n"
            f"3. 收到 [green]\"绑定成功\"[/green] 后，飞书消息将转发到你的 Aria\n\n"
            f"[dim]（中继客户端会在 daemon 启动时自动连接）[/dim]",
            title="📱 飞书绑定步骤",
            border_style="yellow",
        ))
    else:
        print(f"\n你的绑定码: ARIA-BIND-{client_id.upper()}")
        print("1. 在飞书搜索 Aria Bot 并添加好友")
        print(f"2. 发送: /bind ARIA-BIND-{client_id.upper()}")
        print("3. 收到绑定成功后继续\n")

    if _confirm("是否已完成绑定？（可以稍后运行向导再完成）", default=False):
        _ok("飞书中继绑定完成")
    else:
        _warn("请完成绑定后再启动 daemon，否则飞书消息无法到达本机")


def _setup_feishu_own_app(env: dict[str, str]) -> None:
    """Self-hosted Feishu app configuration with step-by-step guidance."""
    if _rich:
        console.print(
            "\n[dim]飞书开发者后台：https://open.feishu.cn/app[/dim]\n"
            "步骤：创建自建应用 → 凭证与基本信息 → 复制 App ID 和 App Secret\n"
        )
    else:
        print("飞书开发者后台：https://open.feishu.cn/app")

    app_id = _ask("FEISHU_APP_ID (如: cli_xxxxxxxxxxxx)", default=env.get("FEISHU_APP_ID", ""))
    secret = _ask("FEISHU_APP_SECRET", default=env.get("FEISHU_APP_SECRET", ""), password=True)

    if app_id:
        env["FEISHU_APP_ID"] = app_id
    if secret:
        env["FEISHU_APP_SECRET"] = secret
    env["ARIA_RELAY_MODE"] = "own_app"

    # Webhook URL guidance
    _info("\n事件订阅 Request URL: http://<你的IP或域名>/api/v1/feishu/event")
    _info("如果没有公网 IP，可以用 cloudflared 建立隧道：")
    _info("  brew install cloudflared && cloudflared tunnel --url http://localhost:8000")

    if _confirm("是否配置 FEISHU_WEBHOOK_URL（群机器人单向推送）？", default=False):
        wh = _ask("FEISHU_WEBHOOK_URL")
        if wh:
            env["FEISHU_WEBHOOK_URL"] = wh
            _ok("飞书 Webhook 已保存")

    _ok("飞书自建应用配置完成")


# ── Step 4: Write config & finalize ──────────────────────────────────────────

def _write_aria_config(env: dict[str, str]) -> None:
    """Write ~/.aria/config.json for aria_cli.py to read."""
    cfg: dict = {}
    if _CFG_FILE.exists():
        try:
            cfg = json.loads(_CFG_FILE.read_text())
        except Exception:
            cfg = {}

    model = env.get("ARIA_DEFAULT_MODEL", "")
    if model:
        cfg["default_model"] = model
        cfg["model"] = model  # also write as "model" for aria_cli.py

    # Persist detected system language so UI renders correctly from first launch
    cfg["ui_lang"] = _SYS_LANG

    _ARIA_DIR.mkdir(parents=True, exist_ok=True)
    _CFG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def _print_summary(env: dict[str, str]) -> None:
    _section("配置完成")
    if _rich:
        t = Table(box=rbox.SIMPLE, show_header=False)
        t.add_column("项目", style="dim", width=22)
        t.add_column("值",   style="cyan")

        def _mask(v: str) -> str:
            return v[:6] + "…" + v[-4:] if len(v) > 12 else "***"

        t.add_row("默认模型",   env.get("ARIA_DEFAULT_MODEL", "—"))
        t.add_row("飞书模式",   env.get("ARIA_RELAY_MODE", "—"))
        if env.get("ARIA_RELAY_CLIENT_ID"):
            t.add_row("中继 Client ID", env["ARIA_RELAY_CLIENT_ID"])
        if env.get("FEISHU_APP_ID"):
            t.add_row("飞书 App ID",   env["FEISHU_APP_ID"])
        if env.get("ANTHROPIC_API_KEY"):
            t.add_row("Anthropic Key", _mask(env["ANTHROPIC_API_KEY"]))
        if env.get("OPENAI_API_KEY"):
            t.add_row("OpenAI Key",    _mask(env["OPENAI_API_KEY"]))
        console.print(t)

        console.print(Panel(
            "[bold green]Aria Code 配置成功！[/bold green]\n\n"
            "启动方式：\n"
            "  [cyan]python3 aria_cli.py[/cyan]              # 交互终端\n"
            "  [cyan]python3 aria_daemon.py --install[/cyan] # 安装为系统服务（开机自启）\n"
            "  [cyan]python3 aria_daemon.py[/cyan]           # 前台运行 daemon\n\n"
            "[dim]配置文件: ~/.aria/.env[/dim]",
            border_style="green",
        ))
    else:
        print("\n配置完成！")
        print(f"默认模型: {env.get('ARIA_DEFAULT_MODEL', '—')}")
        print(f"飞书模式: {env.get('ARIA_RELAY_MODE', '—')}")
        print("\n启动: python3 aria_daemon.py")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Aria Code Setup Wizard / 配置向导")
    parser.add_argument("--model",  action="store_true", help="Model setup only / 仅配置模型")
    parser.add_argument("--feishu", action="store_true", help="Feishu only / 仅配置飞书")
    parser.add_argument("--keys",   action="store_true", help="API keys only / 仅配置 API Key")
    args = parser.parse_args()

    # Show detected language
    _info(_wz("lang_detected"))

    if _SYS_LANG == "zh":
        _intro = (
            "[bold cyan]Aria Code[/bold cyan] [dim]— AI 金融终端[/dim]\n\n"
            "本向导将帮助你完成首次配置：\n"
            "  • 本地 AI 模型（Ollama）\n"
            "  • API 密钥（Claude / GPT-4，可选）\n"
            "  • 飞书机器人连接"
        )
        _title = "🔧 首次配置向导"
        _plain_intro = "\n=== Aria Code 配置向导 ===\n"
    else:
        _intro = (
            "[bold cyan]Aria Code[/bold cyan] [dim]— AI Finance Terminal[/dim]\n\n"
            "This wizard will help you set up:\n"
            "  • Local AI model (Ollama)\n"
            "  • API keys (Claude / GPT-4 — optional)\n"
            "  • Feishu / Lark bot connection"
        )
        _title = "🔧 First-Run Setup"
        _plain_intro = "\n=== Aria Code Setup Wizard ===\n"

    if _rich:
        console.print(Panel(_intro, title=_title, border_style="cyan"))
    else:
        print(_plain_intro)

    env = _load_env()
    run_all = not (args.model or args.feishu or args.keys)

    if run_all or args.model:
        setup_model(env)

    if run_all or args.keys:
        setup_api_keys(env)

    if run_all or args.feishu:
        setup_feishu(env)

    _save_env(env)
    _write_aria_config(env)
    _ok(f"配置已保存到 {_ENV_FILE}")

    if run_all:
        _print_summary(env)

    if run_all and _confirm("\n是否立即启动 Aria Daemon？", default=True):
        os.execv(sys.executable, [sys.executable, str(Path(__file__).parent / "aria_daemon.py")])


if __name__ == "__main__":
    main()
