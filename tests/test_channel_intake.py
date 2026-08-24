"""Tests for apps.channels.intake — the daemon-facing half of the channels
contract: open-mode intake policy (loopback-only when unauthenticated) and
gateway submission of channel tasks (tool-less, injectable runner)."""

import asyncio
from types import SimpleNamespace

import pytest

from aria_code.apps.channels.intake import analyze_alert_via_gateway, should_refuse_open_intake


class TestOpenIntakePolicy:
    def test_loopback_allowed_when_open(self):
        for host in ("127.0.0.1", "::1", "localhost", "[::1]"):
            assert not should_refuse_open_intake(
                host, token_configured=False, secret_configured=False
            )

    def test_remote_refused_when_open(self):
        assert should_refuse_open_intake(
            "192.168.1.50", token_configured=False, secret_configured=False
        )
        assert should_refuse_open_intake(
            "203.0.113.9", token_configured=False, secret_configured=False
        )

    def test_missing_host_refused_when_open(self):
        # Can't prove loopback → fail closed.
        assert should_refuse_open_intake(None, token_configured=False, secret_configured=False)
        assert should_refuse_open_intake("", token_configured=False, secret_configured=False)

    def test_any_credential_admits_remote(self):
        assert not should_refuse_open_intake(
            "203.0.113.9", token_configured=True, secret_configured=False
        )
        assert not should_refuse_open_intake(
            "203.0.113.9", token_configured=False, secret_configured=True
        )


class TestGatewaySubmission:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    @pytest.mark.asyncio
    async def test_returns_text_from_injected_runner(self):
        async def fake_run_turn(prompt, history, **kw):
            assert "NVDA" in prompt
            return SimpleNamespace(text="analysis text", error=None)

        out = await analyze_alert_via_gateway(
            "TradingView alert received: BUY signal for NVDA.",
            {"model": "m"},
            run_turn_fn=fake_run_turn,
        )
        assert out == "analysis text"

    @pytest.mark.asyncio
    async def test_raises_on_gateway_error(self):
        async def failing_run_turn(prompt, history, **kw):
            return SimpleNamespace(text="", error="provider down")

        with pytest.raises(RuntimeError, match="provider down"):
            await analyze_alert_via_gateway("p", {}, run_turn_fn=failing_run_turn)

    @pytest.mark.asyncio
    async def test_raises_on_empty_response(self):
        async def empty_run_turn(prompt, history, **kw):
            return SimpleNamespace(text="   ", error=None)

        with pytest.raises(RuntimeError, match="empty response"):
            await analyze_alert_via_gateway("p", {}, run_turn_fn=empty_run_turn)
