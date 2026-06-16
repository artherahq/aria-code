"""Team command parsing and execution helpers."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
import io
import logging
import sys
from pathlib import Path
from typing import Any, Callable


def _detect_lang(text: str) -> str:
    if not text:
        return "zh"
    zh_chars = sum(1 for c in text if '一' <= c <= '鿿')
    return "zh" if zh_chars / max(len(text), 1) > 0.15 else "en"

logger = logging.getLogger(__name__)

DEFAULT_TEAM_AGENTS = ["macro", "fundamental", "technical", "risk"]
FULL_TEAM_AGENTS = ["macro", "fundamental", "technical", "risk", "news", "catalyst", "sector"]


@dataclass(frozen=True)
class TeamArgs:
    symbols_raw: list[str]
    agent_names: list[str] | None = None
    use_full_team: bool = False


@dataclass(frozen=True)
class TeamAnalysisResult:
    symbol: str
    team_result: Any
    data_bundle: Any = None
    quality_notes: list[str] | None = None
    captured_noise: str = ""


@dataclass(frozen=True)
class SavedTeamReport:
    path: Path
    metadata_path: Path | None = None


def parse_team_args(args: str) -> TeamArgs:
    parts = args.strip().split()
    agent_names = None
    symbols_raw: list[str] = []
    use_full_team = False
    idx = 0
    while idx < len(parts):
        part = parts[idx]
        if part == "--agents" and idx + 1 < len(parts):
            agent_names = [agent.strip() for agent in parts[idx + 1].split(",") if agent.strip()]
            idx += 2
        elif part.startswith("--agents="):
            agent_names = [agent.strip() for agent in part.split("=", 1)[1].split(",") if agent.strip()]
            idx += 1
        elif part == "--full":
            use_full_team = True
            idx += 1
        else:
            symbols_raw.append(part)
            idx += 1
    if use_full_team and not agent_names:
        agent_names = list(FULL_TEAM_AGENTS)
    return TeamArgs(symbols_raw=symbols_raw, agent_names=agent_names, use_full_team=use_full_team)


def resolve_team_symbols(args: TeamArgs, config: dict[str, Any], limit: int = 3) -> list[str]:
    if not args.symbols_raw or args.symbols_raw[0].lower() == "watchlist":
        return [str(symbol).upper() for symbol in config.get("watchlist", ["AAPL", "MSFT", "NVDA"])[:limit]]
    return [symbol.upper() for symbol in args.symbols_raw[:limit]]


def team_agent_names(args: TeamArgs) -> list[str]:
    return args.agent_names or list(DEFAULT_TEAM_AGENTS)


def compact_market_cap(value: Any, currency: str = "USD") -> str:
    try:
        cap = float(value)
        if cap <= 0:
            return "-"
        if cap >= 1e12:
            return f"{currency} {cap / 1e12:.2f}T"
        if cap >= 1e9:
            return f"{currency} {cap / 1e9:.1f}B"
        if cap >= 1e6:
            return f"{currency} {cap / 1e6:.0f}M"
        return f"{currency} {cap:,.0f}"
    except Exception:
        return "-"


def team_quote_snapshot(data_bundle: Any) -> dict[str, Any]:
    quote = getattr(data_bundle, "quote", {}) or {}
    quality = getattr(data_bundle, "quality", {}) or {}
    return {
        "price": quote.get("price") or quote.get("current_price") or quote.get("regular_market_price"),
        "change_pct": quote.get("change_pct") or quote.get("change_percent") or quote.get("pct_change"),
        "currency": quote.get("currency") or quote.get("currency_symbol") or "USD",
        "market_cap": quote.get("market_cap") or quote.get("marketCap"),
        "provider_chain": getattr(data_bundle, "provider_chain", []) or [],
        "missing_fields": getattr(data_bundle, "missing_fields", []) or [],
        "warnings": getattr(data_bundle, "warnings", []) or [],
        "errors": getattr(data_bundle, "errors", []) or [],
        "quality": quality,
        "stale": bool(quality.get("stale", False)),
        "status": getattr(data_bundle, "status", "data_unavailable"),
        "timestamp": getattr(data_bundle, "timestamp", ""),
    }


def build_team_llm_provider(config: dict[str, Any]) -> Any:
    try:
        from providers.llm.base import ProviderConfig
        from providers.llm.ollama import OllamaProvider

        model = config.get("model", "qwen2.5:7b")
        url = config.get("ollama_url", "http://localhost:11434")
        return OllamaProvider(ProviderConfig(name="ollama", model=model, base_url=url))
    except Exception as exc:
        logger.debug("team LLM provider init failed: %s", exc)
        return None


async def fetch_team_data_bundle(symbol: str) -> Any:
    from datasources.router import get_router
    from packages.aria_services.data import DataService

    return await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: DataService(router=get_router()).bundle(symbol),
    )


async def run_team_analysis(
    *,
    symbol: str,
    args: TeamArgs,
    config: dict[str, Any],
    sanitize_result: Callable[[Any, Any], list[str]] | None = None,
    lang: str = "zh",
    on_agent_done: Callable[[str, Any], None] | None = None,
) -> TeamAnalysisResult:
    from agents.team import run_team
    from datasources.router import get_router

    llm_provider = build_team_llm_provider(config)
    data_bundle = None
    try:
        data_bundle = await fetch_team_data_bundle(symbol)
    except Exception as exc:
        logger.debug("team data bundle fetch failed: %s", exc)

    noisy_loggers = ["agents.base", "agents.team", "datasources.router", "data_cleaner"]
    saved_levels = {name: logging.getLogger(name).level for name in noisy_loggers}
    for name in noisy_loggers:
        logging.getLogger(name).setLevel(logging.ERROR)

    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    # The team runs inside a stdout redirect (to swallow noisy agent logs).
    # Restore the real stdout just for the on_agent_done callback so its
    # streaming tree output is actually visible to the user.
    _real_stdout = sys.stdout

    def _agent_done_proxy(name: str, result: Any) -> None:
        if on_agent_done is None:
            return
        try:
            with contextlib.redirect_stdout(_real_stdout):
                on_agent_done(name, result)
        except Exception as exc:
            logger.debug("on_agent_done render failed: %s", exc)

    try:
        with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
            team_result = await run_team(
                symbol=symbol,
                agents=args.agent_names,
                llm_provider=llm_provider,
                data_router=get_router(),
                on_token=None,
                on_agent_done=_agent_done_proxy if on_agent_done else None,
                on_synthesis_start=None,
                lang=lang,
            )
    finally:
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level or logging.NOTSET)

    quality_notes = sanitize_result(team_result, data_bundle) if sanitize_result else []
    captured_noise = (captured_stdout.getvalue() + captured_stderr.getvalue()).strip()
    if captured_noise:
        logger.debug("captured /team noisy output for %s: %s", symbol, captured_noise[:3000])

    return TeamAnalysisResult(
        symbol=symbol,
        team_result=team_result,
        data_bundle=data_bundle,
        quality_notes=quality_notes,
        captured_noise=captured_noise,
    )


def _agent_result_summary(team_result: Any) -> list[dict[str, Any]]:
    return [
        {
            "agent": getattr(result, "agent", ""),
            "success": bool(getattr(result, "success", False)),
            "signal": getattr(result, "signal", None),
            "confidence": getattr(result, "confidence", None),
            "error": getattr(result, "error", None),
            "key_points": getattr(result, "key_points", None),
        }
        for result in getattr(team_result, "results", []) or []
    ]


def build_team_report_markdown(
    *,
    symbol: str,
    team_result: Any,
    data_bundle: Any = None,
    quality_notes: list[str] | None = None,
    created_at: datetime | None = None,
) -> str:
    created = created_at or datetime.now()
    quality_notes = quality_notes or []
    quote_snapshot = team_quote_snapshot(data_bundle) if data_bundle else {}
    currency = quote_snapshot.get("currency") or "USD"
    price = quote_snapshot.get("price")
    cap = quote_snapshot.get("market_cap")
    providers = quote_snapshot.get("provider_chain") or []
    missing = quote_snapshot.get("missing_fields") or []
    warnings = quote_snapshot.get("warnings") or []
    errors = quote_snapshot.get("errors") or []
    quality = quote_snapshot.get("quality") or {}
    stale = bool(quote_snapshot.get("stale"))

    lines = [
        f"# {symbol} 多 Agent 研究报告",
        f"> 生成时间: {created:%Y-%m-%d %H:%M}  |  最终信号: **{team_result.final_signal}**"
        f"  |  置信度: {team_result.confidence:.0%}  |  耗时: {team_result.elapsed_sec:.1f}s",
        "",
        "## 数据质量",
        "",
        f"- 数据状态: `{quote_snapshot.get('status', 'unknown')}`",
        f"- 是否过期: `{'yes' if stale else 'no'}`",
        f"- 当前参考价: `{currency} {price}`" if price is not None else "- 当前参考价: `unavailable`",
        f"- 市值: `{compact_market_cap(cap, currency)}`" if cap else "- 市值: `unavailable`",
        f"- 数据源链: `{', '.join(providers) if providers else 'unknown'}`",
        f"- 缺失字段: `{', '.join(missing) if missing else 'none'}`",
    ]
    if quality_notes:
        lines.append(f"- 输出校验: `{'; '.join(quality_notes)}`")
    if warnings:
        lines.append(f"- 数据警告: `{'; '.join(str(w) for w in warnings[:5])}`")
    if errors:
        lines.append(f"- 数据错误: `{'; '.join(str(e) for e in errors[:5])}`")
    if quality:
        lines.append(f"- 质量摘要: `{quality.get('status', quote_snapshot.get('status', 'unknown'))}`")
    lines += ["", "---", ""]

    for result in getattr(team_result, "results", []) or []:
        if getattr(result, "success", False):
            lines += [
                f"## {result.agent.upper()} ({result.signal}, {result.confidence:.0%})",
                "",
                result.analysis or "*(无分析文本)*",
                "",
            ]
        else:
            lines += [
                f"## {result.agent.upper()} (UNUSABLE)",
                "",
                f"> {result.error or 'agent_failed'}",
                "",
                result.analysis or "*(该 Agent 未产生可用分析文本)*",
                "",
            ]
    lines += ["---", "", "## 综合结论", "", team_result.synthesis or "*(无综合结论)*", ""]
    return "\n".join(lines)


def save_team_report(
    *,
    symbol: str,
    team_result: Any,
    data_bundle: Any = None,
    quality_notes: list[str] | None = None,
    created_at: datetime | None = None,
) -> SavedTeamReport:
    from artifacts import create_artifact, write_artifact_metadata, write_artifact_raw_data

    created = created_at or datetime.now()
    quality_notes = quality_notes or []
    artifact = create_artifact("reports/team", symbol, f"{symbol}_team_report", ".md")
    markdown = build_team_report_markdown(
        symbol=symbol,
        team_result=team_result,
        data_bundle=data_bundle,
        quality_notes=quality_notes,
        created_at=created,
    )
    artifact.path.write_text(markdown, encoding="utf-8")

    quote_snapshot = team_quote_snapshot(data_bundle) if data_bundle else {}
    quality = quote_snapshot.get("quality") or {}
    agent_results = _agent_result_summary(team_result)
    write_artifact_metadata(artifact, {
        "kind": "team_report",
        "status": "complete" if any(result.get("success") for result in agent_results) else "data_unavailable",
        "symbol": symbol,
        "created_at": created.isoformat(timespec="seconds"),
        "data": {
            "agent_count": len(agent_results),
            "successful_agents": sum(1 for result in agent_results if result.get("success")),
            "failed_agents": [result.get("agent") for result in agent_results if not result.get("success")],
            "quote": quote_snapshot,
            "quality": quality,
            "quality_notes": quality_notes,
        },
        "verdict": {
            "final_signal": getattr(team_result, "final_signal", None),
            "confidence": getattr(team_result, "confidence", None),
            "elapsed_sec": getattr(team_result, "elapsed_sec", None),
        },
    })
    write_artifact_raw_data(artifact, {
        "symbol": symbol,
        "agents": agent_results,
        "synthesis": getattr(team_result, "synthesis", None),
        "data_bundle": {
            "quote": getattr(data_bundle, "quote", {}) if data_bundle else {},
            "history": getattr(data_bundle, "history", {}) if data_bundle else {},
            "fundamentals": getattr(data_bundle, "fundamentals", {}) if data_bundle else {},
            "technical": getattr(data_bundle, "technical", {}) if data_bundle else {},
            "provider_chain": getattr(data_bundle, "provider_chain", []) if data_bundle else [],
            "missing_fields": getattr(data_bundle, "missing_fields", []) if data_bundle else [],
            "warnings": getattr(data_bundle, "warnings", []) if data_bundle else [],
            "errors": getattr(data_bundle, "errors", []) if data_bundle else [],
            "quality": getattr(data_bundle, "quality", {}) if data_bundle else {},
            "status": getattr(data_bundle, "status", None) if data_bundle else None,
        },
    })
    return SavedTeamReport(path=artifact.path, metadata_path=artifact.metadata_path)
