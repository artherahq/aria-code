"""Canonical lightweight intent routing for CLI services.

This module keeps routing side-effect free.  It does not call tools, install
packages, or fetch data; it only classifies a user message into stable service
intents that other layers can reuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _first_token(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped.split(maxsplit=1)[0].lower()


def _add_unique(items: list[str], name: str) -> None:
    if name not in items:
        items.append(name)


@dataclass(frozen=True)
class IntentRoute:
    message: str
    primary: str
    intents: tuple[str, ...]
    services: tuple[str, ...]
    explicit_code: bool = False
    visual_artifact: bool = False
    market_related: bool = False

    @property
    def wants_market_prefetch(self) -> bool:
        return self.market_related and not self.visual_artifact and self.primary != "general"

    @property
    def allows_code_autorun(self) -> bool:
        return self.primary in {"code", "strategy", "backtest"} and self.explicit_code


COMMAND_INTENTS = {
    "/chart": "chart",
    "/dashboard": "dashboard",
    "/report": "report",
    "/team": "market_research",
    "/analyze": "market_analysis",
    "/ta": "market_analysis",
    "/quote": "market_snapshot",
    "/backtest": "backtest",
    "/auto-strategy": "strategy",
    "/strategy": "strategy",
    "/ui": "ui_artifact",
    "/vision": "vision",
    "/upload-image": "vision",
    "/screenshot": "screenshot",
    "/browser": "browser",
    "/file": "file_analysis",
    "/github": "github",
    "/mcp": "mcp",
    "/cloud": "cloud",
}


def detect_intents(message: str) -> tuple[str, ...]:
    low = message.lower().strip()
    cmd = _first_token(low)
    intents: list[str] = []

    mapped = COMMAND_INTENTS.get(cmd)
    if mapped:
        _add_unique(intents, mapped)

    if _contains_any(low, ("k线图", "k线", "k-line", "kline", "candlestick", "走势图", "图表", "chart", "plot")):
        _add_unique(intents, "chart")
    if _contains_any(low, ("看板", "晨报", "日报", "dashboard", "market board", "持仓看板")):
        _add_unique(intents, "dashboard")
    if _contains_any(low, ("研究报告", "财报", "报告", "report", "研报")):
        _add_unique(intents, "report")
    if _contains_any(low, ("回测", "backtest", "收益曲线", "最大回撤")):
        _add_unique(intents, "backtest")
    if _contains_any(low, ("策略代码", "写策略", "量化策略", "交易策略", "strategy code", "trading bot")):
        _add_unique(intents, "strategy")
    if _contains_any(low, ("技术分析", "技术指标", "rsi", "macd", "均线", "支撑", "阻力")):
        _add_unique(intents, "market_analysis")
    if _contains_any(low, ("股票", "行情", "持仓", "portfolio", "quote", "market data")):
        _add_unique(intents, "market_snapshot")
    if _contains_any(low, ("图片", "图像", "截图", "上传图片", "分析图片", "识别图片", "image", "screenshot")):
        _add_unique(intents, "vision")
    if _contains_any(low, ("浏览器", "网页截图", "打开网页", "browser", "playwright")):
        _add_unique(intents, "browser")
    if _contains_any(low, ("pdf", "docx", "word", "excel", "xlsx", "csv", "文件分析", "上传文件")):
        _add_unique(intents, "file_analysis")
    if _contains_any(low, ("github", "pull request", "pr ", "issue", "ci")):
        _add_unique(intents, "github")
    if _contains_any(low, ("mcp", "server", "tools", "skills")):
        _add_unique(intents, "mcp")
    if _contains_any(low, ("ollama", "本地模型", "local model")):
        _add_unique(intents, "local_model")
    if _contains_any(low, ("阿里云", "aliyun", "cloud service", "云端服务")):
        _add_unique(intents, "cloud")
    if _contains_any(low, ("搜索", "联网", "上网查", "web search", "google", "查一下", "搜一下", "最新消息")):
        _add_unique(intents, "web_search")
    if _contains_any(low, ("加密货币", "比特币", "以太坊", "bitcoin", "btc", "eth", "crypto", "币安", "binance", "okx", "资金费率", "funding rate")):
        _add_unique(intents, "crypto")
    if _contains_any(low, ("足球", "球赛", "比分预测", "世界杯", "欧洲杯", "英超", "西甲", "football", "soccer", "world cup", "premier league")):
        _add_unique(intents, "sports")

    return tuple(intents)


def _service_names(intents: tuple[str, ...]) -> tuple[str, ...]:
    services: list[str] = []

    def service(name: str) -> None:
        _add_unique(services, name)

    if any(i in intents for i in ("market_snapshot", "market_analysis", "chart", "dashboard", "report", "backtest", "strategy", "market_research")):
        service("market_data")
    if "chart" in intents:
        service("chart_renderer")
    if "dashboard" in intents:
        service("dashboard_generator")
    if "report" in intents:
        service("report_generator")
    if "backtest" in intents or "strategy" in intents:
        service("backtest_engine")
    if "vision" in intents:
        service("vision_input")
    if "screenshot" in intents:
        service("screenshot")
    if "browser" in intents:
        service("browser")
    if "file_analysis" in intents:
        service("file_parser")
    if "github" in intents:
        service("github_cli")
    if "mcp" in intents:
        service("mcp")
    if "local_model" in intents:
        service("local_llm")
    if "cloud" in intents:
        service("cloud_runtime")
    if "web_search" in intents:
        service("web_search")
    if "crypto" in intents:
        service("crypto_data")
    if "sports" in intents:
        service("sports_data")
    return tuple(services)


def build_intent_route(message: str) -> IntentRoute:
    low = message.lower().strip()
    intents = detect_intents(message)
    explicit_code = _contains_any(low, (
        "代码", "脚本", "python", "程序", "实现", "开发", "修改文件",
        "写代码", "编写代码", "策略代码", "保存为.py", ".py",
        "script", "code", "program", "implement", "edit file", "write file",
    ))
    try:
        from intent_classifier import (
            INTENT_ANALYSIS,
            INTENT_CODING,
            INTENT_FINANCE,
            INTENT_GENERAL,
            INTENT_REALTIME,
            classify_intent_sync,
            is_visual_market_artifact_request,
        )
        classifier_intent = classify_intent_sync(message)
        visual_artifact = bool(is_visual_market_artifact_request(message))
    except Exception:
        INTENT_ANALYSIS = "analysis"
        INTENT_CODING = "coding"
        INTENT_FINANCE = "finance"
        INTENT_GENERAL = "general"
        INTENT_REALTIME = "realtime"
        classifier_intent = INTENT_FINANCE
        visual_artifact = any(i in intents for i in ("chart", "dashboard", "report", "ui_artifact"))

    if intents:
        primary = intents[0]
    elif classifier_intent == INTENT_REALTIME:
        primary = "market_snapshot"
    elif classifier_intent == INTENT_ANALYSIS:
        primary = "market_analysis"
    elif classifier_intent == INTENT_CODING:
        primary = "code"
    elif classifier_intent == INTENT_GENERAL:
        primary = "general"
    elif classifier_intent == INTENT_FINANCE:
        primary = "finance"
    else:
        primary = "finance"

    market_related = any(i in intents for i in (
        "market_snapshot", "market_analysis", "chart", "dashboard", "report",
        "backtest", "strategy", "market_research",
    )) or classifier_intent in {INTENT_ANALYSIS, INTENT_REALTIME, INTENT_FINANCE}

    return IntentRoute(
        message=message,
        primary=primary,
        intents=intents,
        services=_service_names(intents),
        explicit_code=explicit_code,
        visual_artifact=visual_artifact,
        market_related=market_related,
    )
