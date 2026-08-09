"""CLI bridge for QuantEngine's auditable next-session A-share predictions.

The bridge intentionally keeps prediction execution explicit.  A market snapshot
is cheap and safe to show automatically; a cross-sectional prediction run may
fetch thousands of histories and writes an auditable report, so it only runs
after the user enters ``/ashare predict``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path


@dataclass(frozen=True)
class ASharePredictArgs:
    universe: str = "core50"
    limit: int | None = None
    period: str = "1y"
    strategy: str = "momentum"
    include_ai_analysis: bool = True
    include_ml_confirmation: bool = True
    universe_file: str = ""
    live_universe: bool = False


def parse_ashare_predict_args(text: str) -> ASharePredictArgs:
    """Parse ``/ashare predict`` flags without importing the QuantEngine."""
    tokens = str(text or "").split()
    universe = "core50"
    limit: int | None = None
    period, strategy, universe_file = "1y", "momentum", ""
    include_ai_analysis = include_ml_confirmation = True
    live_universe = False
    index = 0
    if tokens and not tokens[0].startswith("--"):
        universe = tokens[0].lower()
        index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"--limit", "--period", "--strategy", "--universe-file"}:
            if index + 1 >= len(tokens):
                raise ValueError(f"{token} requires a value")
            value = tokens[index + 1]
            if token == "--limit":
                limit = int(value)
                if limit < 1:
                    raise ValueError("--limit must be positive")
            elif token == "--period":
                period = value
            elif token == "--strategy":
                strategy = value
            else:
                universe_file = value
            index += 2
            continue
        if token == "--no-ai":
            include_ai_analysis = False
        elif token == "--no-ml":
            include_ml_confirmation = False
        elif token == "--live-universe":
            live_universe = True
        else:
            raise ValueError(f"unknown option: {token}")
        index += 1
    if universe not in {"core50", "default", "broad", "broad200", "all"}:
        raise ValueError("universe must be core50, broad, broad200, or all")
    return ASharePredictArgs(
        universe=universe, limit=limit, period=period, strategy=strategy,
        include_ai_analysis=include_ai_analysis,
        include_ml_confirmation=include_ml_confirmation,
        universe_file=universe_file,
        live_universe=live_universe,
    )


def load_universe_file(path: str) -> list[str]:
    """Load a simple comma/newline-separated stock universe with no coercion."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError(f"universe file not found: {source}")
    return list(dict.fromkeys(
        item.strip().upper()
        for item in source.read_text(encoding="utf-8").replace("\n", ",").split(",")
        if item.strip()
    ))


def fetch_live_ashare_universe(*, include_st: bool = False) -> tuple[list[str], dict]:
    """Get the current listed A-share code universe from AkShare's spot feed.

    This is only universe discovery; it does not claim price history is clean or
    tradeable.  The prediction service subsequently applies its own data-quality
    and execution gates before issuing any candidate label.
    """
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("AkShare is required: install aria-code[cn]") from exc
    frame = ak.stock_zh_a_spot_em()
    if frame is None or getattr(frame, "empty", True):
        raise RuntimeError("AkShare returned an empty A-share spot universe")
    records = frame.to_dict("records")
    codes, excluded_st = [], 0
    allowed_prefixes = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "4", "8")
    for row in records:
        code = str(row.get("代码") or row.get("code") or "").strip()
        name = str(row.get("名称") or row.get("name") or "").upper()
        if len(code) != 6 or not code.isdigit() or not code.startswith(allowed_prefixes):
            continue
        if not include_st and ("ST" in name or "退" in name):
            excluded_st += 1
            continue
        codes.append(code)
    codes = list(dict.fromkeys(codes))
    if not codes:
        raise RuntimeError("No eligible A-share codes found in AkShare spot feed")
    return codes, {"source": "akshare.stock_zh_a_spot_em", "count": len(codes), "excluded_st": excluded_st}


def prediction_freshness(result: dict, today: date | None = None) -> dict:
    """Classify an existing run without claiming a stale result is live."""
    today = today or date.today()
    quality = result.get("dataQuality") or {}
    latest = str(quality.get("latestDataDate") or "")
    try:
        last_date = date.fromisoformat(f"{latest[:4]}-{latest[4:6]}-{latest[6:8]}")
        age_days = (today - last_date).days
    except (TypeError, ValueError):
        last_date, age_days = None, None
    return {
        "latest_data_date": latest or None,
        "age_days": age_days,
        "is_quality_pass": quality.get("qualityStatus") == "pass",
        "is_current": bool(last_date and age_days is not None and age_days <= 1),
    }


def build_prediction_service(config: dict):
    """Create the platform service with a stable, user-visible report location."""
    from packages.quant_engine.services.daily_ashare_prediction_service import DailyASharePredictionService

    service = DailyASharePredictionService()
    configured = str(config.get("ashare_prediction_dir") or os.getenv("ARTHERA_DAILY_PREDICTION_DIR") or "").strip()
    if configured:
        service.storage_dir = Path(configured).expanduser()
    else:
        # Existing platform installs wrote their reports here.  Prefer that
        # location when present so the CLI can diagnose historical freshness.
        platform_dir = Path(__file__).resolve().parents[4] / "Arthera" / "packages" / "risk_reports" / "ashare_daily"
        if platform_dir.is_dir():
            service.storage_dir = platform_dir
    service.storage_dir.mkdir(parents=True, exist_ok=True)
    return service


class ASharePredictionCommandsMixin:
    """Expose QuantEngine prediction runs to the interactive and direct CLI."""

    async def cmd_ashare(self, args: str):
        parts = args.strip().split(maxsplit=1)
        action = parts[0].lower() if parts else "status"
        rest = parts[1] if len(parts) > 1 else ""
        if action in {"status", "latest"}:
            self._print_ashare_status(show_candidates=action == "latest")
            return
        if action == "predict":
            try:
                parsed = parse_ashare_predict_args(rest)
                symbols = load_universe_file(parsed.universe_file) if parsed.universe_file else None
            except ValueError as exc:
                msg = f"⚠ {exc}"
                console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                return

            # The service's built-in 'all' alias is a curated broad list, not
            # every listed A-share.  Do not let the CLI falsely label it 'all'.
            if parsed.universe == "all" and parsed.live_universe:
                try:
                    symbols, universe_meta = await asyncio.to_thread(fetch_live_ashare_universe)
                except Exception as exc:
                    msg = f"无法构建实时全 A 股股票池：{exc}"
                    console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                    return
                console.print(
                    f"[dim]已从 {universe_meta['source']} 获取 {universe_meta['count']} 只股票；"
                    f"已排除 {universe_meta['excluded_st']} 只 ST/退市风险标的。[/dim]"
                    if HAS_RICH else f"Live universe: {universe_meta['count']} stocks"
                )
            if parsed.universe == "all" and not symbols:
                msg = (
                    "全 A 股运行需要 --universe-file <文件>（一行/逗号分隔的完整当日股票池）。\n"
                    "内置 all 只是精选 broad 股票池，不能冒充全市场。"
                )
                console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                return

            # No hidden 500-stock truncation for an explicit externally supplied
            # full universe.  The service still receives a bounded list.
            limit = parsed.limit or (len(symbols) if symbols else 500)
            if symbols:
                symbols = symbols[:limit]
            scope = f"自定义股票池 {len(symbols)} 只" if symbols else f"{parsed.universe}（最多 {limit} 只）"
            notice = (
                f"开始 A 股次交易日预测：{scope} · {parsed.period} · {parsed.strategy}\n"
                "将拉取历史行情并生成可审计报告；结果仅供研究/仿真，不执行交易。"
            )
            console.print(f"[dim]{notice}[/dim]") if HAS_RICH else print(notice)
            try:
                service = build_prediction_service(config)
                result = await asyncio.to_thread(
                    service.run_once, symbols=symbols, universe=parsed.universe, limit=limit,
                    period=parsed.period, strategy=parsed.strategy,
                    include_ai_analysis=parsed.include_ai_analysis,
                    include_ml_confirmation=parsed.include_ml_confirmation,
                )
            except Exception as exc:
                msg = f"A 股预测引擎未能完成：{exc}"
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            self._print_ashare_result(result)
            return
        if action == "evaluate":
            try:
                result = await asyncio.to_thread(build_prediction_service(self.terminal.config).evaluate_latest, rest.strip() or None)
            except Exception as exc:
                msg = f"A 股预测评估未能完成：{exc}"
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            console.print(result) if HAS_RICH else print(result)
            return
        if action == "universe":
            include_st = "--include-st" in rest.split()
            try:
                symbols, meta = await asyncio.to_thread(fetch_live_ashare_universe, include_st=include_st)
            except Exception as exc:
                msg = f"无法构建实时 A 股股票池：{exc}"
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            msg = f"实时 A 股股票池：{meta['count']} 只 · 来源 {meta['source']} · 排除 ST/退市风险 {meta['excluded_st']} 只"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
            console.print("[dim]运行完整扫描：/ashare predict all --live-universe[/dim]" if HAS_RICH else "Run: /ashare predict all --live-universe")
            return
        msg = "Usage: /ashare [status|latest|universe|predict|evaluate]"
        console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)

    def _print_ashare_status(self, *, show_candidates: bool):
        try:
            result = build_prediction_service(self.terminal.config).latest()
        except Exception as exc:
            result = None
            error = str(exc)
        else:
            error = ""
        if not result:
            msg = f"没有本地预测结果。{'引擎不可用：' + error if error else '运行 /ashare predict core50 开始。'}"
            console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
            return
        freshness = prediction_freshness(result)
        summary = result.get("summary") or {}
        label = "当前/近一日" if freshness["is_current"] else "过期，不能作为当日预测"
        lines = [
            "A 股次交易日预测状态",
            f"运行：{result.get('runId', '-')}",
            f"数据日期：{freshness['latest_data_date'] or '-'} · {label}",
            f"覆盖：{summary.get('succeeded', 0)}/{summary.get('requested', 0)} · 质量：{summary.get('qualityStatus', '-')}",
            f"强候选/候选/弱候选：{summary.get('strongCandidates', 0)}/{summary.get('longCandidates', 0)}/{summary.get('weakCandidates', 0)}",
        ]
        console.print("\n".join(lines))
        if show_candidates:
            self._print_ashare_result(result, include_header=False)

    def _print_ashare_result(self, result: dict, *, include_header: bool = True):
        summary = result.get("summary") or {}
        freshness = prediction_freshness(result)
        predictions = result.get("predictions") or []
        candidates = [item for item in predictions if item.get("signal") in {"strong_candidate", "long_candidate", "weak_candidate"}]
        if include_header:
            title = "A 股次交易日预测（质量通过）" if freshness["is_quality_pass"] else "A 股次交易日预测（质量未通过）"
            console.print(f"[bold]{title}[/bold]" if HAS_RICH else title)
        console.print(
            f"覆盖 {summary.get('succeeded', 0)}/{summary.get('requested', 0)} · "
            f"数据 {freshness['latest_data_date'] or '-'} · "
            f"候选 {len(candidates)} · 阻断 {summary.get('blocked', 0)}"
        )
        if not freshness["is_current"]:
            console.print("[yellow]⚠ 数据不是当前交易日前一日，结果只可作为历史记录。[/yellow]" if HAS_RICH else "⚠ Result is stale; historical only.")
        for item in candidates[:10]:
            pred = item.get("prediction") or {}
            line = (
                f"  {item.get('rank', '-'):>2}. {item.get('symbol', '-')}  {item.get('signal', '-')}  "
                f"上涨概率 {float(pred.get('upProbability') or 0):.1%}  "
                f"预测收益 {float(pred.get('predictedReturn') or 0):+.1%}  "
                f"风险 {float(pred.get('riskScore') or 0):.2f}"
            )
            console.print(line)
        if not candidates:
            console.print("  无通过候选；不应以模型名义强行给出买入名单。")
