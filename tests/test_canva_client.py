"""Tests for canva_client.py — includes a regression test for the real bug
found this session: POST /autofills was missing the required "type":
"create_from_brand_template" field, verified against canva.dev/docs/connect.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import canva_client


def test_autofill_design_request_includes_required_type_field():
    create_resp = MagicMock(status_code=201)
    create_resp.json.return_value = {"job": {"id": "job1", "status": "in_progress"}}
    poll_resp = MagicMock(status_code=200)
    poll_resp.json.return_value = {
        "job": {"id": "job1", "status": "success", "result": {"design": {"id": "d1", "url": "https://canva.com/d1"}}}
    }

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return create_resp

    with patch.object(canva_client, "_access_token", return_value="tok"), \
         patch("requests.post", side_effect=fake_post), \
         patch("requests.get", return_value=poll_resp):
        result = canva_client.autofill_design("tmpl1", {"headline": {"type": "text", "text": "Q3"}})

    assert captured["json"]["type"] == "create_from_brand_template"
    assert captured["json"]["brand_template_id"] == "tmpl1"
    assert result["success"] is True
    assert result["design_id"] == "d1"


def test_autofill_design_reports_failed_job():
    create_resp = MagicMock(status_code=201)
    create_resp.json.return_value = {"job": {"id": "job1", "status": "in_progress"}}
    poll_resp = MagicMock(status_code=200)
    poll_resp.json.return_value = {"job": {"id": "job1", "status": "failed", "error": {"message": "bad template"}}}

    with patch.object(canva_client, "_access_token", return_value="tok"), \
         patch("requests.post", return_value=create_resp), \
         patch("requests.get", return_value=poll_resp):
        result = canva_client.autofill_design("tmpl1", {})

    assert result["success"] is False
    assert "bad template" in result["error"]


def test_upload_asset_sets_base64_metadata_header(tmp_path):
    img = tmp_path / "chart.png"
    img.write_bytes(b"fake-png-bytes")

    create_resp = MagicMock(status_code=201)
    create_resp.json.return_value = {"job": {"id": "up1", "status": "in_progress"}}
    poll_resp = MagicMock(status_code=200)
    poll_resp.json.return_value = {"job": {"id": "up1", "status": "success", "asset": {"id": "a1", "name": "chart.png"}}}

    captured = {}

    def fake_post(url, headers=None, data=None, timeout=None):
        captured["headers"] = headers
        captured["data"] = data
        return create_resp

    with patch.object(canva_client, "_access_token", return_value="tok"), \
         patch("requests.post", side_effect=fake_post), \
         patch("requests.get", return_value=poll_resp):
        result = canva_client.upload_asset(str(img))

    assert captured["headers"]["Content-Type"] == "application/octet-stream"
    assert "Asset-Upload-Metadata" in captured["headers"]
    assert result["success"] is True
    assert result["asset_id"] == "a1"


def test_upload_asset_missing_file():
    result = canva_client.upload_asset("/nonexistent/file.png")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_refresh_if_needed_skips_refresh_when_token_still_valid():
    import time

    entry = {"access_token": "tok", "expires_at": time.time() + 3600}
    result = canva_client._refresh_if_needed(entry)
    assert result is entry  # unchanged, no network call attempted


def test_refresh_if_needed_raises_without_refresh_token():
    import time

    entry = {"access_token": "tok", "expires_at": time.time() - 10, "refresh_token": ""}
    try:
        canva_client._refresh_if_needed(entry)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "connect again" in str(exc)
