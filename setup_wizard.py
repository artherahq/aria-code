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
    _info("正在安装 Ollama...")
    system = platform.system()
    if system == "Darwin":
        if subprocess.run(["which", "brew"], capture_output=True).returncode == 0:
            ret = subprocess.run(["brew", "install", "ollama"]).returncode
            return ret == 0
        # Direct download
        cmd = 'curl -fsSL https://ollama.com/install.sh | sh'
    elif system == "Linux":
        cmd = 'curl -fsSL https://ollama.com/install.sh | sh'
    elif system == "Windows":
        _warn("请访问 https://ollama.com/download 下载 Windows 版本后重新运行向导。")
        return False
    else:
        _warn(f"未知系统 {system}，请手动安装 Ollama: https://ollama.com")
        return False
    return subprocess.run(cmd, shell=True).returncode == 0


def _pull_model(name: str) -> bool:
    _info(f"正在下载模型 {name}，可能需要几分钟...")
    return subprocess.run(["ollama", "pull", name]).returncode == 0


_RECOMMENDED_MODELS = [
    ("qwen2.5:7b",           "阿里通义千问 7B  ·  中文最强 · ~4GB  · 推荐"),
    ("deepseek-r1:7b",       "DeepSeek R1 7B   ·  推理强   · ~4GB"),
    ("llama3.2:3b",          "Meta Llama 3.2 3B ·  速度快   · ~2GB"),
    ("mistral:7b",           "Mistral 7B        ·  均衡     · ~4GB"),
    ("qwen2.5:14b",          "通义千问 14B      ·  质量高   · ~8GB  · 需 16GB RAM"),
    ("deepseek-r1:14b",      "DeepSeek R1 14B   ·  推理最强 · ~8GB  · 需 16GB RAM"),
]


def setup_model(env: dict[str, str]) -> None:
    _section("本地模型配置")

    if not _ollama_installed():
        _warn("未检测到 Ollama")
        if _confirm("是否自动安装 Ollama？"):
            if not _install_ollama():
                _err("Ollama 安装失败，请手动安装: https://ollama.com")
                return
            _ok("Ollama 安装完成")
        else:
            _info("跳过 Ollama 安装。你也可以配置 API 模型（下方步骤）。")
            return

    existing = _ollama_models()

    if _rich:
        t = Table(box=rbox.SIMPLE, show_header=True)
        t.add_column("#", style="dim", width=3)
        t.add_column("模型", style="cyan")
        t.add_column("说明", style="dim")
        for i, (name, desc) in enumerate(_RECOMMENDED_MODELS, 1):
            marker = " [green]✓ 已安装[/green]" if name in existing else ""
            t.add_row(str(i), name + marker, desc)
        if existing:
            for m in existing:
                if not any(m == n for n, _ in _RECOMMENDED_MODELS):
                    t.add_row("*", m, "已安装的其他模型")
        console.print(t)
    else:
        for i, (name, desc) in enumerate(_RECOMMENDED_MODELS, 1):
            installed = " [已安装]" if name in existing else ""
            print(f"  {i}. {name}{installed} — {desc}")

    if existing:
        default_model = existing[0]
        choice = _ask(
            f"选择模型（输入序号/名称，直接回车使用 {default_model}）",
            default=default_model,
        )
    else:
        choice = _ask("选择模型（输入序号 1-6 或完整名称）", default="1")

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
        if _confirm(f"模型 {model_name} 未安装，是否立即下载？"):
            if _pull_model(model_name):
                _ok(f"模型 {model_name} 下载完成")
            else:
                _err(f"模型下载失败，请手动运行: ollama pull {model_name}")
        else:
            _warn(f"跳过下载。记得手动运行: ollama pull {model_name}")

    env["ARIA_DEFAULT_MODEL"] = model_name
    env["OLLAMA_BASE_URL"] = "http://localhost:11434"
    _ok(f"默认模型设置为: {model_name}")


# ── Step 2: API keys (optional) ──────────────────────────────────────────────

def setup_api_keys(env: dict[str, str]) -> None:
    _section("API 密钥（可选）")
    _info("配置后可使用 Claude / GPT-4 等云端模型，以及图片理解功能")

    if _confirm("配置 Anthropic API Key (Claude)？", default=False):
        key = _ask("ANTHROPIC_API_KEY", password=True)
        if key:
            env["ANTHROPIC_API_KEY"] = key
            _ok("Anthropic API Key 已保存")

    if _confirm("配置 OpenAI API Key (GPT-4 / Whisper)？", default=False):
        key = _ask("OPENAI_API_KEY", password=True)
        if key:
            env["OPENAI_API_KEY"] = key
            _ok("OpenAI API Key 已保存")


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
    parser = argparse.ArgumentParser(description="Aria Code 配置向导")
    parser.add_argument("--model",  action="store_true", help="仅配置模型")
    parser.add_argument("--feishu", action="store_true", help="仅配置飞书")
    parser.add_argument("--keys",   action="store_true", help="仅配置 API Key")
    args = parser.parse_args()

    if _rich:
        console.print(Panel(
            "[bold cyan]Aria Code[/bold cyan] [dim]— AI 金融终端[/dim]\n\n"
            "本向导将帮助你完成首次配置：\n"
            "  • 本地 AI 模型（Ollama）\n"
            "  • API 密钥（Claude / GPT-4，可选）\n"
            "  • 飞书机器人连接",
            title="🔧 首次配置向导",
            border_style="cyan",
        ))
    else:
        print("\n=== Aria Code 配置向导 ===\n")

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
