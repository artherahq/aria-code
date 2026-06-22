"""Tests for SessionManager.search_sessions() full-text search."""

import json
import pytest
from pathlib import Path
from apps.cli.session_store import SessionManager


@pytest.fixture()
def session_mgr(tmp_path):
    mgr = SessionManager(sessions_dir=tmp_path / "sessions")
    return mgr


def _write_session(mgr: SessionManager, sid: str, title: str, messages: list):
    mgr.save_session(sid, messages, metadata={"title": title})


class TestSearchSessions:
    def test_empty_sessions_returns_empty(self, session_mgr):
        results = session_mgr.search_sessions("AAPL")
        assert results == []

    def test_finds_match_in_user_message(self, session_mgr):
        _write_session(session_mgr, "s001", "Test", [
            {"role": "user", "content": "Tell me about AAPL earnings"},
            {"role": "assistant", "content": "AAPL reported strong results"},
        ])
        results = session_mgr.search_sessions("AAPL")
        assert len(results) == 1
        assert results[0]["id"] == "s001"

    def test_no_match_returns_empty(self, session_mgr):
        _write_session(session_mgr, "s002", "Test", [
            {"role": "user", "content": "What is the weather?"},
        ])
        results = session_mgr.search_sessions("AAPL")
        assert results == []

    def test_case_insensitive_match(self, session_mgr):
        _write_session(session_mgr, "s003", "Test", [
            {"role": "user", "content": "Discuss aapl stock price"},
        ])
        results = session_mgr.search_sessions("AAPL")
        assert len(results) == 1

    def test_match_count_reflects_multiple_hits(self, session_mgr):
        _write_session(session_mgr, "s004", "Test", [
            {"role": "user", "content": "AAPL is interesting"},
            {"role": "assistant", "content": "Yes AAPL is a large-cap stock"},
            {"role": "user", "content": "What is AAPL forecast?"},
        ])
        results = session_mgr.search_sessions("AAPL")
        assert results[0]["match_count"] == 3

    def test_sorted_by_match_count_descending(self, session_mgr):
        _write_session(session_mgr, "s005", "Few", [
            {"role": "user", "content": "AAPL once"},
        ])
        _write_session(session_mgr, "s006", "Many", [
            {"role": "user", "content": "AAPL AAPL"},
            {"role": "assistant", "content": "AAPL AAPL AAPL"},
        ])
        results = session_mgr.search_sessions("AAPL")
        assert results[0]["match_count"] >= results[1]["match_count"]
        assert results[0]["id"] == "s006"

    def test_preview_contains_context(self, session_mgr):
        _write_session(session_mgr, "s007", "Test", [
            {"role": "user", "content": "The company Apple (AAPL) reported earnings"},
        ])
        results = session_mgr.search_sessions("AAPL")
        assert "AAPL" in results[0]["preview"]

    def test_result_has_required_fields(self, session_mgr):
        _write_session(session_mgr, "s008", "My title", [
            {"role": "user", "content": "search term here"},
        ])
        results = session_mgr.search_sessions("search term")
        assert len(results) == 1
        r = results[0]
        assert "id" in r
        assert "title" in r
        assert "updated" in r
        assert "match_count" in r
        assert "preview" in r

    def test_title_preserved(self, session_mgr):
        _write_session(session_mgr, "s009", "My Session Title", [
            {"role": "user", "content": "hello world"},
        ])
        results = session_mgr.search_sessions("hello world")
        assert results[0]["title"] == "My Session Title"

    def test_matches_in_list_content_blocks(self, session_mgr):
        _write_session(session_mgr, "s010", "Blocks", [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Analysis of AAPL shows bullish trend"},
            ]},
        ])
        results = session_mgr.search_sessions("AAPL")
        assert len(results) == 1

    def test_limit_respected(self, session_mgr):
        for i in range(5):
            _write_session(session_mgr, f"sess{i:03d}", f"Title {i}", [
                {"role": "user", "content": f"query mention {i}"},
            ])
        results = session_mgr.search_sessions("query mention", limit=3)
        assert len(results) <= 3
