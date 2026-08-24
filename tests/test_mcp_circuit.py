"""Tests for MCP per-server failure isolation: the ServerCircuit state machine
(pure, fake clock) and its wiring into MCPToolRegistry.call_tool (fail-fast,
half-open probe, dead-process reconnect, reload_server). Pins the contract's
mcp-layer next step: one bad server must not cost every call its full request
timeout for the rest of the session."""

from types import SimpleNamespace

import pytest

from aria_code.packages.aria_mcp.circuit import ServerCircuit
from aria_code.mcp_client import MCPToolRegistry


# ── pure state machine ────────────────────────────────────────────────────────

class FakeClock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


def test_circuit_stays_closed_below_threshold():
    c = ServerCircuit(failure_threshold=3, clock=FakeClock())
    c.record_failure(); c.record_failure()
    assert c.state == "closed"
    assert c.allow_call()


def test_circuit_opens_at_threshold_and_fails_fast():
    clk = FakeClock()
    c = ServerCircuit(failure_threshold=3, cooldown_seconds=60, clock=clk)
    for _ in range(3):
        c.record_failure()
    assert c.state == "open"
    assert not c.allow_call()
    assert 0 < c.seconds_until_probe() <= 60


def test_half_open_admits_single_probe_after_cooldown():
    clk = FakeClock()
    c = ServerCircuit(failure_threshold=1, cooldown_seconds=60, clock=clk)
    c.record_failure()
    assert not c.allow_call()
    clk.now += 61
    assert c.state == "half_open"
    assert c.allow_call()        # the single probe
    assert not c.allow_call()    # concurrent second call blocked while probing


def test_probe_success_closes_circuit():
    clk = FakeClock()
    c = ServerCircuit(failure_threshold=1, cooldown_seconds=60, clock=clk)
    c.record_failure()
    clk.now += 61
    assert c.allow_call()
    c.record_success()
    assert c.state == "closed"
    assert c.allow_call()


def test_probe_failure_reopens_for_another_cooldown():
    clk = FakeClock()
    c = ServerCircuit(failure_threshold=1, cooldown_seconds=60, clock=clk)
    c.record_failure()
    clk.now += 61
    assert c.allow_call()
    c.record_failure()
    assert c.state == "open"
    assert not c.allow_call()


# ── registry wiring ───────────────────────────────────────────────────────────

def _failing_server(calls):
    async def call_tool(tool, args):
        calls.append(tool)
        return {"success": False, "error": "boom"}
    return SimpleNamespace(
        call_tool=call_tool, is_alive=True, _running=True, tools=[],
        description="", restart=None,
    )


@pytest.mark.asyncio
async def test_registry_fails_fast_once_circuit_opens(tmp_path):
    reg = MCPToolRegistry(config_path=tmp_path / "mcp.json")
    calls = []
    reg._servers = {"bad": _failing_server(calls)}
    clk = FakeClock()
    reg._circuits["bad"] = ServerCircuit(failure_threshold=3, cooldown_seconds=60, clock=clk)

    for _ in range(3):
        r = await reg.call_tool("bad/x", {})
        assert not r["success"]
    assert len(calls) == 3

    r = await reg.call_tool("bad/x", {})          # circuit open now
    assert not r["success"]
    assert r.get("circuit") == "open"
    assert "temporarily disabled" in r["error"]
    assert len(calls) == 3                         # server NOT touched — fail fast

    clk.now += 61                                  # cooldown over → probe allowed
    await reg.call_tool("bad/x", {})
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_success_resets_circuit(tmp_path):
    reg = MCPToolRegistry(config_path=tmp_path / "mcp.json")
    state = {"fail": True}
    async def call_tool(tool, args):
        return {"success": not state["fail"]}
    reg._servers = {"s": SimpleNamespace(call_tool=call_tool, is_alive=True,
                                         _running=True, tools=[], description="")}
    clk = FakeClock()
    reg._circuits["s"] = ServerCircuit(failure_threshold=2, cooldown_seconds=60, clock=clk)

    await reg.call_tool("s/x", {}); await reg.call_tool("s/x", {})
    assert reg._circuits["s"].state == "open"
    clk.now += 61
    state["fail"] = False
    r = await reg.call_tool("s/x", {})             # probe succeeds
    assert r["success"]
    assert reg._circuits["s"].state == "closed"


@pytest.mark.asyncio
async def test_dead_process_triggers_restart_attempt(tmp_path):
    reg = MCPToolRegistry(config_path=tmp_path / "mcp.json")
    restarts = []
    async def restart():
        restarts.append(1)
        return False                                # restart fails
    reg._servers = {"dead": SimpleNamespace(
        call_tool=None, is_alive=False, _running=False, tools=[],
        description="", restart=restart,
    )}
    r = await reg.call_tool("dead/x", {})
    assert not r["success"]
    assert restarts == [1]
    assert "restart failed" in r["error"]


@pytest.mark.asyncio
async def test_reload_server_restarts_and_resets_circuit(tmp_path):
    reg = MCPToolRegistry(config_path=tmp_path / "mcp.json")
    async def restart():
        return True
    reg._servers = {"s": SimpleNamespace(
        restart=restart, is_alive=True, _running=True, description="",
        tools=[{"name": "t1"}],
    )}
    clk = FakeClock()
    c = reg._circuits["s"] = ServerCircuit(failure_threshold=1, cooldown_seconds=60, clock=clk)
    c.record_failure()
    assert c.state == "open"

    ok = await reg.reload_server("s")
    assert ok
    assert c.state == "closed"                     # circuit reset
    assert reg._tool_map["s/t1"] == ("s", "t1")    # tool map refreshed


@pytest.mark.asyncio
async def test_reload_unknown_server_returns_false(tmp_path):
    reg = MCPToolRegistry(config_path=tmp_path / "mcp.json")
    assert not await reg.reload_server("nope")
