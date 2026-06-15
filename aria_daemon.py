#!/usr/bin/env python3
"""
aria_daemon.py — Aria always-on background daemon.

Responsibilities:
  1. Price alert watchdog  — checks SQLite alerts every 30 s
  2. APScheduler cron jobs — morning brief, market scan, custom schedules
  3. Telegram bot          — bidirectional command channel
  4. Webhook job executor  — processes jobs queued by FastAPI /webhook/trigger
  5. APNs push delivery    — fires on alert trigger or scheduled job completion

Start manually:   python3 aria_daemon.py [--debug]
Install daemon:   python3 aria_daemon.py --install   (macOS LaunchAgent)
Uninstall:        python3 aria_daemon.py --uninstall

Config via env vars (or ~/.aria/.env):
  TELEGRAM_BOT_TOKEN        — Telegram bot token from @BotFather
  TELEGRAM_ALLOWED_IDS      — comma-separated chat IDs (e.g. "123456,789012")
  APNS_KEY_ID               — Apple Developer key ID (10-char string)
  APNS_TEAM_ID              — Apple Developer team ID
  APNS_BUNDLE_ID            — App bundle ID (default: com.arthera.app)
  APNS_SANDBOX              — "true" for sandbox / TestFlight (default: true)
  APNS_AUTH_KEY_P8          — .p8 key content, or place file at ~/.aria/apns.p8
  WEBHOOK_TOKEN             — Static token for /api/v1/webhook/trigger
  ARIA_API_BASE             — FastAPI backend URL (default: http://localhost:8000)
  ARIA_CODE_DIR             — Path to aria-code directory
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Logging ───────────────────────────────────────────────────────────────────

_LOG_DIR = Path.home() / ".aria" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG if "--debug" in sys.argv else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_DIR / "daemon.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("aria.daemon")

# ── Paths & config ────────────────────────────────────────────────────────────

_ARIA_DIR  = Path.home() / ".aria"
_DB_PATH   = _ARIA_DIR / "daemon.db"
_PID_FILE  = _ARIA_DIR / "daemon.pid"
_ENV_FILE  = _ARIA_DIR / ".env"

_ARIA_CODE_DIR = Path(os.environ.get("ARIA_CODE_DIR", Path(__file__).parent))
if str(_ARIA_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_ARIA_CODE_DIR))


def _load_env() -> None:
    """Load ~/.aria/.env into os.environ if it exists."""
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


# ── DB bootstrap ───────────────────────────────────────────────────────────────

def _init_db() -> None:
    _ARIA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS device_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                platform TEXT DEFAULT 'ios',
                user_id TEXT,
                bundle_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                condition TEXT NOT NULL,
                value REAL NOT NULL,
                message TEXT,
                notify_push INTEGER DEFAULT 1,
                notify_telegram INTEGER DEFAULT 1,
                notify_email TEXT,
                once INTEGER DEFAULT 1,
                active INTEGER DEFAULT 1,
                trigger_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                triggered_at TEXT
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                name TEXT,
                cron_expr TEXT NOT NULL,
                command TEXT NOT NULL,
                symbols TEXT DEFAULT '[]',
                channels TEXT DEFAULT '["ios","telegram"]',
                language TEXT DEFAULT 'zh',
                user_id TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                last_run TEXT,
                next_run TEXT
            );
            CREATE TABLE IF NOT EXISTS webhook_jobs (
                id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                source TEXT DEFAULT 'external',
                status TEXT DEFAULT 'pending',
                result TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                done_at TEXT
            );
            CREATE TABLE IF NOT EXISTS push_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT,
                device_token TEXT,
                title TEXT,
                body TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()


_init_db()


# ── Price fetch (lightweight, no VPN proxy) ────────────────────────────────────

async def _fetch_price(symbol: str) -> tuple[Optional[float], Optional[float]]:
    """Fast price lookup. Returns (current_price, prev_close) for alert evaluation."""
    import math
    try:
        import yfinance as yf
        if symbol.isdigit() and len(symbol) == 6:
            yfn = symbol + (".SS" if symbol.startswith(("6", "5")) else ".SZ")
        else:
            yfn = symbol
        ticker = yf.Ticker(yfn)
        info = ticker.fast_info
        raw_price = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
        raw_prev  = getattr(info, "previous_close", None)
        # yfinance can return float('nan') — treat as None
        price      = float(raw_price)  if raw_price  is not None and not math.isnan(float(raw_price))  else None
        prev_close = float(raw_prev)   if raw_prev   is not None and not math.isnan(float(raw_prev))   else None
        return (price, prev_close)
    except Exception as exc:
        logger.debug("_fetch_price %s: %s", symbol, exc)
        return (None, None)


# ── Push notifications ────────────────────────────────────────────────────────

async def _push_alert(title: str, body: str, extra: Optional[dict] = None, alert_id: Optional[str] = None) -> None:
    """Try to push via APNs; log any failure but never raise."""
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "Arthera" / "apps" / "api" / "src"))
        from services.apns_service import push_to_all
        sent = await push_to_all(title, body, extra, alert_id)
        logger.info("APNs push: %d device(s) notified for alert_id=%s", sent, alert_id)
    except ImportError:
        logger.debug("apns_service not reachable — skipping push")


# ── Telegram push (one-shot, no polling) ─────────────────────────────────────

async def _telegram_push(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
    if not token or not allowed_raw:
        return
    chat_ids = [int(x.strip()) for x in allowed_raw.split(",") if x.strip().isdigit()]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            for cid in chat_ids:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": cid, "text": text, "parse_mode": "Markdown"},
                )
    except Exception as exc:
        logger.warning("_telegram_push failed: %s", exc)


# ── Feishu push (webhook card) ────────────────────────────────────────────────

async def _feishu_push(title: str, body: str) -> None:
    """POST an interactive card to a Feishu group webhook (FEISHU_WEBHOOK_URL)."""
    url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    if not url:
        return
    color = "red"   if any(w in title for w in ("预警", "Alert", "熔断", "ERROR")) else \
            "green" if any(w in title for w in ("晨报", "完成", "Brief"))         else "blue"
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title":    {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": body[:2000]}},
                {"tag": "hr"},
                {"tag": "note", "elements": [
                    {"tag": "plain_text",
                     "content": "Aria Daemon · " + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")},
                ]},
            ],
        },
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=card)
            if resp.status_code >= 400:
                logger.warning("_feishu_push HTTP %s: %s", resp.status_code, resp.text[:120])
    except Exception as exc:
        logger.warning("_feishu_push failed: %s", exc)


# ── Alert watchdog ────────────────────────────────────────────────────────────

async def _alert_watchdog() -> None:
    """Check all active alerts against live prices every 30 seconds."""
    logger.info("Alert watchdog started")
    while True:
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE active=1"
                ).fetchall()
            alerts = [dict(r) for r in rows]

            # Group by symbol to minimise API calls
            by_symbol: dict[str, list[dict]] = {}
            for a in alerts:
                by_symbol.setdefault(a["symbol"], []).append(a)

            for symbol, sym_alerts in by_symbol.items():
                price, prev_close = await _fetch_price(symbol)
                if price is None:
                    continue
                for alert in sym_alerts:
                    await _check_alert(alert, price, prev_close)

        except Exception as exc:
            logger.error("Alert watchdog error: %s", exc)

        await asyncio.sleep(30)


async def _check_alert(alert: dict, price: float, prev_close: Optional[float] = None) -> None:
    cond = alert["condition"]
    val  = float(alert["value"])

    # pct_change 条件: prev_close=None 时跳过而非永远不触发
    if cond in ("pct_change_above", "pct_change_below") and (not prev_close or prev_close <= 0):
        logger.debug("Alert %s: prev_close unavailable, skipping pct_change check", alert["id"])
        return

    pct_chg = ((price - prev_close) / prev_close * 100) if prev_close and prev_close > 0 else None
    fired = (
        (cond == "price_above"       and price > val) or
        (cond == "price_below"       and price < val) or
        (cond == "pct_change_above"  and pct_chg is not None and pct_chg > val) or
        (cond == "pct_change_below"  and pct_chg is not None and pct_chg < val)
    )
    if not fired:
        return

    symbol  = alert["symbol"]
    pct_str = f" ({pct_chg:+.2f}%)" if pct_chg is not None else ""
    message = alert.get("message") or f"{symbol} {cond.replace('_', ' ')} {val}"
    title   = "🔔 ARIA 价格预警"
    body    = f"{symbol} ¥{price:.2f}{pct_str} — {message}"

    logger.info("Alert triggered: %s | %s @ %.4f", alert["id"], symbol, price)

    # Update DB
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "UPDATE alerts SET trigger_count=trigger_count+1, triggered_at=datetime('now')"
            + (", active=0" if alert.get("once", 1) else "")
            + " WHERE id=?",
            (alert["id"],),
        )
        conn.commit()

    extra = {"symbol": symbol, "price": price, "signal": "ALERT"}

    if alert.get("notify_push", 1):
        await _push_alert(title, body, extra, alert["id"])

    if alert.get("notify_telegram", 1):
        await _telegram_push(f"*{title}*\n{body}")
    await _feishu_push(title, body)

    # 触发后自动运行轻量分析（technical + risk），结果推送给用户
    asyncio.create_task(_auto_analyze_alert(symbol, price, pct_str))


async def _auto_analyze_alert(symbol: str, price: float, pct_str: str) -> None:
    """Run a lightweight technical+risk analysis after an alert fires and push the result."""
    try:
        sys.path.insert(0, str(_ARIA_CODE_DIR))
        from agents.team import AgentTeam
        team = AgentTeam(agent_names=["technical", "risk"])
        result = await asyncio.wait_for(team.run(symbol), timeout=45.0)
        signal  = result.signal or "N/A"
        conf    = f"{result.confidence:.0%}" if result.confidence else "?"
        points  = "\n".join(f"  • {p}" for p in (result.key_points or [])[:3])
        body = (
            f"🤖 *{symbol}* 预警后快速分析\n"
            f"现价 {price:.4f}{pct_str}\n\n"
            f"Signal: *{signal}*  置信度: {conf}\n"
            f"{points}"
        )
        await _telegram_push(body)
        await _feishu_push(f"🤖 {symbol} 预警分析", body.replace("*", "**"))
    except asyncio.TimeoutError:
        logger.warning("_auto_analyze_alert %s: timeout", symbol)
    except Exception as exc:
        logger.debug("_auto_analyze_alert %s: %s", symbol, exc)


# ── Telegram command handler ─────────────────────────────────────────────────

async def _telegram_command(cmd: str, args: str, chat_id: int) -> str:
    """Route Telegram bot commands to Aria functions."""
    args = args.strip()
    logger.info("Telegram cmd=/%s args=%s chat=%d", cmd, args[:40], chat_id)

    if cmd == "help":
        return (
            "*Aria 命令列表*\n\n"
            "`/price SYMBOL` — 实时报价\n"
            "`/report SYMBOL` — 深度分析研报\n"
            "`/brief` — 今日晨报\n"
            "`/screen` — 热门 A 股筛选\n"
            "`/alert SYMBOL cond value` — 添加价格预警\n"
            "    条件: price\\_above / price\\_below / pct\\_change\\_above\n"
            "`/alerts` — 查看预警列表\n"
            "`/status` — Daemon 运行状态\n"
            "\n直接发文字也可对话 Aria。"
        )

    if cmd in ("price", "p"):
        symbol = args.upper() or "SPY"
        price, _ = await _fetch_price(symbol)
        if price:
            return f"*{symbol}* 当前价格: `¥{price:.4f}`" if len(symbol) == 6 and symbol.isdigit() else f"*{symbol}* ${price:.4f}"
        return f"⚠️ 无法获取 {symbol} 价格（市场可能已关闭）"

    if cmd == "report":
        symbol = args.upper()
        if not symbol:
            return "用法: `/report AAPL` 或 `/report 600519`"
        return await _run_report(symbol)

    if cmd in ("brief", "morning", "briefing"):
        return await _run_morning_brief()

    if cmd == "screen":
        return await _run_screener()

    if cmd == "alert":
        return await _handle_alert_add(args, chat_id)

    if cmd == "alerts":
        return _list_alerts()

    if cmd == "status":
        return _daemon_status()

    if cmd == "chat":
        # Natural language fallback
        return await _run_chat(args)

    return f"未知命令 `/{cmd}`。发送 `/help` 查看命令列表。"


async def _run_report(symbol: str) -> str:
    """Generate a quick text summary report."""
    try:
        price, _pc = await _fetch_price(symbol)
        price_str = f"¥{price:.2f}" if price and len(symbol) == 6 and symbol.isdigit() else (f"${price:.2f}" if price else "N/A")

        # Try to import aria-code market data for indicators
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "market_data_client",
                _ARIA_CODE_DIR / "market_data_client.py",
            )
            mdc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mdc)
            client = mdc.MarketDataClient()
            quote = await asyncio.wait_for(client.quote(symbol), timeout=8.0)
            name  = quote.get("name", symbol)
            chg   = quote.get("change_pct", 0)
            vol   = quote.get("volume", 0)
            chg_str = f"{chg:+.2f}%" if chg else ""
        except Exception:
            name, chg_str, vol = symbol, "", 0

        lines = [
            f"*{name}* `{symbol}` 快速分析",
            f"",
            f"💰 价格: `{price_str}` {chg_str}",
            f"📊 成交量: `{vol:,}`" if vol else "",
            f"",
            f"⚠️ 深度研报请在 Terminal 使用 `/report {symbol}` 命令。",
            f"此处为轻量版摘要。",
        ]
        return "\n".join(l for l in lines if l is not None)

    except Exception as exc:
        logger.error("_run_report %s: %s", symbol, exc)
        return f"⚠️ 分析 {symbol} 时出错: {exc}"


async def _run_morning_brief() -> str:
    """Quick morning market summary."""
    try:
        indices = {"标普500": "SPY", "纳指": "QQQ", "沪深300": "000300.SS"}
        lines = ["*🌅 Aria 晨报*\n"]
        for name, sym in indices.items():
            p, _pc = await _fetch_price(sym)
            lines.append(f"  {name}: `{'¥' if '.' in sym else '$'}{p:.2f}`" if p else f"  {name}: N/A")
        lines.append("\n_数据来自 yfinance，仅供参考_")
        return "\n".join(lines)
    except Exception as exc:
        return f"⚠️ 晨报生成失败: {exc}"


async def _run_screener() -> str:
    """Quick hot-stock screener using market_data_client if available."""
    try:
        spec = __import__("importlib.util").util.spec_from_file_location(
            "market_data_client", _ARIA_CODE_DIR / "market_data_client.py"
        )
        mdc = __import__("importlib.util").util.module_from_spec(spec)
        spec.loader.exec_module(mdc)
        client = mdc.MarketDataClient()
        result = await asyncio.wait_for(client.hot_ashare(limit=8), timeout=10.0)
        stocks = result if isinstance(result, list) else []
        if not stocks:
            return "⚠️ 暂时无法获取行情数据（市场未开市或数据源异常）"
        lines = ["*🔥 A股热门*\n"]
        for s in stocks[:8]:
            sym  = s.get("symbol", "")
            name = s.get("name", sym)
            p    = s.get("price", 0)
            chg  = s.get("change_pct", 0)
            lines.append(f"  `{sym}` {name}  ¥{p:.2f}  {chg:+.2f}%")
        return "\n".join(lines)
    except Exception as exc:
        return f"⚠️ 筛选器出错: {exc}"


async def _handle_alert_add(args: str, chat_id: int) -> str:
    """Parse and store an alert from Telegram. Format: SYMBOL cond value"""
    parts = args.split()
    if len(parts) < 3:
        return (
            "用法: `/alert SYMBOL condition value`\n"
            "示例: `/alert 600362 price_below 39.5`\n"
            "条件: `price_above` / `price_below`"
        )
    symbol, cond, val_str = parts[0].upper(), parts[1].lower(), parts[2]
    valid_conds = {"price_above", "price_below", "pct_change_above", "pct_change_below"}
    if cond not in valid_conds:
        return f"⚠️ 无效条件 `{cond}`。可用: {', '.join(valid_conds)}"
    try:
        val = float(val_str)
    except ValueError:
        return f"⚠️ 无效数值: `{val_str}`"

    import uuid
    alert_id = str(uuid.uuid4())[:8]
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO alerts (id, symbol, condition, value, message, notify_push, notify_telegram) "
            "VALUES (?,?,?,?,?,1,1)",
            (alert_id, symbol, cond, val, f"{symbol} {cond.replace('_',' ')} {val}"),
        )
        conn.commit()
    return f"✅ 预警已设置 `[{alert_id}]`\n{symbol} {cond.replace('_', ' ')} `{val}`"


def _list_alerts() -> str:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, symbol, condition, value, active, trigger_count FROM alerts ORDER BY created_at DESC LIMIT 15"
        ).fetchall()
    if not rows:
        return "📭 暂无预警设置"
    lines = ["*预警列表*\n"]
    for r in rows:
        status = "🟢" if r["active"] else "⚫"
        lines.append(f"{status} `{r['id']}` {r['symbol']} {r['condition'].replace('_',' ')} `{r['value']}` (触发{r['trigger_count']}次)")
    return "\n".join(lines)


def _daemon_status() -> str:
    pid = os.getpid()
    with sqlite3.connect(_DB_PATH) as conn:
        alert_count  = conn.execute("SELECT COUNT(*) FROM alerts WHERE active=1").fetchone()[0]
        sched_count  = conn.execute("SELECT COUNT(*) FROM schedules WHERE enabled=1").fetchone()[0]
        device_count = conn.execute("SELECT COUNT(*) FROM device_tokens").fetchone()[0]
        job_count    = conn.execute("SELECT COUNT(*) FROM webhook_jobs WHERE status='pending'").fetchone()[0]
    return (
        f"*🤖 Aria Daemon 状态*\n"
        f"  PID: `{pid}`\n"
        f"  活跃预警: `{alert_count}`\n"
        f"  定时任务: `{sched_count}`\n"
        f"  推送设备: `{device_count}`\n"
        f"  待处理 Webhook: `{job_count}`\n"
        f"  时间: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
    )


async def _run_chat(text: str) -> str:
    """Simple chat fallback using aria-code LLM if configured."""
    return f"💬 _收到你的消息: \"{text[:80]}\"_\n\n请在 Terminal 启动 `/aria` 获得完整对话体验。"


# ── Webhook job executor ─────────────────────────────────────────────────────

async def _webhook_executor() -> None:
    """Poll webhook_jobs table for pending jobs and execute them."""
    logger.info("Webhook executor started")
    while True:
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM webhook_jobs WHERE status='pending' ORDER BY created_at LIMIT 5"
                ).fetchall()
            for row in rows:
                asyncio.create_task(_execute_job(dict(row)))
        except Exception as exc:
            logger.error("Webhook executor error: %s", exc)
        await asyncio.sleep(2)


async def _execute_job(job: dict) -> None:
    job_id  = job["id"]
    command = job["command"]
    try:
        import json as _json
        payload = _json.loads(job.get("payload") or "{}")
    except Exception:
        payload = {}

    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "UPDATE webhook_jobs SET status='running', started_at=datetime('now') WHERE id=?",
            (job_id,),
        )
        conn.commit()

    try:
        if command.startswith("chat:"):
            result = await _run_chat(command[5:])
        elif command.startswith("report ") or command.startswith("/report "):
            sym = command.split()[-1].upper()
            result = await _run_report(sym)
        elif command in ("morning-brief", "/morning-brief", "brief"):
            result = await _run_morning_brief()
        elif command in ("screen", "/screen"):
            result = await _run_screener()
        else:
            raise ValueError(f"未知 Webhook 命令: {command!r}（支持: chat: / report / morning-brief / screen）")

        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "UPDATE webhook_jobs SET status='done', result=?, done_at=datetime('now') WHERE id=?",
                (result[:2000], job_id),
            )
            conn.commit()

        # If channels include telegram, send result
        channels = []
        try:
            import json as _j
            channels = _j.loads(payload.get("channels", "[]") or "[]")
        except Exception:
            pass
        if "telegram" in channels:
            await _telegram_push(result)
        if "feishu" in channels:
            await _feishu_push("⏰ Webhook 任务完成", result[:2000])

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc)
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "UPDATE webhook_jobs SET status='error', result=?, done_at=datetime('now') WHERE id=?",
                (str(exc)[:500], job_id),
            )
            conn.commit()


# ── APScheduler cron ─────────────────────────────────────────────────────────

def _start_scheduler() -> None:
    """Load schedules from DB and register with APScheduler."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM schedules WHERE enabled=1"
            ).fetchall()

        for row in rows:
            s = dict(row)
            try:
                trigger = CronTrigger.from_crontab(s["cron_expr"], timezone="Asia/Shanghai")
                scheduler.add_job(
                    _run_scheduled_job,
                    trigger=trigger,
                    args=[s],
                    id=s["id"],
                    name=s.get("name") or s["command"],
                    replace_existing=True,
                    misfire_grace_time=300,
                )
                logger.info("Scheduled: %s [%s]", s.get("name", s["id"]), s["cron_expr"])
            except Exception as exc:
                logger.error("Failed to schedule %s: %s", s["id"], exc)

        # Default morning brief if no schedule exists
        if not rows:
            scheduler.add_job(
                _run_morning_brief_and_push,
                CronTrigger.from_crontab("0 8 * * 1-5", timezone="Asia/Shanghai"),
                id="default_morning_brief",
                name="默认晨报",
                replace_existing=True,
            )
            logger.info("Registered default morning brief @ 08:00 weekdays")

        scheduler.start()
        logger.info("APScheduler started with %d job(s)", len(scheduler.get_jobs()))
        return scheduler
    except ImportError:
        logger.warning("APScheduler not installed — cron disabled. pip install apscheduler")
        return None


async def _run_scheduled_job(schedule: dict) -> None:
    logger.info("Running scheduled job: %s", schedule.get("name", schedule["id"]))
    import json as _j
    channels = _j.loads(schedule.get("channels") or '["ios","telegram"]')
    cmd = schedule["command"]
    result = ""
    try:
        if cmd == "morning-brief":
            result = await _run_morning_brief()
        elif cmd == "screen":
            result = await _run_screener()
        elif cmd.startswith("report "):
            result = await _run_report(cmd.split()[-1].upper())
        elif cmd.startswith("custom:"):
            result = f"Custom job: {cmd[7:]}"
        else:
            raise ValueError(f"未知定时命令: {cmd!r}（支持: morning-brief / screen / report <symbol> / custom:<payload>）")
    except Exception as exc:
        result = f"⚠️ 定时任务 [{cmd}] 失败: {exc}"

    if "telegram" in channels:
        await _telegram_push(f"⏰ *定时任务完成*\n{result}")
    if "feishu" in channels:
        await _feishu_push(f"⏰ 定时任务：{cmd}", result[:2000])
    if "ios" in channels:
        await _push_alert(
            "Aria 定时任务",
            result[:150],
            {"command": cmd},
        )

    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "UPDATE schedules SET last_run=datetime('now') WHERE id=?",
            (schedule["id"],),
        )
        conn.commit()


async def _run_morning_brief_and_push() -> None:
    brief = await _run_morning_brief()
    await _telegram_push(brief)
    await _feishu_push("📊 Aria 晨报", brief[:2000])
    await _push_alert("Aria 晨报", brief[:150])


# ── PID management ────────────────────────────────────────────────────────────

def _write_pid() -> None:
    _ARIA_DIR.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Install / uninstall as macOS LaunchAgent ──────────────────────────────────

def _install_launchagent() -> None:
    python  = sys.executable
    script  = str(Path(__file__).resolve())
    log_out = str(_LOG_DIR / "daemon.log")
    log_err = str(_LOG_DIR / "daemon.err")
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.aria.daemon.plist"

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.aria.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_out}</string>
    <key>StandardErrorPath</key>
    <string>{log_err}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{str(Path(python).parent)}</string>
        <key>ARIA_CODE_DIR</key>
        <string>{str(_ARIA_CODE_DIR)}</string>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
"""
    plist_path.write_text(plist)
    os.system(f"launchctl unload '{plist_path}' 2>/dev/null; launchctl load -w '{plist_path}'")
    print(f"✅ Aria Daemon installed as LaunchAgent: {plist_path}")
    print(f"   Logs: {log_out}")
    print(f"   To uninstall: python3 {script} --uninstall")


def _uninstall_launchagent() -> None:
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.aria.daemon.plist"
    if plist_path.exists():
        os.system(f"launchctl unload '{plist_path}' 2>/dev/null")
        plist_path.unlink()
        print("✅ Aria Daemon LaunchAgent removed")
    else:
        print("ℹ️  LaunchAgent not installed")
    _remove_pid()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("═" * 55)
    logger.info(" Aria Daemon starting  PID=%d", os.getpid())
    logger.info("═" * 55)
    _write_pid()

    loop = asyncio.get_event_loop()
    # Graceful shutdown on SIGINT / SIGTERM
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))

    tasks = [
        asyncio.create_task(_alert_watchdog(), name="alert_watchdog"),
        asyncio.create_task(_webhook_executor(), name="webhook_executor"),
    ]

    scheduler = _start_scheduler()

    # Start Telegram bot if token configured
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if tg_token:
        from aria_telegram_bot import TelegramBot
        allowed_raw = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
        allowed_ids = set(
            int(x.strip()) for x in allowed_raw.split(",") if x.strip().isdigit()
        )
        bot = TelegramBot(token=tg_token, allowed_chat_ids=allowed_ids)
        me = await bot.get_me()
        if me:
            logger.info("Telegram bot: @%s", me.get("username", "?"))
            await _telegram_push("🤖 *Aria Daemon 已启动*\n发送 `/help` 查看命令。")
        tasks.append(asyncio.create_task(bot.start(_telegram_command), name="telegram_bot"))
    else:
        logger.info("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")

    # Start Feishu relay client if relay mode configured
    relay_url       = os.environ.get("ARIA_RELAY_URL", "")
    relay_client_id = os.environ.get("ARIA_RELAY_CLIENT_ID", "")
    relay_mode      = os.environ.get("ARIA_RELAY_MODE", "")
    if relay_url and relay_client_id and relay_mode == "relay":
        try:
            from aria_relay_client import _connect_and_serve as _relay_serve
            tasks.append(asyncio.create_task(_relay_serve(), name="feishu_relay"))
            logger.info("Feishu relay client started → %s (client=%s)", relay_url, relay_client_id)
        except ImportError:
            logger.warning("aria_relay_client.py not found — Feishu relay disabled")

    logger.info("All workers started. Daemon running.")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        _remove_pid()
        if scheduler:
            scheduler.shutdown(wait=False)
        logger.info("Aria Daemon stopped")


async def _shutdown() -> None:
    logger.info("Shutdown signal received")
    for task in asyncio.all_tasks():
        if task.get_name() not in ("main_task",):
            task.cancel()


if __name__ == "__main__":
    if "--install" in sys.argv:
        _install_launchagent()
    elif "--uninstall" in sys.argv:
        _uninstall_launchagent()
    elif "--status" in sys.argv:
        pid_file = _ARIA_DIR / "daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                print(f"✅ Aria Daemon is running (PID {pid})")
            except ProcessLookupError:
                print("⚠️  PID file exists but process not running")
        else:
            print("⚫ Aria Daemon is not running")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
