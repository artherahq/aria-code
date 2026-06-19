"""Intent-aware dependency preflight for Aria CLI.

This module is intentionally pure and side-effect free: it detects likely user
intent, checks local availability, and returns install guidance. It never
installs packages or mutates config.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
import shlex
import shutil
from typing import Callable, Iterable, Mapping

from apps.cli.intent_router import build_intent_route, detect_intents


ModuleChecker = Callable[[str], bool]
CommandChecker = Callable[[str], bool]
EnvGetter = Callable[[str], str | None]


@dataclass(frozen=True)
class PythonRequirement:
    module: str
    package: str
    purpose: str
    required: bool = True


@dataclass(frozen=True)
class CommandRequirement:
    command: str
    install_hint: str
    purpose: str
    required: bool = True


@dataclass(frozen=True)
class EnvRequirement:
    name: str
    purpose: str
    required: bool = False


@dataclass(frozen=True)
class IntentPreflight:
    intents: tuple[str, ...]
    services: tuple[str, ...]
    python: tuple[PythonRequirement, ...]
    commands: tuple[CommandRequirement, ...]
    env: tuple[EnvRequirement, ...]
    missing_python: tuple[PythonRequirement, ...]
    missing_commands: tuple[CommandRequirement, ...]
    missing_env: tuple[EnvRequirement, ...]

    @property
    def has_findings(self) -> bool:
        return bool(self.missing_python or self.missing_commands or self.missing_env)

    @property
    def has_required_findings(self) -> bool:
        return any(req.required for req in self.missing_python + self.missing_commands + self.missing_env)

    def pip_install_command(self) -> str:
        packages: list[str] = []
        seen: set[str] = set()
        for req in self.missing_python:
            if req.package not in seen:
                packages.append(req.package)
                seen.add(req.package)
        if not packages:
            return ""
        return "python3 -m pip install " + " ".join(shlex.quote(pkg) for pkg in packages)


@dataclass(frozen=True)
class InstallPlan:
    """User-confirmed install guidance derived from an intent preflight."""

    services: tuple[str, ...]
    pip_packages: tuple[str, ...]
    pip_command: str
    command_hints: tuple[str, ...]
    env_hints: tuple[str, ...]
    has_required_items: bool

    @property
    def has_actions(self) -> bool:
        return bool(self.pip_packages or self.command_hints or self.env_hints)


_PY_REQS: Mapping[str, PythonRequirement] = {
    "aiohttp": PythonRequirement("aiohttp", "aiohttp", "异步 HTTP 请求"),
    "requests": PythonRequirement("requests", "requests", "HTTP 请求"),
    "pandas": PythonRequirement("pandas", "pandas", "表格与行情数据处理"),
    "numpy": PythonRequirement("numpy", "numpy", "数值计算"),
    "yfinance": PythonRequirement("yfinance", "yfinance", "美股/港股/ETF/加密行情"),
    "akshare": PythonRequirement("akshare", "akshare", "A 股与中文市场数据"),
    "matplotlib": PythonRequirement("matplotlib", "matplotlib", "PNG 静态图表渲染", required=False),
    "mplfinance": PythonRequirement("mplfinance", "mplfinance", "K 线 PNG 渲染", required=False),
    "PIL": PythonRequirement("PIL", "Pillow", "图片读取与剪贴板图片"),
    "mss": PythonRequirement("mss", "mss", "屏幕截图"),
    "pyautogui": PythonRequirement("pyautogui", "pyautogui", "桌面自动化"),
    "playwright": PythonRequirement("playwright", "playwright", "浏览器自动化"),
    "openpyxl": PythonRequirement("openpyxl", "openpyxl", "Excel 文件解析"),
    "pdfplumber": PythonRequirement("pdfplumber", "pdfplumber", "PDF 文本与表格解析", required=False),
    "pypdf": PythonRequirement("pypdf", "pypdf", "PDF 解析备用引擎", required=False),
    "docx": PythonRequirement("docx", "python-docx", "Word/DOCX 文件解析"),
    "bs4": PythonRequirement("bs4", "beautifulsoup4", "HTML 文件解析"),
    "duckdb": PythonRequirement("duckdb", "duckdb", "本地分析型 SQL"),
    "vectorbt": PythonRequirement("vectorbt", "vectorbt", "增强向量化回测", required=False),
    "alpaca": PythonRequirement("alpaca", "alpaca-py", "Alpaca 券商连接"),
    "tigeropen": PythonRequirement("tigeropen", "tigeropen", "Tiger 券商连接"),
    "longbridge": PythonRequirement("longbridge", "longbridge", "Longbridge 券商连接"),
    "ib_insync": PythonRequirement("ib_insync", "ib_insync", "IBKR 券商连接"),
    "futu": PythonRequirement("futu", "futu-api", "富途券商连接"),
    "webull": PythonRequirement("webull", "webull", "Webull 券商连接"),
    "easytrader": PythonRequirement("easytrader", "easytrader", "A 股交易客户端连接"),
}

_CMD_REQS: Mapping[str, CommandRequirement] = {
    "ollama": CommandRequirement("ollama", "https://ollama.com/download", "本地模型运行"),
    "node": CommandRequirement("node", "brew install node", "部分 MCP server 运行时", required=False),
    "gh": CommandRequirement("gh", "brew install gh && gh auth login", "GitHub PR/Issue/CI 操作"),
    "playwright": CommandRequirement("playwright", "python3 -m playwright install chromium", "安装 Chromium 浏览器内核", required=False),
}

_BROKER_MODULES = {
    "alpaca": ("alpaca", ("alpaca", "alpaca-py")),
    "tiger": ("tigeropen", ("tiger", "老虎", "tigeropen")),
    "longbridge": ("longbridge", ("longbridge", "长桥")),
    "ibkr": ("ib_insync", ("ibkr", "interactive brokers", "盈透")),
    "futu": ("futu", ("futu", "富途")),
    "webull": ("webull", ("webull",)),
    "easytrader": ("easytrader", ("easytrader", "同花顺", "雪球")),
}


def _default_module_available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _default_command_available(command: str) -> bool:
    return shutil.which(command) is not None


def _add_req(target: list[PythonRequirement], key: str) -> None:
    req = _PY_REQS[key]
    if not any(item.module == req.module for item in target):
        target.append(req)


def _add_cmd(target: list[CommandRequirement], key: str) -> None:
    req = _CMD_REQS[key]
    if not any(item.command == req.command for item in target):
        target.append(req)


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _detect_intents(message: str) -> tuple[str, ...]:
    return detect_intents(message)


def build_intent_preflight(
    message: str,
    *,
    module_available: ModuleChecker | None = None,
    command_available: CommandChecker | None = None,
    env_get: EnvGetter | None = None,
) -> IntentPreflight:
    """Build an intent-aware dependency report for a user request."""
    module_available = module_available or _default_module_available
    command_available = command_available or _default_command_available
    env_get = env_get or os.environ.get

    low = message.lower().strip()
    route = build_intent_route(message)
    intents = route.intents
    python_reqs: list[PythonRequirement] = []
    command_reqs: list[CommandRequirement] = []
    env_reqs: list[EnvRequirement] = []
    services: list[str] = list(route.services)

    def service(name: str) -> None:
        if name not in services:
            services.append(name)

    if any(i in intents for i in ("market_snapshot", "market_analysis", "chart", "dashboard", "report", "backtest", "strategy", "market_research")):
        service("market_data")
        _add_req(python_reqs, "pandas")
        _add_req(python_reqs, "numpy")
        _add_req(python_reqs, "yfinance")
        if any(ch in low for ch in ("a股", "沪", "深", ".sz", ".ss", "港股", "宁德时代", "贵州茅台")):
            _add_req(python_reqs, "akshare")

    if "chart" in intents:
        service("chart_renderer")
        if _contains_any(low, ("png", "图片", "静态", "k线", "candlestick")):
            _add_req(python_reqs, "matplotlib")
            _add_req(python_reqs, "mplfinance")

    if "dashboard" in intents:
        service("dashboard_generator")
        _add_req(python_reqs, "akshare")

    if "report" in intents:
        service("report_generator")
        _add_req(python_reqs, "matplotlib")

    if "backtest" in intents or "strategy" in intents:
        service("backtest_engine")
        _add_req(python_reqs, "vectorbt")

    if "vision" in intents:
        service("vision_input")
        _add_req(python_reqs, "PIL")

    if "screenshot" in intents:
        service("screenshot")
        _add_req(python_reqs, "PIL")
        _add_req(python_reqs, "mss")

    if "browser" in intents:
        service("browser")
        _add_req(python_reqs, "playwright")
        _add_req(python_reqs, "PIL")
        _add_cmd(command_reqs, "playwright")

    if "file_analysis" in intents:
        service("file_parser")
        _add_req(python_reqs, "pandas")
        _add_req(python_reqs, "openpyxl")
        _add_req(python_reqs, "pdfplumber")
        _add_req(python_reqs, "pypdf")
        _add_req(python_reqs, "docx")
        _add_req(python_reqs, "bs4")
        _add_req(python_reqs, "PIL")

    for _, (module, aliases) in _BROKER_MODULES.items():
        if _contains_any(low, aliases):
            service("broker_connector")
            _add_req(python_reqs, module)

    if "github" in intents:
        service("github_cli")
        _add_cmd(command_reqs, "gh")

    if "mcp" in intents:
        service("mcp")
        _add_cmd(command_reqs, "node")

    if "local_model" in intents:
        service("local_llm")
        _add_cmd(command_reqs, "ollama")

    if "cloud" in intents:
        service("cloud_runtime")
        env_reqs.append(EnvRequirement("ALIYUN_ACCESS_KEY_ID", "阿里云访问密钥", required=False))

    missing_python = tuple(req for req in python_reqs if not module_available(req.module))
    missing_commands = tuple(req for req in command_reqs if not command_available(req.command))
    missing_env = tuple(req for req in env_reqs if not env_get(req.name))

    return IntentPreflight(
        intents=intents,
        services=tuple(services),
        python=tuple(python_reqs),
        commands=tuple(command_reqs),
        env=tuple(env_reqs),
        missing_python=missing_python,
        missing_commands=missing_commands,
        missing_env=missing_env,
    )


def build_install_plan(report: IntentPreflight) -> InstallPlan:
    """Convert preflight findings into explicit user-approved install steps."""
    packages: list[str] = []
    seen: set[str] = set()
    for req in report.missing_python:
        if req.package not in seen:
            packages.append(req.package)
            seen.add(req.package)

    command_hints = tuple(
        f"{req.command}: {req.install_hint}"
        for req in report.missing_commands
    )
    env_hints = tuple(
        f"{req.name}: {req.purpose}"
        for req in report.missing_env
    )
    pip_command = ""
    if packages:
        pip_command = "python3 -m pip install " + " ".join(shlex.quote(pkg) for pkg in packages)

    return InstallPlan(
        services=report.services,
        pip_packages=tuple(packages),
        pip_command=pip_command,
        command_hints=command_hints,
        env_hints=env_hints,
        has_required_items=report.has_required_findings,
    )


def format_preflight_plain(report: IntentPreflight) -> str:
    """Return a concise plain-text preflight message."""
    if not report.has_findings:
        return ""
    plan = build_install_plan(report)
    lines = ["依赖预检：当前请求可能需要补充本机能力"]
    if report.intents:
        lines.append("意图: " + ", ".join(report.intents))
    if report.services:
        lines.append("服务: " + ", ".join(report.services))
    if report.missing_python:
        reqs = ", ".join(f"{r.package}({r.purpose})" for r in report.missing_python)
        lines.append("缺少 Python 包: " + reqs)
        if plan.pip_command:
            lines.append("安装命令: " + plan.pip_command)
    if report.missing_commands:
        for hint in plan.command_hints:
            lines.append("缺少工具: " + hint)
    if report.missing_env:
        envs = ", ".join(plan.env_hints)
        lines.append("可选环境变量未配置: " + envs)
    if plan.has_actions:
        lines.append("Aria 不会自动安装；确认需要后再运行安装命令，或用 /setup 查看配置向导。")
    return "\n".join(lines)
