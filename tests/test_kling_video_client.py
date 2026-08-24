"""Tests for kling_video_client.py — JWT auth (AK/SK, not a static bearer
token) and the submit/poll shape, verified against Kling's own auth docs
plus a detailed third-party API doc mirroring api.klingai.com this session.
"""
import time
from unittest.mock import MagicMock, patch
import pytest

jwt = pytest.importorskip("jwt")

from aria_code.clients import kling_video_client as kvc


def test_mint_jwt_payload_shape():
    with patch.object(kvc, "_require_keys", return_value=("test_ak", "test_sk_long_enough_for_hmac_ok")):
        token = kvc._mint_jwt()
    decoded = jwt.decode(token, "test_sk_long_enough_for_hmac_ok", algorithms=["HS256"])
    assert decoded["iss"] == "test_ak"
    assert 1799 <= decoded["exp"] - int(time.time()) <= 1801
    assert decoded["nbf"] <= int(time.time())


def test_require_keys_missing_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("KLING_ACCESS_KEY", raising=False)
    monkeypatch.delenv("KLING_SECRET_KEY", raising=False)
    with patch.object(kvc, "_providers_path") as mock_path:
        mock_path.return_value.exists.return_value = False
        try:
            kvc._require_keys()
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "/apikey set kling" in str(exc)


def test_submit_video_posts_string_duration_and_correct_fields():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"code": 0, "message": "Success", "data": {"task_id": "task_xyz"}}
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return resp

    with patch.object(kvc, "_require_keys", return_value=("ak", "sk_long_enough_for_hmac_signing_ok")), \
         patch("requests.post", side_effect=fake_post):
        result = kvc.submit_video("a calm sunset", duration=5, mode="std")

    assert captured["url"].endswith("/v1/videos/text2video")
    assert captured["json"]["duration"] == "5"  # Kling takes duration as a string
    assert captured["json"]["mode"] == "std"
    assert result["success"] is True
    assert result["task_id"] == "task_xyz"


def test_submit_video_surfaces_kling_error_code():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"code": 1234, "message": "content moderation failed"}
    with patch.object(kvc, "_require_keys", return_value=("ak", "sk_long_enough_for_hmac_signing_ok")), \
         patch("requests.post", return_value=resp):
        result = kvc.submit_video("a prompt")
    assert result["success"] is False
    assert "content moderation failed" in result["error"]


def test_poll_video_succeed_extracts_video_url():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "code": 0, "message": "Success",
        "data": {
            "task_id": "task_xyz", "task_status": "succeed",
            "task_result": {"videos": [{"id": "v1", "url": "https://example.com/kling.mp4", "duration": "5"}]},
        },
    }
    with patch.object(kvc, "_require_keys", return_value=("ak", "sk_long_enough_for_hmac_signing_ok")), \
         patch("requests.get", return_value=resp):
        result = kvc.poll_video("task_xyz", download=False)
    assert result["success"] is True
    assert result["video_url"] == "https://example.com/kling.mp4"


def test_poll_video_processing_status():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"code": 0, "message": "Success", "data": {"task_id": "t1", "task_status": "processing"}}
    with patch.object(kvc, "_require_keys", return_value=("ak", "sk_long_enough_for_hmac_signing_ok")), \
         patch("requests.get", return_value=resp):
        result = kvc.poll_video("t1", download=False)
    assert result["success"] is True
    assert result["status"] == "processing"


def test_estimate_cost_uses_per_second_rate():
    est = kvc.estimate_cost(10)
    assert est["provider"] == "kling"
    assert est["estimated_cost_usd"] == round(10 * kvc.APPROX_COST_PER_SECOND_USD, 2)
