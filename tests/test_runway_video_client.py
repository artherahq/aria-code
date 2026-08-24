"""Tests for runway_video_client.py — verified against Runway's own
generated Python SDK source this session (their docs page wasn't fetchable)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from aria_code.clients import runway_video_client as rvc


def test_require_key_missing_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("RUNWAYML_API_SECRET", raising=False)
    with patch.object(rvc, "_providers_path") as mock_path:
        mock_path.return_value.exists.return_value = False
        try:
            rvc._require_key()
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "/apikey set runway" in str(exc)


def test_submit_video_posts_correct_fields():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"id": "task_abc"}
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return resp

    with patch.object(rvc, "_require_key", return_value="rw_key"), \
         patch("requests.post", side_effect=fake_post):
        result = rvc.submit_video("a calm sunset", duration=5, ratio="1280:720")

    assert captured["url"].endswith("/v1/text_to_video")
    assert captured["json"]["promptText"] == "a calm sunset"
    assert captured["json"]["ratio"] == "1280:720"
    assert captured["headers"]["X-Runway-Version"] == rvc.API_VERSION
    assert result["success"] is True
    assert result["task_id"] == "task_abc"


def test_submit_video_surfaces_http_error():
    resp = MagicMock(status_code=400, text="Invalid ratio for model")
    with patch.object(rvc, "_require_key", return_value="rw_key"), \
         patch("requests.post", return_value=resp):
        result = rvc.submit_video("a prompt")
    assert result["success"] is False
    assert "400" in result["error"]


def test_poll_video_succeeded_extracts_output_url():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"id": "task_abc", "status": "SUCCEEDED", "output": ["https://example.com/out.mp4"]}
    with patch.object(rvc, "_require_key", return_value="rw_key"), \
         patch("requests.get", return_value=resp):
        result = rvc.poll_video("task_abc", download=False)
    assert result["success"] is True
    assert result["video_url"] == "https://example.com/out.mp4"


def test_poll_video_failed_status():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"id": "task_abc", "status": "FAILED", "failure": "content policy violation"}
    with patch.object(rvc, "_require_key", return_value="rw_key"), \
         patch("requests.get", return_value=resp):
        result = rvc.poll_video("task_abc", download=False)
    assert result["success"] is False
    assert "content policy violation" in result["error"]


def test_poll_video_running_status_reports_progress():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"id": "task_abc", "status": "RUNNING", "progress": 0.4}
    with patch.object(rvc, "_require_key", return_value="rw_key"), \
         patch("requests.get", return_value=resp):
        result = rvc.poll_video("task_abc", download=False)
    assert result["success"] is True
    assert result["status"] == "RUNNING"
    assert result["progress"] == 0.4


def test_estimate_cost_uses_per_second_rate():
    est = rvc.estimate_cost(8)
    assert est["provider"] == "runway"
    assert est["estimated_cost_usd"] == round(8 * rvc.APPROX_COST_PER_SECOND_USD, 2)
