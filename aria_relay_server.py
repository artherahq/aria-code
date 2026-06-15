#!/usr/bin/env python3
"""
aria_relay_server.py — Aria 中继服务器
=======================================
你（产品方）部署一次，所有用户共用。

架构:
  用户飞书消息
    → 飞书云 → POST /feishu/event
    → relay_server 查 feishu_user_id → 对应 WebSocket
    → relay_server 转发 payload 到用户本机
    → aria_relay_client.dispatch_event()
    → Aria LLM 回复
    → relay_server 收到 response
    → relay_server 调飞书 reply_message API
    → 飞书消息卡片展示给用户

WebSocket 注册流程:
  1. 用户本机 aria_relay_client 连接 wss://relay.yourdomain.com
  2. 发送: {"type": "register", "client_id": "aria-xxxxxxxxxxxx"}
  3. 用户在飞书向 Aria Bot 发: /bind ARIA-BIND-ARIA-XXXXXXXXXXXX
  4. 服务器记录: feishu_user_id → client_id
  5. 后续消息经 WebSocket 透传

依赖:
  pip install fastapi uvicorn websockets httpx

启动:
  python3 aria_relay_server.py
  # 或
  uvicorn aria_relay_server:app --host 0.0.0.0 --port 8765

所需环境变量:
  FEISHU_APP_ID         飞书应用 App ID
  FEISHU_APP_SECRET     飞书应用 App Secret
  RELAY_SECRET          WebSocket 注册鉴权（可选，留空则不鉴权）
  DB_PATH               SQLite 路径（默认 ./relay.db）
  MESSAGE_TIMEOUT       等待用户本机回复的超时秒数（默认 90）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

logger = logging.getLogger("aria.relay_server")

# ── Config ────────────────────────────────────────────────────────────────────

_FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "")
_FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
_RELAY_SECRET      = os.environ.get("RELAY_SECRET", "")
_DB_PATH           = os.environ.get("DB_PATH", "./relay.db")
_MSG_TIMEOUT       = int(os.environ.get("MESSAGE_TIMEOUT", "90"))
_FEISHU_API        = "https://open.feishu.cn/open-apis"


# ── SQLite store ──────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bindings (
            feishu_user_id TEXT PRIMARY KEY,
            client_id      TEXT NOT NULL,
            bound_at       REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_binds (
            client_id  TEXT PRIMARY KEY,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


_db_conn: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = _db()
    return _db_conn


def _lookup_client(feishu_user_id: str) -> Optional[str]:
    row = get_db().execute(
        "SELECT client_id FROM bindings WHERE feishu_user_id = ?",
        (feishu_user_id,),
    ).fetchone()
    return row["client_id"] if row else None


def _bind(feishu_user_id: str, client_id: str) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO bindings VALUES (?, ?, ?)",
        (feishu_user_id, client_id, time.time()),
    )
    get_db().execute(
        "DELETE FROM pending_binds WHERE client_id = ?", (client_id,)
    )
    get_db().commit()


def _register_pending(client_id: str) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO pending_binds VALUES (?, ?)",
        (client_id, time.time()),
    )
    get_db().commit()


def _is_valid_client_id(client_id: str) -> bool:
    row = get_db().execute(
        "SELECT client_id FROM pending_binds WHERE client_id = ?", (client_id,)
    ).fetchone()
    return row is not None


# ── WebSocket connection registry ─────────────────────────────────────────────

_connections: dict[str, WebSocket] = {}   # client_id → WebSocket
_pending_responses: dict[str, asyncio.Future] = {}   # request_id → Future


# ── Feishu API helpers ────────────────────────────────────────────────────────

_feishu_token_cache: dict[str, Any] = {}


async def _get_tenant_token() -> str:
    now = time.time()
    if _feishu_token_cache.get("expires_at", 0) > now + 60:
        return _feishu_token_cache["token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_FEISHU_API}/auth/v3/tenant_access_token/internal",
            json={"app_id": _FEISHU_APP_ID, "app_secret": _FEISHU_APP_SECRET},
            timeout=10,
        )
        data = resp.json()

    token = data.get("tenant_access_token", "")
    expire = int(data.get("expire", 7200))
    _feishu_token_cache.update({"token": token, "expires_at": now + expire})
    return token


async def _reply_feishu(message_id: str, content: str, color: str = "blue") -> None:
    token = await _get_tenant_token()
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "Aria"},
                "template": color,
            },
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
        },
    }
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{_FEISHU_API}/im/v1/messages/{message_id}/reply",
            headers={"Authorization": f"Bearer {token}"},
            json=card,
            timeout=15,
        )


async def _send_feishu_text(open_id: str, text: str) -> None:
    token = await _get_tenant_token()
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{_FEISHU_API}/im/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"receive_id_type": "open_id"},
            json={
                "receive_id": open_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
            timeout=15,
        )


# ── Route message to local aria instance ─────────────────────────────────────

async def _route_to_local(feishu_user_id: str, payload: dict) -> Optional[Any]:
    """Forward Feishu event to the user's connected local aria instance."""
    client_id = _lookup_client(feishu_user_id)
    if not client_id:
        return None

    ws = _connections.get(client_id)
    if not ws:
        return None

    req_id = f"req_{uuid.uuid4().hex[:10]}"
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_responses[req_id] = future

    try:
        await ws.send_text(json.dumps({
            "type": "message",
            "id": req_id,
            "payload": payload,
        }))
        result = await asyncio.wait_for(future, timeout=_MSG_TIMEOUT)
        return result
    except asyncio.TimeoutError:
        logger.warning("Timeout waiting for response from client_id=%s", client_id)
        return {"error": "Aria 本机响应超时，请检查 aria_relay_client 是否在线"}
    finally:
        _pending_responses.pop(req_id, None)


# ── FastAPI app ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [relay] %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info("Aria Relay Server started  db=%s", _DB_PATH)
    get_db()  # init tables
    yield
    logger.info("Relay Server shutting down")


app = FastAPI(title="Aria Relay Server", lifespan=lifespan)


# ── WebSocket endpoint (users' local machines) ────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id: Optional[str] = None

    try:
        # First message must be register
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        msg = json.loads(raw)

        if msg.get("type") != "register":
            await websocket.send_text(json.dumps({"ok": False, "reason": "first message must be register"}))
            return

        client_id = msg.get("client_id", "")
        if not client_id:
            await websocket.send_text(json.dumps({"ok": False, "reason": "client_id required"}))
            return

        # Validate RELAY_SECRET if configured
        if _RELAY_SECRET and msg.get("secret") != _RELAY_SECRET:
            await websocket.send_text(json.dumps({"ok": False, "reason": "invalid secret"}))
            return

        # Register in-memory + mark as pending bind (if first time)
        _connections[client_id] = websocket
        if _lookup_client.__module__:  # always true; used as noop to be explicit
            _register_pending(client_id)

        await websocket.send_text(json.dumps({"ok": True, "client_id": client_id}))
        logger.info("Client connected: %s", client_id)

        # Message loop
        async for raw in websocket.iter_text():
            try:
                response_msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if response_msg.get("type") == "pong":
                continue

            if response_msg.get("type") == "response":
                req_id = response_msg.get("id", "")
                future = _pending_responses.get(req_id)
                if future and not future.done():
                    future.set_result(response_msg.get("result"))

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        logger.warning("Register timeout for new connection")
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
    finally:
        if client_id:
            _connections.pop(client_id, None)
            logger.info("Client disconnected: %s", client_id)


# ── Feishu event endpoint ─────────────────────────────────────────────────────

@app.post("/feishu/event")
async def feishu_event(request: Request):
    """Feishu Developer Console → Event Subscription → Request URL: /feishu/event"""
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # URL verification challenge
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}

    # Extract sender + message_id
    event = payload.get("event", {})
    message = event.get("message", {})
    sender  = event.get("sender", {})
    feishu_user_id = sender.get("sender_id", {}).get("open_id", "")
    message_id     = message.get("message_id", "")

    if not feishu_user_id:
        return {"code": 0}

    # Handle /bind command
    msg_type = message.get("message_type", "")
    if msg_type == "text":
        try:
            text_content = json.loads(message.get("content", "{}")).get("text", "").strip()
        except Exception:
            text_content = ""

        if text_content.upper().startswith("/BIND ") or text_content.upper().startswith("ARIA-BIND-"):
            raw_code = text_content.upper().replace("/BIND ", "").strip()
            # Normalize: "ARIA-BIND-ARIA-XXXX" → "aria-xxxx"
            client_id_upper = raw_code.replace("ARIA-BIND-", "").replace("ARIA-", "aria-").lower()
            _bind(feishu_user_id, client_id_upper)
            await _send_feishu_text(
                feishu_user_id,
                f"✅ 绑定成功！你的 Aria 实例已连接。\n"
                f"Client ID: {client_id_upper}\n"
                f"现在可以直接发消息与你的 Aria 交互了。"
            )
            return {"code": 0}

    # Route to local aria instance
    result = await _route_to_local(feishu_user_id, payload)

    if result is None:
        # No binding found or client offline
        if not _lookup_client(feishu_user_id):
            await _send_feishu_text(
                feishu_user_id,
                "👋 你好！要开始使用 Aria，请：\n"
                "1. 在你的电脑上安装 Aria Code\n"
                "2. 运行 `python3 setup_wizard.py` 完成配置\n"
                "3. 发送绑定码绑定你的账户"
            )
        else:
            await _reply_feishu(
                message_id,
                "⚠️ Aria 本机未连接。请确保你的电脑上 `aria_relay_client.py` 正在运行。",
                color="yellow",
            )

    return {"code": 0}


# ── Status endpoint ───────────────────────────────────────────────────────────

@app.get("/status")
async def status():
    return {
        "connected_clients": len(_connections),
        "client_ids": list(_connections.keys()),
        "total_bindings": get_db().execute("SELECT COUNT(*) FROM bindings").fetchone()[0],
        "feishu_app_configured": bool(_FEISHU_APP_ID),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "aria_relay_server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8765")),
        reload=False,
        log_level="info",
    )
