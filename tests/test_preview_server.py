"""Tests for preview_server.py and its hook in artifacts.write_artifact_metadata.

Exercises the real aiohttp server (not mocked) since the whole point is the
HTTP/SSE wire behavior — matches the pattern already used for
test_aria_mcp_server.py's real JSON-RPC exchanges.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_code import preview_server


@pytest.fixture(autouse=True)
async def _cleanup_session():
    yield
    await preview_server.stop_session()


async def test_start_session_binds_localhost_only():
    session = await preview_server.start_session()
    assert session.url.startswith("http://127.0.0.1:")


async def test_start_session_is_idempotent():
    session1 = await preview_server.start_session()
    session2 = await preview_server.start_session()
    assert session1 is session2


async def test_get_active_session_none_before_start():
    assert preview_server.get_active_session() is None


async def test_get_active_session_after_start():
    session = await preview_server.start_session()
    assert preview_server.get_active_session() is session


async def test_stop_session_clears_active_session():
    await preview_server.start_session()
    await preview_server.stop_session()
    assert preview_server.get_active_session() is None


async def test_shell_page_has_no_external_references():
    import aiohttp

    session = await preview_server.start_session()
    async with aiohttp.ClientSession() as client:
        async with client.get(session.url) as resp:
            assert resp.status == 200
            html = await resp.text()
    assert "EventSource" in html
    assert "cdn." not in html
    assert "http://" not in html.replace(session.url, "")
    assert "https://" not in html


async def test_notify_new_version_creates_thread_and_serves_content(tmp_path):
    session = await preview_server.start_session()

    class _FakeRecord:
        category = "generated/reports/market"
        topic = "AAPL"
        path = tmp_path / "report.html"

    _FakeRecord.path.write_text("<html>v1</html>", encoding="utf-8")
    await session.notify_new_version(preview_server.thread_id_for(_FakeRecord), _FakeRecord)

    import aiohttp

    async with aiohttp.ClientSession() as client:
        async with client.get(session.url + "state") as resp:
            state = await resp.json()
        thread_id = state["active_thread_id"]
        assert thread_id == "generated/reports/market:AAPL"
        assert len(state["threads"][thread_id]["versions"]) == 1

        async with client.get(session.url + f"artifact/{thread_id}?v=0") as resp:
            body = await resp.text()
        assert body == "<html>v1</html>"


async def test_same_thread_id_appends_version_not_new_thread(tmp_path):
    session = await preview_server.start_session()

    class _FakeRecord:
        category = "generated/reports/market"
        topic = "AAPL"
        path = tmp_path / "report.html"

    for i in range(2):
        _FakeRecord.path.write_text(f"<html>v{i}</html>", encoding="utf-8")
        await session.notify_new_version(preview_server.thread_id_for(_FakeRecord), _FakeRecord)

    assert len(session.threads) == 1
    thread_id = preview_server.thread_id_for(_FakeRecord)
    assert len(session.threads[thread_id].versions) == 2


async def test_artifact_route_unknown_thread_404():
    import aiohttp

    session = await preview_server.start_session()
    async with aiohttp.ClientSession() as client:
        async with client.get(session.url + "artifact/nonexistent-thread") as resp:
            assert resp.status == 404


async def test_sse_broadcasts_new_version_event(tmp_path):
    import aiohttp

    session = await preview_server.start_session()

    events = []

    async def listen():
        async with aiohttp.ClientSession() as client:
            async with client.get(session.url + "events") as resp:
                async for line in resp.content:
                    text = line.decode().strip()
                    if text.startswith("data:"):
                        events.append(text)
                        return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.2)

    class _FakeRecord:
        category = "generated/reports/market"
        topic = "TSLA"
        path = tmp_path / "report.html"

    _FakeRecord.path.write_text("<html>tsla</html>", encoding="utf-8")
    await session.notify_new_version(preview_server.thread_id_for(_FakeRecord), _FakeRecord)

    await asyncio.wait_for(task, timeout=3)
    assert len(events) == 1
    assert "new-version" in events[0]
    assert "TSLA" in events[0]


def test_thread_id_for_groups_by_category_and_topic():
    class _RecordA:
        category = "generated/reports/market"
        topic = "AAPL"

    class _RecordB:
        category = "generated/reports/market"
        topic = "AAPL"

    class _RecordC:
        category = "generated/reports/market"
        topic = "MSFT"

    assert preview_server.thread_id_for(_RecordA) == preview_server.thread_id_for(_RecordB)
    assert preview_server.thread_id_for(_RecordA) != preview_server.thread_id_for(_RecordC)


# ── artifacts.py hook ────────────────────────────────────────────────────────

async def test_write_artifact_metadata_notifies_active_session(tmp_path, monkeypatch):
    from aria_code.artifacts import create_user_artifact, write_artifact_metadata

    monkeypatch.setattr("aria_code.artifacts.user_generated_dir", lambda create=True: tmp_path)
    session = await preview_server.start_session()

    record = create_user_artifact("reports/market", "AAPL", "AAPL_market_report", ".html")
    record.path.write_text("<html>real write</html>", encoding="utf-8")
    write_artifact_metadata(record, {"kind": "market_report", "symbol": "AAPL"})

    await asyncio.sleep(0.2)
    assert session.active_thread_id is not None
    assert len(session.threads) == 1


def test_write_artifact_metadata_is_noop_safe_without_session(tmp_path, monkeypatch):
    """The overwhelmingly common path — no /canvas session running — must
    never raise, even outside an asyncio event loop (write_artifact_metadata
    is a plain sync function callable from anywhere)."""
    from aria_code.artifacts import create_user_artifact, write_artifact_metadata

    monkeypatch.setattr("aria_code.artifacts.user_generated_dir", lambda create=True: tmp_path)
    assert preview_server.get_active_session() is None

    record = create_user_artifact("reports/market", "AAPL", "AAPL_market_report", ".html")
    record.path.write_text("<html>no session</html>", encoding="utf-8")
    write_artifact_metadata(record, {"kind": "market_report", "symbol": "AAPL"})  # must not raise


# ── Cross-process discovery ─────────────────────────────────────────────────
# The port is chosen from a range at bind time, so a separate process (the
# Electron terminal's preview panel) cannot guess it. These cover the handshake.

async def test_discovery_file_absent_before_start(monkeypatch, tmp_path):
    monkeypatch.setattr(preview_server, "discovery_path", lambda: tmp_path / "canvas.json")
    assert preview_server.read_discovery() is None


async def test_discovery_file_written_on_start(monkeypatch, tmp_path):
    import os

    monkeypatch.setattr(preview_server, "discovery_path", lambda: tmp_path / "canvas.json")
    session = await preview_server.start_session()
    data = preview_server.read_discovery()
    assert data is not None
    assert data["url"] == session.url
    assert data["pid"] == os.getpid()


async def test_discovery_file_removed_on_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(preview_server, "discovery_path", lambda: tmp_path / "canvas.json")
    await preview_server.start_session()
    assert preview_server.read_discovery() is not None
    await preview_server.stop_session()
    assert preview_server.read_discovery() is None


def test_discovery_ignores_stale_file_from_dead_process(monkeypatch, tmp_path):
    """A crash or kill -9 leaves the file behind; without the liveness probe a
    consumer would point an iframe at a dead port forever."""
    import json

    path = tmp_path / "canvas.json"
    monkeypatch.setattr(preview_server, "discovery_path", lambda: path)
    path.write_text(json.dumps({"url": "http://127.0.0.1:8765/", "pid": 999999}), encoding="utf-8")
    assert preview_server.read_discovery() is None


def test_discovery_returns_data_for_live_process(monkeypatch, tmp_path):
    import json
    import os

    path = tmp_path / "canvas.json"
    monkeypatch.setattr(preview_server, "discovery_path", lambda: path)
    path.write_text(json.dumps({"url": "http://127.0.0.1:8765/", "pid": os.getpid()}), encoding="utf-8")
    data = preview_server.read_discovery()
    assert data is not None and data["url"] == "http://127.0.0.1:8765/"


def test_discovery_tolerates_corrupt_file(monkeypatch, tmp_path):
    path = tmp_path / "canvas.json"
    monkeypatch.setattr(preview_server, "discovery_path", lambda: path)
    path.write_text("{not json", encoding="utf-8")
    assert preview_server.read_discovery() is None
