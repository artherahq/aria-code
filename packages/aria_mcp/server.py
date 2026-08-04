"""aria-code MCP server — exposes read-only aria-code capabilities over stdio JSON-RPC.

Lets any MCP-compatible client (Claude Code, Codex, Cursor, ...) call into
aria-code's market data, multi-agent research team, and generated-artifact
list. Pattern mirrors Arthera's packages/quant_engine/mcp_server.py (hand-rolled
stdio JSON-RPC, no `mcp` SDK dependency).

Usage (from repo root)::

    python3 aria_mcp_server.py

Register with Claude Code::

    claude mcp add aria-code -- python3 /path/to/aria-code/aria_mcp_server.py

Scope: ``skill:``-backed exposures (report/backtest skills) are never
registered — skills aren't a clean JSON-in/JSON-out callable today. Beyond
that, everything is read-only EXCEPT the small, explicit ``_WRITE_SAFE``
allowlist below (order previews, chart/PDF generation) — each entry there is
individually justified as unable to move money or place a live trade. Order
*execution* (``brokers.trading.execute_order_preview`` with ``confirmed=True``)
is never imported anywhere in this module — confirming a previewed trade
always requires a human running ``/trade confirm <preview_id>`` in the
aria-code terminal itself, never something reachable from an MCP client.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("aria_mcp_server")

MCP_PROTOCOL_VERSION = "2024-11-05"

# ---------------------------------------------------------------------------
# Input schemas for the exposed tools (default_exposures() carries names +
# descriptions but not JSON Schema — kept here, next to the handlers that
# actually consume the arguments).
# ---------------------------------------------------------------------------

_INPUT_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "aria.market.quote": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Ticker/symbol, e.g. AAPL, 600519, BTC-USD"},
        },
        "required": ["symbol"],
    },
    "aria.agent.team": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Ticker/symbol to research"},
            "agents": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subset of agent names to run (default: all)",
            },
        },
        "required": ["symbol"],
    },
    "aria.artifacts.list": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max artifacts to return (default 20)"},
        },
        "required": [],
    },
    "aria.broker.positions": {
        "type": "object",
        "properties": {
            "broker_id": {"type": "string", "description": "Configured broker id (~/.aria-code/brokers.json). Omit to use the default broker."},
        },
        "required": [],
    },
    "aria.broker.list_previews": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max previews to return (default 10)"},
        },
        "required": [],
    },
    "aria.broker.preview_order": {
        "type": "object",
        "properties": {
            "broker_id": {"type": "string", "description": "Configured broker id. Omit to use the default broker."},
            "symbol": {"type": "string", "description": "Ticker/symbol to trade"},
            "side": {"type": "string", "enum": ["buy", "sell"]},
            "quantity": {"type": "number", "description": "Share/unit quantity"},
            "order_type": {"type": "string", "enum": ["limit", "market"], "description": "Default: limit"},
            "price": {"type": "number", "description": "Limit price (required for limit orders)"},
        },
        "required": ["symbol", "side"],
    },
    "aria.report.chart": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Ticker/symbol to chart"},
        },
        "required": ["symbol"],
    },
    "aria.report.pdf": {
        "type": "object",
        "properties": {
            "markdown": {"type": "string", "description": "Markdown text to render"},
            "title": {"type": "string", "description": "Report title, used for the filename"},
        },
        "required": ["markdown"],
    },
    "aria.report.generate_image": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Text prompt (e.g. minimal-editorial-poster's compiled prompt)"},
            "size": {"type": "string", "enum": ["1024x1024", "1536x1024", "1024x1536", "auto"], "description": "Default: 1024x1536 (portrait poster)"},
            "quality": {"type": "string", "enum": ["low", "medium", "high", "auto"], "description": "Default: high"},
        },
        "required": ["prompt"],
    },
    "aria.report.edit_image": {
        "type": "object",
        "properties": {
            "image_path": {"type": "string", "description": "Local path to the existing photo to transform"},
            "prompt": {"type": "string", "description": "How to transform it, e.g. \"convert to duotone with warm ochre accent, simplify the busy rock background into flat negative space, add subtle scan-noise texture\""},
            "size": {"type": "string", "enum": ["1024x1024", "1536x1024", "1024x1536", "auto"], "description": "Default: 1024x1536"},
            "quality": {"type": "string", "enum": ["low", "medium", "high", "auto"], "description": "Default: high"},
        },
        "required": ["image_path", "prompt"],
    },
    "aria.report.generate_image_local": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Text prompt (e.g. minimal-editorial-poster's compiled prompt)"},
            "width": {"type": "integer", "description": "Default: 1024"},
            "height": {"type": "integer", "description": "Default: 1024"},
            "steps": {"type": "integer", "description": "Inference steps; default 4 (tuned for SDXL-Turbo)"},
        },
        "required": ["prompt"],
    },
    "aria.report.edit_image_local": {
        "type": "object",
        "properties": {
            "image_path": {"type": "string", "description": "Local path to the existing photo to transform"},
            "prompt": {"type": "string", "description": "How to transform it"},
            "strength": {"type": "number", "description": "0-1, how much to diverge from the original (default 0.6)"},
        },
        "required": ["image_path", "prompt"],
    },
    "aria.report.canva_design": {
        "type": "object",
        "properties": {
            "template_id": {"type": "string", "description": "Canva brand template id"},
            "data": {"type": "object", "description": "Field name -> typed DatasetValue, e.g. {\"headline\": {\"type\": \"text\", \"text\": \"Q3 Report\"}, \"chart_img\": {\"type\": \"image\", \"asset_id\": \"<from aria.report.canva_upload_asset>\"}}. Value types: text, image, video, chart, sheet — see Canva's Autofill API docs."},
        },
        "required": ["template_id", "data"],
    },
    "aria.report.canva_upload_asset": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Local path to the image file to upload (e.g. the path returned by aria.report.chart)"},
        },
        "required": ["file_path"],
    },
    "aria.report.docx": {
        "type": "object",
        "properties": {
            "markdown": {"type": "string", "description": "Markdown text to render"},
            "title": {"type": "string", "description": "Document title, used for the filename and cover heading"},
        },
        "required": ["markdown"],
    },
    "aria.report.pptx": {
        "type": "object",
        "properties": {
            "markdown": {"type": "string", "description": "Markdown text to render (each heading becomes a slide)"},
            "title": {"type": "string", "description": "Deck title, used for the filename and title slide"},
        },
        "required": ["markdown"],
    },
    "aria.figma.read_file": {
        "type": "object",
        "properties": {
            "file_key": {"type": "string", "description": "Figma file key (from the file's URL: figma.com/file/<file_key>/...)"},
            "depth": {"type": "integer", "description": "How many tree levels to include per page (default 2)"},
        },
        "required": ["file_key"],
    },
    "aria.figma.comments": {
        "type": "object",
        "properties": {
            "file_key": {"type": "string", "description": "Figma file key"},
        },
        "required": ["file_key"],
    },
    "aria.report.generate": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Ticker/symbol to research"},
        },
        "required": ["symbol"],
    },
    "aria.backtest.run": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Ticker/symbol to backtest"},
            "strategy": {"type": "string", "enum": ["buy_hold", "sma_cross"], "description": "Default: sma_cross"},
            "days": {"type": "integer", "description": "History window in days (default 365)"},
        },
        "required": ["symbol"],
    },
}

# Exposures that are NOT read_only (they write a local file, a preview
# record, or a Canva design draft) but are individually verified safe to
# auto-execute with no human confirmation: none of them can move money,
# place a live trade, or touch anything outside the local workspace/artifact
# directories or the user's own Canva account. Every other non-read_only
# exposure stays blocked by default — see _build_tools().
_WRITE_SAFE = {
    "aria.broker.preview_order", "aria.report.chart", "aria.report.pdf",
    "aria.report.canva_design", "aria.report.canva_upload_asset",
    "aria.report.docx", "aria.report.pptx",
    "aria.report.generate", "aria.backtest.run",
    "aria.report.generate_image", "aria.report.edit_image",
    "aria.report.generate_image_local", "aria.report.edit_image_local",
}


# ---------------------------------------------------------------------------
# Handlers — each is `async def handler(arguments: dict) -> dict`
# ---------------------------------------------------------------------------

async def _call_market_quote(args: Dict[str, Any]) -> Dict[str, Any]:
    from runtime.tool_executor import ToolExecutor
    from apps.cli.tools.market_tools import tool_get_market_data

    executor = ToolExecutor({"get_market_data": (tool_get_market_data, "quote + technicals")})
    return await executor.execute("get_market_data", {"symbol": args.get("symbol", "")})


async def _call_agent_team(args: Dict[str, Any]) -> Dict[str, Any]:
    from dataclasses import asdict

    from agents.team import run_team
    from datasources.router import get_router

    symbol = str(args.get("symbol", "")).strip()
    if not symbol:
        return {"success": False, "error": "symbol is required"}
    try:
        data_router = get_router()
    except Exception as exc:
        logger.debug("get_router() unavailable, running without live data bundle: %s", exc)
        data_router = None
    # llm_provider intentionally left None: agents fall back to their
    # deterministic template mode (see agents/base.py) rather than this
    # server needing to bootstrap the CLI's full provider/config stack.
    # Output is real (real data, real rule-based signals) but not
    # LLM-narrated — documented in the tool description surfaced to clients.
    result = await run_team(symbol, agents=args.get("agents"), llm_provider=None, data_router=data_router)
    return {"success": True, **asdict(result)}


async def _call_artifacts_list(args: Dict[str, Any]) -> Dict[str, Any]:
    from artifacts import recent_artifacts_all

    limit = int(args.get("limit", 20) or 20)
    return {"success": True, "artifacts": recent_artifacts_all(limit=limit)}


def _get_broker(broker_id: str = ""):
    from brokers.registry import get_registry

    reg = get_registry()
    if broker_id:
        return reg.connect(broker_id)
    broker = reg.connect_default()
    if broker is None:
        raise ValueError("No broker_id given and no default broker configured (~/.aria-code/brokers.json)")
    return broker


async def _call_broker_positions(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio
    from dataclasses import asdict

    loop = asyncio.get_event_loop()

    def _run():
        broker = _get_broker(str(args.get("broker_id", "")))
        return asdict(broker.account_info()), [asdict(p) for p in broker.positions()]

    try:
        account, positions = await loop.run_in_executor(None, _run)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "account": account, "positions": positions}


async def _call_broker_list_previews(args: Dict[str, Any]) -> Dict[str, Any]:
    from brokers.trading import list_order_previews

    limit = int(args.get("limit", 10) or 10)
    return {"success": True, "previews": list_order_previews(limit=limit)}


async def _call_broker_preview_order(args: Dict[str, Any]) -> Dict[str, Any]:
    # This module never imports execute_order_preview — building a preview
    # here can never result in a live order. See module docstring.
    import asyncio

    from brokers.trading import OrderIntent, build_order_preview

    symbol = str(args.get("symbol", "")).strip()
    side = str(args.get("side", "")).strip().lower()
    if not symbol or side not in ("buy", "sell"):
        return {"success": False, "error": "symbol and side (buy/sell) are required"}

    intent = OrderIntent(
        symbol=symbol,
        side=side,
        quantity=args.get("quantity"),
        order_type=str(args.get("order_type") or "limit"),
        price=args.get("price"),
        source="mcp",
    )
    loop = asyncio.get_event_loop()

    def _run():
        broker = _get_broker(str(args.get("broker_id", "")))
        return build_order_preview(broker, intent)

    try:
        preview = await loop.run_in_executor(None, _run)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, **preview}


async def _call_report_chart(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio
    import base64

    from artifacts import create_user_artifact
    from report_generator import _fetch_report_data_sync, generate_price_chart

    symbol = str(args.get("symbol", "")).strip().upper()
    if not symbol:
        return {"success": False, "error": "symbol is required"}

    loop = asyncio.get_event_loop()

    def _run():
        df, _clean_result, fundamentals = _fetch_report_data_sync(symbol)
        if df is None or df.empty:
            return None
        return generate_price_chart(df, symbol, fundamentals)

    try:
        b64_png = await loop.run_in_executor(None, _run)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not b64_png:
        return {"success": False, "error": f"No price data available for {symbol}"}

    artifact = create_user_artifact("chart", symbol, f"{symbol}_chart", ".png")
    artifact.path.write_bytes(base64.b64decode(b64_png))
    return {"success": True, "path": str(artifact.path)}


async def _call_report_pdf(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from artifacts import create_user_artifact
    from markdown_pdf import markdown_to_pdf

    md = str(args.get("markdown", ""))
    if not md.strip():
        return {"success": False, "error": "markdown is required"}
    title = str(args.get("title") or "report").strip() or "report"

    artifact = create_user_artifact("report", title, title, ".pdf")
    loop = asyncio.get_event_loop()
    try:
        result_path = await loop.run_in_executor(None, markdown_to_pdf, md, artifact.path)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not result_path:
        return {"success": False, "error": "PDF rendering failed (no HTML→PDF engine available)"}
    return {"success": True, "path": str(result_path)}


async def _call_report_generate(args: Dict[str, Any]) -> Dict[str, Any]:
    from report_generator import generate_report

    symbol = str(args.get("symbol", "")).strip().upper()
    if not symbol:
        return {"success": False, "error": "symbol is required"}
    try:
        path = await generate_report(symbol)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not path:
        return {"success": False, "error": f"Report generation failed for {symbol} (no data?)"}
    return {"success": True, "path": str(path)}


async def _call_backtest_run(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio
    from dataclasses import asdict

    from backtest_engine import BacktestEngine, get_strategy, load_bars

    symbol = str(args.get("symbol", "")).strip().upper()
    if not symbol:
        return {"success": False, "error": "symbol is required"}
    strategy_name = str(args.get("strategy") or "sma_cross")
    days = int(args.get("days", 365) or 365)

    loop = asyncio.get_event_loop()

    def _run():
        bars = load_bars(symbol, days=days)
        if not bars:
            raise ValueError(f"No price history available for {symbol}")
        strategy = get_strategy(strategy_name)
        engine = BacktestEngine()
        return engine.run({symbol: bars}, strategy)

    try:
        result = await loop.run_in_executor(None, _run)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, **asdict(result)}


async def _call_figma_read_file(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from figma_client import get_file_summary

    file_key = str(args.get("file_key", "")).strip()
    if not file_key:
        return {"success": False, "error": "file_key is required"}
    depth = int(args.get("depth", 2) or 2)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, lambda: get_file_summary(file_key, depth=depth))
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def _call_figma_comments(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from figma_client import list_comments

    file_key = str(args.get("file_key", "")).strip()
    if not file_key:
        return {"success": False, "error": "file_key is required"}
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, list_comments, file_key)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def _call_report_docx(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from artifacts import create_user_artifact
    from report_exporters import markdown_to_docx

    md = str(args.get("markdown", ""))
    if not md.strip():
        return {"success": False, "error": "markdown is required"}
    title = str(args.get("title") or "report").strip() or "report"

    artifact = create_user_artifact("report", title, title, ".docx")
    loop = asyncio.get_event_loop()
    try:
        result_path = await loop.run_in_executor(None, markdown_to_docx, md, artifact.path, title)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not result_path:
        return {"success": False, "error": "DOCX rendering failed (python-docx not installed?)"}
    return {"success": True, "path": str(result_path)}


async def _call_report_pptx(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from artifacts import create_user_artifact
    from report_exporters import markdown_to_pptx

    md = str(args.get("markdown", ""))
    if not md.strip():
        return {"success": False, "error": "markdown is required"}
    title = str(args.get("title") or "report").strip() or "report"

    artifact = create_user_artifact("report", title, title, ".pptx")
    loop = asyncio.get_event_loop()
    try:
        result_path = await loop.run_in_executor(None, markdown_to_pptx, md, artifact.path, title)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not result_path:
        return {"success": False, "error": "PPTX rendering failed (python-pptx not installed?)"}
    return {"success": True, "path": str(result_path)}


async def _call_generate_image(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio
    from functools import partial

    from openai_image_client import generate_image

    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return {"success": False, "error": "prompt is required"}

    loop = asyncio.get_event_loop()
    try:
        fn = partial(generate_image, prompt, size=args.get("size") or "1024x1536", quality=args.get("quality") or "high")
        return await loop.run_in_executor(None, fn)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def _call_edit_image(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio
    from functools import partial

    from openai_image_client import edit_image

    image_path = str(args.get("image_path", "")).strip()
    prompt = str(args.get("prompt", "")).strip()
    if not image_path or not prompt:
        return {"success": False, "error": "image_path and prompt are required"}

    loop = asyncio.get_event_loop()
    try:
        fn = partial(edit_image, image_path, prompt, size=args.get("size") or "1024x1536", quality=args.get("quality") or "high")
        return await loop.run_in_executor(None, fn)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def _call_generate_image_local(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio
    from functools import partial

    from local_image_provider import generate_image_local

    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return {"success": False, "error": "prompt is required"}

    loop = asyncio.get_event_loop()
    fn = partial(
        generate_image_local,
        prompt,
        width=int(args.get("width", 1024) or 1024),
        height=int(args.get("height", 1024) or 1024),
        steps=int(args.get("steps", 4) or 4),
    )
    try:
        return await loop.run_in_executor(None, fn)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def _call_edit_image_local(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio
    from functools import partial

    from local_image_provider import edit_image_local

    image_path = str(args.get("image_path", "")).strip()
    prompt = str(args.get("prompt", "")).strip()
    if not image_path or not prompt:
        return {"success": False, "error": "image_path and prompt are required"}

    loop = asyncio.get_event_loop()
    fn = partial(edit_image_local, image_path, prompt, strength=float(args.get("strength", 0.6) or 0.6))
    try:
        return await loop.run_in_executor(None, fn)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def _call_canva_design(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from canva_client import autofill_design

    template_id = str(args.get("template_id", "")).strip()
    data = args.get("data") or {}
    if not template_id or not isinstance(data, dict):
        return {"success": False, "error": "template_id and data (object) are required"}

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, autofill_design, template_id, data)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return result


async def _call_canva_upload_asset(args: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from canva_client import upload_asset

    file_path = str(args.get("file_path", "")).strip()
    if not file_path:
        return {"success": False, "error": "file_path is required"}

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, upload_asset, file_path)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    return result


_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
    "aria.market.quote": _call_market_quote,
    "aria.agent.team": _call_agent_team,
    "aria.artifacts.list": _call_artifacts_list,
    "aria.broker.positions": _call_broker_positions,
    "aria.broker.list_previews": _call_broker_list_previews,
    "aria.broker.preview_order": _call_broker_preview_order,
    "aria.report.chart": _call_report_chart,
    "aria.report.pdf": _call_report_pdf,
    "aria.report.generate_image": _call_generate_image,
    "aria.report.edit_image": _call_edit_image,
    "aria.report.generate_image_local": _call_generate_image_local,
    "aria.report.edit_image_local": _call_edit_image_local,
    "aria.report.canva_design": _call_canva_design,
    "aria.report.canva_upload_asset": _call_canva_upload_asset,
    "aria.report.docx": _call_report_docx,
    "aria.report.pptx": _call_report_pptx,
    "aria.figma.read_file": _call_figma_read_file,
    "aria.figma.comments": _call_figma_comments,
    "aria.report.generate": _call_report_generate,
    "aria.backtest.run": _call_backtest_run,
}


def _build_tools() -> List[Dict[str, Any]]:
    """Real MCP tool descriptors — read-only exposures, plus the explicit
    _WRITE_SAFE allowlist.

    Hard filter, not just trust in the exposure list: any exposure whose
    target is skill-backed (not a clean callable in v1), or that isn't
    read_only AND isn't in _WRITE_SAFE, is dropped here — so a future
    careless edit to default_exposures() can't silently make a live-trading
    or otherwise unreviewed write tool reachable. Adding something to
    _WRITE_SAFE is a deliberate, individually-justified code change, not a
    side effect of editing the exposure list.
    """
    from packages.aria_mcp.bridge import default_exposures

    tools = []
    for exposure in default_exposures():
        if not exposure.read_only and exposure.name not in _WRITE_SAFE:
            continue
        if exposure.target.startswith("skill:"):
            continue
        if exposure.name not in _HANDLERS:
            continue
        tools.append({
            "name": exposure.name,
            "description": exposure.description,
            "inputSchema": _INPUT_SCHEMAS.get(exposure.name, {"type": "object", "properties": {}}),
        })
    return tools


TOOLS: List[Dict[str, Any]] = _build_tools()
_TOOL_NAMES = {t["name"] for t in TOOLS}


# ---------------------------------------------------------------------------
# MCP stdio server (JSON-RPC 2.0) — same wire format as
# Arthera's packages/quant_engine/mcp_server.py
# ---------------------------------------------------------------------------

def _reply(req_id: Any, result: Any) -> None:
    msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}, ensure_ascii=False)
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _error(req_id: Any, code: int, message: str) -> None:
    msg = json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


async def _handle(req: Dict[str, Any]) -> None:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {}) or {}

    if method == "initialize":
        _reply(req_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "aria-code", "version": "1.0.0"},
        })

    elif method == "notifications/initialized":
        pass  # no response needed for notifications

    elif method == "tools/list":
        _reply(req_id, {"tools": TOOLS})

    elif method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments", {}) or {}
        if name not in _TOOL_NAMES:
            _error(req_id, -32601, f"Unknown or non-exposed tool: {name}")
            return
        try:
            result = await _HANDLERS[name](arguments)
            text = json.dumps(result, ensure_ascii=False, default=str)
            _reply(req_id, {"content": [{"type": "text", "text": text}]})
        except TypeError as exc:
            _error(req_id, -32602, f"Invalid arguments for {name}: {exc}")
        except Exception as exc:
            _error(req_id, -32603, f"Tool error: {exc}\n{traceback.format_exc()[-300:]}")

    elif method == "ping":
        _reply(req_id, {})

    else:
        if req_id is not None:
            _error(req_id, -32601, f"Method not found: {method}")


def main() -> None:
    import asyncio

    logger.info("aria-code MCP server starting (stdio transport, %d tools)", len(TOOLS))

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("JSON parse error: %s", exc)
                continue
            try:
                await _handle(req)
            except Exception as exc:
                logger.error("Unhandled error: %s\n%s", exc, traceback.format_exc())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
