import tomllib

from doctor import format_doctor_plain, provider_health_checks, provider_health_summary, run_doctor
from packages.aria_services.provider_health import summarize_provider_health


def test_run_doctor_reports_core_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    report = run_doctor({"data_sharing": False, "feedback_upload": False}, cwd=tmp_path)

    names = {check.name: check for check in report.checks}
    assert names["python"].status == "ok"
    assert names["artifact_root"].status == "ok"
    assert names["artifact_inventory"].status == "warn"
    assert "0 artifacts" in names["artifact_inventory"].detail
    assert names["privacy"].detail == "data_sharing=False, feedback_upload=False"
    assert names["ollama"].status == "warn"
    assert "network check skipped" in names["ollama"].detail


def test_format_doctor_plain_includes_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    text = format_doctor_plain(run_doctor({}, cwd=tmp_path))

    assert text.startswith("Aria Code doctor")
    assert "artifact_root" in text
    assert "passed" in text


def test_provider_health_checks_report_cooldown_and_auth_failures():
    checks = provider_health_checks([
        {
            "provider": "yfinance",
            "status": "rate_limited",
            "failures": 2,
            "cooldown_active": True,
            "cooldown_remaining_seconds": 42,
            "last_error_category": "rate_limited",
            "last_error": "429 Too Many Requests",
        },
        {
            "provider": "finnhub",
            "status": "auth",
            "failures": 1,
            "cooldown_active": False,
            "last_error_category": "auth",
            "last_error": "invalid api key",
        },
        {
            "provider": "akshare",
            "status": "ok",
            "failures": 0,
            "cooldown_active": False,
        },
    ])

    by_name = {check.name: check for check in checks}
    assert by_name["data_provider:yfinance"].status == "warn"
    assert "cooldown=42s" in by_name["data_provider:yfinance"].detail
    assert "switch provider" in by_name["data_provider:yfinance"].suggestion
    assert by_name["data_provider:finnhub"].status == "err"
    assert "API key" in by_name["data_provider:finnhub"].suggestion
    assert by_name["data_provider:akshare"].status == "ok"


def test_provider_health_checks_warn_without_calls():
    checks = provider_health_checks([])

    assert checks[0].name == "data_provider_health"
    assert checks[0].status == "warn"
    assert "no provider calls" in checks[0].detail


def test_provider_health_summary_compacts_state():
    summary = provider_health_summary([
        {"provider": "yfinance", "status": "ok", "cooldown_active": False, "last_success_at": 100.0},
        {"provider": "finnhub", "status": "rate_limited", "cooldown_active": True, "cooldown_remaining_seconds": 42, "last_error_category": "rate_limited"},
        {"provider": "akshare", "status": "auth", "cooldown_active": False, "last_error_category": "auth"},
    ])

    assert summary.name == "provider_health_summary"
    assert summary.status == "err"
    assert "3 providers" in summary.detail
    assert "1 ok" in summary.detail
    assert "1 cooldown" in summary.detail
    assert "Fix API keys first" in summary.suggestion


def test_summarize_provider_health_builds_structured_snapshot():
    summary = summarize_provider_health([
        {"provider": "yfinance", "status": "ok", "cooldown_active": False},
        {"provider": "finnhub", "status": "rate_limited", "cooldown_active": True, "last_error_category": "rate_limited"},
    ])

    payload = summary.to_dict()
    assert payload["schema"] == "aria.provider_health_summary.v1"
    assert payload["total"] == 2
    assert payload["cooldown"] == 1
    assert payload["status"] == "warn"
    assert payload["providers"] == ["yfinance", "finnhub"]


def test_pyproject_includes_top_level_modules():
    with open("pyproject.toml", "rb") as handle:
        data = tomllib.load(handle)

    modules = set(data["tool"]["setuptools"]["py-modules"])

    assert {"aria_cli", "doctor", "data_service", "artifacts", "report_generator"} <= modules
