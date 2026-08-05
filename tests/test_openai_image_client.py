"""Tests for openai_image_client.py — field shapes verified against the
official openai-python SDK source this session (images/generations and
images/edits, since the public docs page 403s to scrapers)."""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import openai_image_client as oic


def _fake_b64_png() -> str:
    return base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def test_api_key_prefers_env_var(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert oic._api_key() == "sk-from-env"


def test_generate_image_missing_key_raises_actionable_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch.object(oic, "_providers_path") as mock_path:
        mock_path.return_value.exists.return_value = False
        try:
            oic._require_key()
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "/apikey set openai" in str(exc)


def test_generate_image_posts_gpt_image_1_with_correct_fields(tmp_path):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"created": 1, "data": [{"b64_json": _fake_b64_png()}]}
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return resp

    with patch.object(oic, "_require_key", return_value="sk-test"), \
         patch("requests.post", side_effect=fake_post), \
         patch("artifacts.create_user_artifact") as mock_artifact:
        mock_artifact.return_value.path = tmp_path / "out.png"
        result = oic.generate_image("a minimal poster", size="1024x1024", quality="high", confirmed=True)

    assert captured["url"].endswith("/images/generations")
    assert captured["json"]["model"] == "gpt-image-1"
    assert captured["json"]["prompt"] == "a minimal poster"
    assert result["success"] is True


def test_generate_image_surfaces_api_error():
    resp = MagicMock(status_code=401, text="Incorrect API key provided")
    with patch.object(oic, "_require_key", return_value="sk-bad"), \
         patch("requests.post", return_value=resp):
        result = oic.generate_image("a poster", confirmed=True)
    assert result["success"] is False
    assert "401" in result["error"]


def test_generate_image_refuses_without_confirmed():
    result = oic.generate_image("a poster")
    assert result["success"] is False
    assert "confirmed" in result["error"]


def test_edit_image_refuses_without_confirmed():
    result = oic.edit_image("/nonexistent/photo.jpg", "make it duotone")
    assert result["success"] is False
    assert "confirmed" in result["error"]


def test_edit_image_missing_file():
    result = oic.edit_image("/nonexistent/photo.jpg", "make it duotone", confirmed=True)
    assert result["success"] is False
    assert "not found" in result["error"]


def test_edit_image_posts_multipart_to_edits_endpoint(tmp_path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"fake-jpeg")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"created": 1, "data": [{"b64_json": _fake_b64_png()}]}
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["files"] = list(files.keys())
        return resp

    with patch.object(oic, "_require_key", return_value="sk-test"), \
         patch("requests.post", side_effect=fake_post), \
         patch("artifacts.create_user_artifact") as mock_artifact:
        mock_artifact.return_value.path = tmp_path / "out.png"
        result = oic.edit_image(str(src), "convert to duotone", confirmed=True)

    assert captured["url"].endswith("/images/edits")
    assert captured["data"]["model"] == "gpt-image-1"
    assert "image" in captured["files"]
    assert result["success"] is True


def test_edit_image_with_mask_uploads_both_files(tmp_path):
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"fake-jpeg")
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"fake-mask-png")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"created": 1, "data": [{"b64_json": _fake_b64_png()}]}
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["files"] = list(files.keys())
        return resp

    with patch.object(oic, "_require_key", return_value="sk-test"), \
         patch("requests.post", side_effect=fake_post), \
         patch("artifacts.create_user_artifact") as mock_artifact:
        mock_artifact.return_value.path = tmp_path / "out.png"
        result = oic.edit_image(str(src), "fill in the sky", mask_path=str(mask), confirmed=True)

    assert "image" in captured["files"]
    assert "mask" in captured["files"]
    assert result["success"] is True


def test_edit_image_mask_not_found():
    result = oic.edit_image("/nonexistent/photo.jpg", "make it duotone", mask_path="/nonexistent/mask.png", confirmed=True)
    assert result["success"] is False
    assert "not found" in result["error"]


def test_estimate_cost_known_size_quality():
    result = oic.estimate_cost(size="1024x1024", quality="high")
    assert result["estimated_cost_usd"] == 0.167


def test_estimate_cost_unknown_combo_reports_none():
    result = oic.estimate_cost(size="auto", quality="auto")
    assert result["estimated_cost_usd"] is None
