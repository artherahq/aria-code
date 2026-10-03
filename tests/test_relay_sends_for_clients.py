"""Relay mode can answer — and the relay's bot identity is not up for grabs.

Before 2026-10-03 relay mode dropped every answer: the relay forwarded each
event, and the client's bot tried to reply with app credentials that relay
mode deliberately keeps off the user's machine. Now the client hands its
message back over the WebSocket and the relay sends it. That makes the
relay's bot something any connected client could try to use, so most of
these tests are about what it refuses.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from unittest import mock

import pytest

if os.environ.get("GITHUB_ACTIONS"):
    # These tests used to be skipped in CI without anyone noticing: no
    # workflow installed fastapi, so importorskip skipped every relay test,
    # including the account-hijack regressions. In CI that is now a failure.
    import fastapi  # noqa: F401

fastapi_testclient = pytest.importorskip("fastapi.testclient", reason="relay server dependency")
TestClient = fastapi_testclient.TestClient


@pytest.fixture
def relay(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "relay.db"))
    for key in ("FEISHU_ENCRYPT_KEY", "RELAY_ALLOW_UNVERIFIED_EVENTS", "RELAY_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "tok")
    import aria_relay_server
    module = importlib.reload(aria_relay_server)

    async def token():
        return "tenant-token"

    monkeypatch.setattr(module, "_get_tenant_token", token)
    return module


def _reply(target="om_1", **kw):
    return {"op": "reply", "target": target, "msg_type": "text", "content": "{}", **kw}


def _send(chat="oc_acme", **kw):
    return {"op": "send", "target": chat, "msg_type": "interactive", "content": "{}", **kw}


class TestWhatAClientMaySend:
    def test_a_reply_to_a_message_forwarded_to_it(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        assert relay._may_send("aria-a", _reply()) == ""

    def test_not_a_reply_to_someone_elses_message(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        assert relay._may_send("aria-b", _reply())
        assert relay._may_send("aria-a", _reply("om_never_forwarded"))

    def test_not_a_reply_after_the_window(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        relay._forwarded["om_1"] = ("aria-a", 0.0)
        assert relay._may_send("aria-a", _reply())

    def test_a_post_only_to_chats_its_user_has_spoken_in(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        assert relay._may_send("aria-a", _send("oc_acme")) == ""
        assert relay._may_send("aria-a", _send("oc_someone_elses_group"))
        assert relay._may_send("aria-b", _send("oc_acme"))

    def test_not_to_a_person_directly(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        assert relay._may_send("aria-a", _send("oc_acme", receive_id_type="open_id"))

    def test_only_text_and_cards_of_reasonable_size(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        assert relay._may_send("aria-a", _reply(msg_type="image"))
        assert relay._may_send("aria-a", _reply(content="x" * 30_001))

    def test_rate_limited(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        results = [relay._may_send("aria-a", _reply()) for _ in range(relay._SEND_LIMIT + 1)]
        assert results[:-1] == [""] * relay._SEND_LIMIT
        assert results[-1] == "rate limit"


class _Post:
    def __init__(self, response, calls):
        self.response, self.calls = response, calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs["json"]))
        return mock.Mock(json=lambda: self.response)


class TestSending:
    def _send_for(self, relay, request, response=None):
        calls = []
        response = response or {"code": 0, "data": {"message_id": "om_card"}}
        with mock.patch.object(relay.httpx, "AsyncClient", lambda: _Post(response, calls)):
            result = asyncio.run(relay._send_for_client("aria-a", request))
        return result, calls

    def test_a_refused_send_never_reaches_feishu(self, relay):
        result, calls = self._send_for(relay, _reply("om_unknown"))
        assert result["code"] == -1 and calls == []

    def test_an_allowed_reply_is_sent_as_a_reply(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        result, calls = self._send_for(relay, _reply())
        assert result["code"] == 0
        assert calls[0][0].endswith("/im/v1/messages/om_1/reply")

    def test_a_card_it_sends_is_remembered_as_its_card(self, relay):
        relay._remember_forward("aria-a", "om_1", "oc_acme")
        self._send_for(relay, _send("oc_acme"))
        assert relay._card_origin("om_card") == "aria-a"

    def test_routing_a_message_records_where_it_came_from(self, relay):
        relay._bind("ou_alice", "aria-a")

        async def fake_route(client_id, payload, timeout=None):
            return {"code": 0}

        with mock.patch.object(relay, "_route_to_client", fake_route):
            asyncio.run(relay._route_to_local("ou_alice", {"event": {"message": {
                "message_id": "om_9", "chat_id": "oc_acme"}}}))
        assert relay._may_send("aria-a", _reply("om_9")) == ""
        assert relay._may_send("aria-a", _send("oc_acme")) == ""


def _card_press(message_id="om_card"):
    return {"token": "tok", "header": {"event_type": "card.action.trigger"}, "event": {
        "operator": {"open_id": "ou_boss"},
        "action": {"value": {"aria_approval": "abc", "decision": "approve"}},
        "context": {"open_message_id": message_id, "open_chat_id": "oc_acme"}}}


class TestCardPresses:
    def test_a_press_goes_to_the_client_that_posted_the_card(self, relay):
        relay.get_db().execute("INSERT INTO card_origins VALUES ('om_card', 'aria-a', 0)")
        seen = {}

        async def fake_route(client_id, payload, timeout=None):
            seen.update(client=client_id, timeout=timeout)
            return {"toast": {"type": "success", "content": "已批准并执行。"}}

        with mock.patch.object(relay, "_route_to_client", fake_route):
            response = TestClient(relay.app).post("/feishu/event", json=_card_press())
        assert response.json()["toast"]["content"] == "已批准并执行。"
        assert seen["client"] == "aria-a"
        assert seen["timeout"] < 3, "Feishu waits at most 3 s for a card callback"

    def test_a_card_the_relay_did_not_post_is_refused(self, relay):
        response = TestClient(relay.app).post("/feishu/event", json=_card_press("om_other"))
        assert response.json()["toast"]["type"] == "error"

    def test_an_unanswered_press_says_so(self, relay):
        relay.get_db().execute("INSERT INTO card_origins VALUES ('om_card', 'aria-a', 0)")

        async def offline(client_id, payload, timeout=None):
            return None

        with mock.patch.object(relay, "_route_to_client", offline):
            response = TestClient(relay.app).post("/feishu/event", json=_card_press())
        assert response.json()["toast"]["type"] == "error"

    def test_a_forged_press_is_rejected_before_routing(self, relay):
        forged = {k: v for k, v in _card_press().items() if k != "token"}
        assert TestClient(relay.app).post("/feishu/event", json=forged).status_code == 401


class TestOverTheWebSocket:
    def test_a_refused_send_comes_back_as_a_send_result(self, relay):
        with TestClient(relay.app).websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "register", "client_id": "aria-a"}))
            assert json.loads(ws.receive_text())["ok"]
            ws.send_text(json.dumps({"type": "send", "id": "s1", **_send("oc_not_mine")}))
            answer = json.loads(ws.receive_text())
        assert answer["type"] == "send_result" and answer["id"] == "s1"
        assert answer["result"]["code"] == -1


# ── The client side ──────────────────────────────────────────────────────────

import aria_code.aria_feishu_bot as bot  # noqa: E402
from aria_code.clients import aria_relay_client as relay_client  # noqa: E402


@pytest.fixture
def relay_mode(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    sent = []

    async def relay_send(request):
        sent.append(request)
        return {"code": 0, "data": {"message_id": "om_new"}}

    bot.set_relay_sender(relay_send)
    yield sent
    bot.set_relay_sender(None)


class TestTheBotInRelayMode:
    def test_replies_go_through_the_relay(self, relay_mode):
        asyncio.run(bot.reply_text("om_1", "hello"))
        assert relay_mode == [{"op": "reply", "target": "om_1", "msg_type": "text",
                               "content": json.dumps({"text": "hello"}), "receive_id_type": "chat_id"}]

    def test_proactive_cards_go_through_the_relay(self, relay_mode):
        assert asyncio.run(bot.send_card_to_chat("oc_acme", "t", "b")) is True
        assert relay_mode[0]["op"] == "send" and relay_mode[0]["target"] == "oc_acme"
        assert bot.can_send()

    def test_with_app_credentials_it_sends_directly(self, relay_mode, monkeypatch):
        posts = []

        async def token():
            return "t"

        async def post(url, token, payload):
            posts.append(url)
            return {"code": 0}

        monkeypatch.setattr(bot, "_get_access_token", token)
        monkeypatch.setattr(bot, "_feishu_post", post)
        asyncio.run(bot.reply_text("om_1", "hello"))
        assert posts and relay_mode == []

    def test_without_either_it_cannot_send_and_says_so(self, monkeypatch):
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        bot.set_relay_sender(None)
        assert not bot.can_send()
        assert asyncio.run(bot.send_card_to_chat("oc_acme", "t", "b")) is False

    def test_an_attachment_is_declined_with_an_explanation(self, relay_mode, monkeypatch, tmp_path):
        monkeypatch.setenv("ARIA_CONVERSATIONS_DB", str(tmp_path / "c.db"))
        event = {"header": {"event_type": "im.message.receive_v1"}, "event": {
            "sender": {"sender_id": {"open_id": "ou_alice"}},
            "message": {"message_id": "om_1", "chat_id": "oc_dm", "chat_type": "p2p",
                        "message_type": "image", "content": "{}"}}}
        asyncio.run(bot.dispatch_event(event, authorized_by_binding=True))
        assert "不支持" in json.loads(relay_mode[0]["content"])["text"]


class TestRelayClientSender:
    def test_a_send_waits_for_the_relays_confirmation(self):
        frames = []

        class FakeSocket:
            async def send(self, raw):
                frames.append(json.loads(raw))
                relay_client._settle_send({"id": frames[-1]["id"], "result": {"code": 0}})

        result = asyncio.run(relay_client._relay_sender(FakeSocket())(_reply()))
        assert result == {"code": 0}
        assert frames[0]["type"] == "send" and frames[0]["target"] == "om_1"
        assert relay_client._pending_sends == {}
