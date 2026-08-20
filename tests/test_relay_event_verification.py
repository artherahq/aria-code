"""relay 的 /feishu/event 必须校验请求来源，/status 不得公开 client_ids。

2026-08-19 修复一条完整的未授权账户劫持链——修复前四步即可劫持任意在线用户：

  1. GET  /status         → 拿到全部在线 client_ids（当时直接列出）
  2. POST /feishu/event   → 伪造事件（当时零校验），sender.open_id 填攻击者的，
                            正文填 "/bind ARIA-BIND-<受害者 client_id>"
  3. 服务端执行 _bind(攻击者, 受害者client_id) → 绑定被改写
  4. 攻击者此后发的消息被转发到受害者本机执行，结果回传给攻击者

RELAY_SECRET 当时只校验 WebSocket 注册侧，HTTP 侧完全没有防护。
这些用例把攻击链的每一环都钉住，防止回归。
"""

from __future__ import annotations

import hashlib
import importlib
import json

import pytest

fastapi_testclient = pytest.importorskip(
    "fastapi.testclient", reason="需要 fastapi（relay 服务端依赖）"
)
TestClient = fastapi_testclient.TestClient


def _load_relay(monkeypatch, tmp_path, **env):
    """按给定环境变量重新加载 relay 模块（配置在 import 期读取）。"""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "relay.db"))
    for key in ("FEISHU_ENCRYPT_KEY", "FEISHU_VERIFICATION_TOKEN",
                "RELAY_ALLOW_UNVERIFIED_EVENTS", "RELAY_SECRET"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import aria_relay_server
    return importlib.reload(aria_relay_server)


def _forged_bind_event(victim_client_id: str) -> dict:
    """攻击者伪造的绑定事件：把自己的 open_id 绑到受害者的 client_id 上。"""
    return {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_attacker"}},
            "message": {
                "message_id": "om_forged",
                "message_type": "text",
                "content": json.dumps({"text": f"/bind ARIA-BIND-{victim_client_id}"}),
            },
        }
    }


def test_unverified_event_is_rejected_by_default(monkeypatch, tmp_path):
    """没配任何校验手段时默认拒绝——下游是用户本机，不能默认放行。"""
    relay = _load_relay(monkeypatch, tmp_path)
    client = TestClient(relay.app)
    resp = client.post("/feishu/event", json=_forged_bind_event("aria-victim0001"))
    assert resp.status_code == 401


def test_forged_bind_cannot_hijack_a_binding(monkeypatch, tmp_path):
    """攻击链第 2-3 步：伪造事件不得改写绑定关系。"""
    relay = _load_relay(monkeypatch, tmp_path, FEISHU_VERIFICATION_TOKEN="tok_secret")
    client = TestClient(relay.app)

    resp = client.post("/feishu/event", json=_forged_bind_event("aria-victim0001"))
    assert resp.status_code == 401

    row = relay.get_db().execute(
        "SELECT COUNT(*) FROM bindings WHERE feishu_user_id = ?", ("ou_attacker",)
    ).fetchone()
    assert row[0] == 0, "伪造事件竟然写入了绑定关系——劫持链未被阻断"


def test_wrong_verification_token_is_rejected(monkeypatch, tmp_path):
    relay = _load_relay(monkeypatch, tmp_path, FEISHU_VERIFICATION_TOKEN="tok_secret")
    client = TestClient(relay.app)
    payload = _forged_bind_event("aria-victim0001") | {"token": "tok_wrong"}
    assert client.post("/feishu/event", json=payload).status_code == 401


def test_correct_verification_token_passes(monkeypatch, tmp_path):
    """正向用例：配置正确 token 的真实事件必须能通过，否则等于把功能关死。"""
    relay = _load_relay(monkeypatch, tmp_path, FEISHU_VERIFICATION_TOKEN="tok_secret")
    client = TestClient(relay.app)
    resp = client.post("/feishu/event", json={"token": "tok_secret", "challenge": "abc123"})
    assert resp.status_code == 200
    assert resp.json()["challenge"] == "abc123"


def test_encrypt_key_signature_is_enforced(monkeypatch, tmp_path):
    relay = _load_relay(monkeypatch, tmp_path, FEISHU_ENCRYPT_KEY="k_secret")
    client = TestClient(relay.app)
    body = json.dumps({"challenge": "abc123"}).encode()

    # 错误签名 → 拒绝
    bad = client.post("/feishu/event", content=body, headers={
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": "1", "X-Lark-Request-Nonce": "n",
        "X-Lark-Signature": "deadbeef",
    })
    assert bad.status_code == 401

    # 正确签名 → 通过
    sig = hashlib.sha256(("1" + "n" + "k_secret").encode() + body).hexdigest()
    good = client.post("/feishu/event", content=body, headers={
        "Content-Type": "application/json",
        "X-Lark-Request-Timestamp": "1", "X-Lark-Request-Nonce": "n",
        "X-Lark-Signature": sig,
    })
    assert good.status_code == 200


def test_status_does_not_leak_client_ids(monkeypatch, tmp_path):
    """攻击链第 1 步：client_id 就是 /bind 的凭证，不能对匿名请求公开。"""
    relay = _load_relay(monkeypatch, tmp_path, RELAY_SECRET="s_secret")
    client = TestClient(relay.app)

    anon = client.get("/status").json()
    assert "client_ids" not in anon, "/status 仍在向匿名请求泄露 client_ids"
    assert "connected_clients" in anon

    authed = client.get("/status", headers={"X-Relay-Secret": "s_secret"}).json()
    assert "client_ids" in authed, "带正确 secret 时应能看到明细"
