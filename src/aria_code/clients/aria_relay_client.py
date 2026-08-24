#!/usr/bin/env python3
"""
aria_relay_client.py — 连接 Aria 中继服务器的 WebSocket 客户端
================================================================
本机运行，把中继服务器转发来的飞书消息交给 aria_feishu_bot 处理，
并把 LLM 回复返回给中继服务器，再由服务器推送到飞书。

启动方式:
  python3 aria_relay_client.py              # 单次连接（断线自动重连）
  python3 aria_relay_client.py --once       # 调试：收到第一条消息后退出

所需环境变量（读取 ~/.aria/.env）:
  ARIA_RELAY_URL        wss://relay.aria.ai（或自建服务器地址）
  ARIA_RELAY_CLIENT_ID  setup_wizard 生成的 12 位 hex id
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("aria.relay_client")

# ── 加载 ~/.aria/.env ──────────────────────────────────────────────────────

def _load_env() -> None:
    env_file = Path.home() / ".aria" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                if k not in os.environ:
                    os.environ[k] = v.strip()


_load_env()

_RELAY_URL = os.environ.get("ARIA_RELAY_URL", "wss://relay.aria.ai")
_CLIENT_ID = os.environ.get("ARIA_RELAY_CLIENT_ID", "")
_RECONNECT_DELAY_MAX = 60   # seconds
_RECONNECT_DELAY_BASE = 3


# ── 本地 aria_feishu_bot import ───────────────────────────────────────────────

def _get_feishu_bot():
    aria_dir = Path(__file__).parent
    if str(aria_dir) not in sys.path:
        sys.path.insert(0, str(aria_dir))
    try:
        import aria_feishu_bot
        return aria_feishu_bot
    except ImportError as e:
        logger.warning("aria_feishu_bot not importable: %s", e)
        return None


# ── Message handler ───────────────────────────────────────────────────────────

async def _handle_message(raw_msg: dict, ws) -> None:
    """
    Server sends:
      {"type": "message", "id": "req_xxx", "payload": <feishu_event_dict>}

    We reply:
      {"type": "response", "id": "req_xxx", "result": <any>}
    """
    req_id  = raw_msg.get("id", "")
    payload = raw_msg.get("payload", {})

    bot = _get_feishu_bot()
    if bot is None:
        result = {"error": "aria_feishu_bot unavailable"}
    else:
        try:
            result = await bot.dispatch_event(payload)
        except Exception as e:
            logger.exception("dispatch_event error")
            result = {"error": str(e)[:300]}

    reply = json.dumps({"type": "response", "id": req_id, "result": result})
    await ws.send(reply)


# ── Main loop ─────────────────────────────────────────────────────────────────

async def _connect_and_serve(once: bool = False) -> None:
    try:
        import websockets  # type: ignore
    except ImportError:
        logger.error("websockets package not installed — run: pip install websockets")
        sys.exit(1)

    if not _CLIENT_ID:
        logger.error(
            "ARIA_RELAY_CLIENT_ID is not set. "
            "Run setup_wizard.py to generate your client ID."
        )
        sys.exit(1)

    delay = _RECONNECT_DELAY_BASE
    while True:
        try:
            logger.info("Connecting to %s (client_id=%s)", _RELAY_URL, _CLIENT_ID)
            async with websockets.connect(
                _RELAY_URL,
                ping_interval=30,
                ping_timeout=10,
                open_timeout=15,
            ) as ws:
                # Register with server
                await ws.send(json.dumps({
                    "type": "register",
                    "client_id": _CLIENT_ID,
                }))
                ack_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                ack = json.loads(ack_raw)
                if not ack.get("ok"):
                    logger.error("Registration rejected: %s", ack.get("reason", "unknown"))
                    await asyncio.sleep(delay)
                    continue

                logger.info("Registered. Waiting for messages…")
                delay = _RECONNECT_DELAY_BASE  # reset on success

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON from relay: %r", raw[:100])
                        continue

                    if msg.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                        continue

                    if msg.get("type") == "message":
                        asyncio.create_task(_handle_message(msg, ws))

                    if once:
                        return

        except (OSError, ConnectionRefusedError) as e:
            logger.warning("Connection failed: %s — retry in %ds", e, delay)
        except asyncio.CancelledError:
            logger.info("Relay client cancelled")
            return
        except Exception as e:
            logger.warning("Relay error: %s — retry in %ds", e, delay)

        await asyncio.sleep(delay)
        delay = min(delay * 2, _RECONNECT_DELAY_MAX)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Aria 中继客户端")
    parser.add_argument("--once", action="store_true", help="接收一条消息后退出（调试用）")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [aria-relay] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(_connect_and_serve(once=args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
