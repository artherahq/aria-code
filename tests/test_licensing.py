"""Tests for the feature-entitlement gate (licensing.py)."""
import hashlib
import hmac
import json
import pathlib
import sys

import pytest

_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)

import licensing  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # No ambient license; redirect license paths to an empty tmp dir; clear cache.
    monkeypatch.delenv("ARIA_LICENSE_KEY", raising=False)
    monkeypatch.delenv("ARIA_LICENSE_PUBKEY", raising=False)
    monkeypatch.setattr(licensing, "_LICENSE_PATHS", [tmp_path / "license.json"])
    monkeypatch.setattr(licensing, "_CACHE", None)
    yield


def _refresh():
    licensing.current_license(refresh=True)


def test_free_features_always_available():
    _refresh()
    for f in ("chat", "market_data", "backtest", "broker_trade"):
        assert licensing.has_feature(f) is True


def test_premium_blocked_by_default():
    _refresh()
    assert licensing.has_feature("premium_factors") is False
    ok, msg = licensing.require_feature("premium_factors")
    assert ok is False and "专业版" in msg


def test_env_key_grants_all(monkeypatch):
    monkeypatch.setenv("ARIA_LICENSE_KEY", "abc123")
    _refresh()
    assert licensing.has_feature("premium_factors") is True
    assert licensing.license_status()["tier"] == "pro"


def test_license_file_grants_listed_features(tmp_path, monkeypatch):
    lic = tmp_path / "license.json"
    lic.write_text(json.dumps({"key": "k", "tier": "pro",
                               "features": ["premium_factors"]}))
    monkeypatch.setattr(licensing, "_LICENSE_PATHS", [lic])
    _refresh()
    assert licensing.has_feature("premium_factors") is True
    assert licensing.has_feature("other_premium") is False


def test_expired_license_invalid(tmp_path, monkeypatch):
    lic = tmp_path / "license.json"
    lic.write_text(json.dumps({"key": "k", "features": ["*"], "exp": "2000-01-01"}))
    monkeypatch.setattr(licensing, "_LICENSE_PATHS", [lic])
    _refresh()
    assert licensing.has_feature("premium_factors") is False
    st = licensing.license_status()
    assert st["expired"] is True and st["valid"] is False


def test_signature_required_when_pubkey_set(tmp_path, monkeypatch):
    secret = "build-secret"
    body = {"key": "k", "tier": "pro", "features": ["premium_factors"]}
    good_sig = hmac.new(secret.encode(), json.dumps(body, sort_keys=True).encode(),
                        hashlib.sha256).hexdigest()

    lic = tmp_path / "license.json"
    monkeypatch.setattr(licensing, "_LICENSE_PATHS", [lic])
    monkeypatch.setenv("ARIA_LICENSE_PUBKEY", secret)

    # Bad signature → rejected
    lic.write_text(json.dumps({**body, "sig": "deadbeef"}))
    _refresh()
    assert licensing.has_feature("premium_factors") is False

    # Correct signature → accepted
    lic.write_text(json.dumps({**body, "sig": good_sig}))
    _refresh()
    assert licensing.has_feature("premium_factors") is True
