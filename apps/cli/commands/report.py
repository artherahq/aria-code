"""Report command parsing and prompt builders."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReportArgs:
    symbol: str = "AAPL"
    fmt: str = "html"
    report_type: str = "standard"
    export_pdf: bool = False
    output_dir: Path | None = None

    @property
    def is_markdown(self) -> bool:
        return self.fmt in ("md", "markdown")


@dataclass(frozen=True)
class SavedMarkdownReport:
    path: Path
    metadata_path: Path | None = None


@dataclass(frozen=True)
class GeneratedHtmlReport:
    path: Path
    team_result: Any = None
    agent_names: tuple[str, ...] = ()


def report_agent_names(report_type: str) -> list[str]:
    if report_type == "deep":
        return ["macro", "fundamental", "technical", "risk", "news", "catalyst", "sector"]
    return ["macro", "fundamental", "technical", "risk"]


def report_file_size_kb(path: Path) -> int:
    return max(1, path.stat().st_size // 1024)


def all_agents_failed(team_result: Any) -> bool:
    results = getattr(team_result, "results", None)
    if not results:
        return False
    non_synthesis = [result for result in results if getattr(result, "agent", None) != "synthesis"]
    if not non_synthesis:
        return False
    return all(not getattr(result, "success", False) for result in non_synthesis)


async def export_report_pdf(report_path: Path) -> Path | None:
    from report_generator import export_pdf

    return await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: export_pdf(report_path),
    )


async def update_report_index(report_dir: Path) -> Path | None:
    from report_generator import update_reports_index

    return await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: update_reports_index(report_dir),
    )


def build_report_llm_provider(config: dict[str, Any]) -> Any:
    try:
        from providers.llm.base import ProviderConfig
        from providers.llm.ollama import OllamaProvider

        model = config.get("model", "qwen2.5:7b")
        url = config.get("ollama_url", "http://localhost:11434")
        return OllamaProvider(ProviderConfig(name="ollama", model=model, base_url=url))
    except Exception as exc:
        logger.debug("[report] llm provider unavailable: %s", exc)
        return None


async def run_report_agents(
    *,
    symbol: str,
    report_type: str,
    config: dict[str, Any],
) -> Any:
    from agents.team import run_team
    from datasources.router import get_router

    agent_names = report_agent_names(report_type)
    llm_provider = build_report_llm_provider(config)
    noisy_loggers = ["agents.base", "datasources.router", "data_cleaner"]
    saved_levels = {name: logging.getLogger(name).level for name in noisy_loggers}

    def suppress_token_stdout(_token: str) -> None:
        return None

    for name in noisy_loggers:
        logging.getLogger(name).setLevel(logging.ERROR)
    try:
        return await run_team(
            symbol=symbol,
            agents=agent_names,
            llm_provider=llm_provider,
            data_router=get_router(),
            on_token=suppress_token_stdout,
        )
    finally:
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level or logging.NOTSET)


async def generate_html_report(
    *,
    symbol: str,
    report_type: str,
    output_dir: Path | None,
    config: dict[str, Any],
) -> GeneratedHtmlReport:
    from report_generator import generate_report

    agent_names = report_agent_names(report_type)
    team_result = None
    try:
        team_result = await run_report_agents(
            symbol=symbol,
            report_type=report_type,
            config=config,
        )
    except Exception as exc:
        logger.debug("[report] team analysis failed: %s", exc)

    path = await generate_report(
        symbol=symbol,
        team_result=team_result,
        output_dir=output_dir,
    )
    return GeneratedHtmlReport(
        path=path,
        team_result=team_result,
        agent_names=tuple(agent_names),
    )


def parse_report_args(args: str) -> ReportArgs:
    parts = args.split()
    symbol = "AAPL"
    fmt = "html"
    report_type = "standard"
    export_pdf = False
    output_dir_arg = None
    skip_next = False

    for idx, part in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        if part.startswith("--format="):
            fmt = part.split("=", 1)[1].lower()
        elif part == "--format" and idx + 1 < len(parts):
            fmt = parts[idx + 1].lower()
            skip_next = True
        elif part.startswith("--type="):
            report_type = part.split("=", 1)[1].lower()
        elif part == "--type" and idx + 1 < len(parts):
            report_type = parts[idx + 1].lower()
            skip_next = True
        elif part == "--pdf":
            export_pdf = True
        elif part.startswith("--output="):
            output_dir_arg = part.split("=", 1)[1]
        elif part == "--output" and idx + 1 < len(parts):
            output_dir_arg = parts[idx + 1]
            skip_next = True
        elif not part.startswith("-"):
            symbol = part.upper()

    output_dir = Path(output_dir_arg).expanduser() if output_dir_arg else None
    return ReportArgs(
        symbol=symbol,
        fmt=fmt,
        report_type=report_type,
        export_pdf=export_pdf,
        output_dir=output_dir,
    )


def _display_value(value: Any, digits: int = 2, suffix: str = "") -> str:
    try:
        if value in (None, "", "N/A", "-", "nan"):
            return "-"
        if isinstance(value, (int, float)):
            return f"{float(value):,.{digits}f}{suffix}"
        return str(value)
    except Exception:
        return "-"


def metric_line(label: str, value: Any, digits: int = 2, suffix: str = "") -> str:
    rendered = _display_value(value, digits=digits, suffix=suffix)
    return f"- {label}: {rendered}" if rendered != "-" else ""


def markdown_data_block(market_data: dict[str, Any]) -> str:
    data_lines = [
        metric_line("当前价", market_data.get("price")),
        metric_line("涨跌", market_data.get("change_pct"), suffix="%"),
        metric_line("RSI(14)", market_data.get("rsi")),
        metric_line("MACD", market_data.get("macd"), digits=4),
        metric_line("MA20", market_data.get("ma20")),
        metric_line("MA60", market_data.get("ma60")),
        metric_line("布林上轨", market_data.get("bb_upper")),
        metric_line("布林下轨", market_data.get("bb_lower")),
    ]
    data_block = "\n".join(line for line in data_lines if line)
    if data_block:
        return data_block
    return "- 实时行情数据暂不可用；报告必须明确说明数据限制，不得编造价格或指标。"


def markdown_provenance_block(data_quality: dict[str, Any], data_bundle: Any = None) -> str:
    provider_chain = (
        data_quality.get("providers")
        or getattr(data_bundle, "provider_chain", [])
        or []
    )
    missing_fields = (
        getattr(data_bundle, "missing_fields", [])
        or data_quality.get("missing_fields")
        or []
    )
    lines = [
        f"- 数据状态: {data_quality.get('status', getattr(data_bundle, 'status', 'unknown') if data_bundle else 'unknown')}",
        f"- 是否过期: {'yes' if data_quality.get('stale') else 'no'}",
        f"- 数据源链: {', '.join(provider_chain) if provider_chain else 'unknown'}",
        f"- 缺失字段: {', '.join(missing_fields) if missing_fields else 'none'}",
    ]
    return "\n".join(lines)


def report_depth_description(report_type: str) -> str:
    if report_type == "deep":
        return "深度（8页）版本：包含估值模型（DCF + 相对估值）、财务分析（3年P&L）、管理层分析、行业竞争格局"
    if report_type == "brief":
        return "简评版本：1页，核心观点 + 关键数据 + 1句话结论"
    return "标准版本：封面、技术分析、基本面概览、风险因素"


def build_markdown_report_prompt(
    *,
    symbol: str,
    report_type: str,
    market_data: dict[str, Any],
    data_quality: dict[str, Any],
    data_bundle: Any = None,
    now: datetime | None = None,
) -> str:
    report_date = (now or datetime.now()).strftime("%Y-%m-%d")
    data_block = markdown_data_block(market_data)
    provenance_block = markdown_provenance_block(data_quality, data_bundle)
    depth = report_depth_description(report_type)
    return (
        f"为 {symbol} 生成一份专业 Markdown 投研报告（{depth}）。\n\n"
        f"**实时数据（仅使用下列已返回字段；缺失字段不要补写）**：\n"
        f"{data_block}\n\n"
        f"**数据质量（必须在报告中如实说明）**：\n"
        f"{provenance_block}\n\n"
        f"报告结构（Markdown）：\n"
        f"# {symbol} 投资研究报告\n"
        f"**评级**: 买入/中性/减持  **目标价**: X.XX  **日期**: {report_date}\n\n"
        f"## 核心观点\n"
        f"## 技术面分析\n"
        f"## 基本面概况\n"
        f"## 风险因素\n"
        f"## 投资建议\n\n"
        f"请用真实数据，不要使用占位符；缺失数据请说明限制，用中文输出。"
    )


def clean_markdown_report_response(text: str) -> str:
    """Remove injected market-data blocks from model output before saving."""

    return re.sub(r"\n*## 📊.*?(?=\n#|\Z)", "", text, flags=re.DOTALL).strip()


def _missing_market_fields(market_data: dict[str, Any], data_bundle: Any = None) -> list[str]:
    bundle_missing = getattr(data_bundle, "missing_fields", None) if data_bundle else None
    if bundle_missing:
        return list(bundle_missing)
    return [
        key
        for key in ("price", "change_pct", "rsi", "macd", "ma20", "ma60")
        if market_data.get(key) in (None, "", 0)
    ]


def _provider_chain(market_data: dict[str, Any], data_quality: dict[str, Any], data_bundle: Any = None) -> list[Any]:
    chain = (
        data_quality.get("providers")
        or getattr(data_bundle, "provider_chain", [])
        or market_data.get("provider_chain")
        or market_data.get("data_provider")
        or []
    )
    return chain if isinstance(chain, list) else [chain]


def save_markdown_report(
    *,
    symbol: str,
    report_type: str,
    markdown_text: str,
    timestamp: str,
    output_dir: Path | None,
    market_data: dict[str, Any],
    data_quality: dict[str, Any],
    data_bundle: Any = None,
    created_at: datetime | None = None,
) -> SavedMarkdownReport:
    """Persist a Markdown report and sidecar metadata when using artifact storage."""

    created = created_at or datetime.now()
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{symbol}_report_{timestamp}.md"
        out_file.write_text(clean_markdown_report_response(markdown_text), encoding="utf-8")
        return SavedMarkdownReport(path=out_file)

    from artifacts import create_artifact, write_artifact_metadata, write_artifact_raw_data

    artifact = create_artifact("reports/market", symbol, f"{symbol}_market_report", ".md")
    out_file = artifact.path
    out_file.write_text(clean_markdown_report_response(markdown_text), encoding="utf-8")

    write_artifact_metadata(artifact, {
        "kind": "market_report",
        "format": "markdown",
        "status": "partial" if market_data else "data_unavailable",
        "symbol": symbol,
        "created_at": created.isoformat(timespec="seconds"),
        "data": {
            "provider_chain": _provider_chain(market_data, data_quality, data_bundle),
            "warnings": data_quality.get("warnings") or getattr(data_bundle, "warnings", []) or [],
            "errors": data_quality.get("errors") or getattr(data_bundle, "errors", []) or [],
            "stale": bool(data_quality.get("stale", False)),
            "quality": data_quality,
            "missing_fields": _missing_market_fields(market_data, data_bundle),
        },
        "report": {
            "type": report_type,
            "metadata_path": str(artifact.metadata_path),
        },
    })
    write_artifact_raw_data(artifact, {
        "symbol": symbol,
        "market_data": market_data,
        "data_bundle": {
            "quote": getattr(data_bundle, "quote", {}) if data_bundle else {},
            "history": getattr(data_bundle, "history", {}) if data_bundle else {},
            "fundamentals": getattr(data_bundle, "fundamentals", {}) if data_bundle else {},
            "technical": getattr(data_bundle, "technical", {}) if data_bundle else {},
            "quality": data_quality,
        },
    })
    return SavedMarkdownReport(path=out_file, metadata_path=artifact.metadata_path)
