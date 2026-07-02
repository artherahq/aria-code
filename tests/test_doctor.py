try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

from doctor import analyze_python_drift, format_doctor_plain, npm_runtime_checks, provider_health_checks, provider_health_summary, run_doctor
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


def test_npm_runtime_checks_report_custom_paths(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    config = tmp_path / "config"
    cache = tmp_path / "cache"
    venv_bin = runtime / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    config.mkdir()
    cache.mkdir()
    (runtime / "aria_cli.py").write_text("print('aria')\n", encoding="utf-8")
    (venv_bin / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (runtime / ".npm-install-info.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("ARIA_HOME", str(runtime))
    monkeypatch.setenv("ARIA_CONFIG_DIR", str(config))
    monkeypatch.setenv("ARIA_CACHE_DIR", str(cache))

    checks = {check.name: check for check in npm_runtime_checks()}

    assert checks["npm_runtime:install_dir"].status == "ok"
    assert str(runtime) in checks["npm_runtime:install_dir"].detail
    assert "source=env:ARIA_HOME" in checks["npm_runtime:install_dir"].detail
    assert checks["npm_runtime:aria_cli"].status == "ok"
    assert checks["npm_runtime:venv"].status == "ok"
    assert checks["npm_runtime:install_info"].status == "ok"
    assert checks["npm_runtime:config_dir"].status == "ok"
    assert checks["npm_runtime:cache_dir"].status == "ok"


def test_npm_runtime_checks_treat_source_checkout_as_valid(monkeypatch, tmp_path):
    runtime = tmp_path / "missing-runtime"
    config = tmp_path / "config"
    cache = tmp_path / "cache"
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    config.mkdir()
    cache.mkdir()
    (tmp_path / "aria_cli.py").write_text("print('aria')\n", encoding="utf-8")
    (venv_bin / "python").write_text("#!/usr/bin/env python\n", encoding="utf-8")

    monkeypatch.setenv("ARIA_HOME", str(runtime))
    monkeypatch.setenv("ARIA_CONFIG_DIR", str(config))
    monkeypatch.setenv("ARIA_CACHE_DIR", str(cache))
    monkeypatch.delenv("ARIA_CODE_HOME", raising=False)
    monkeypatch.delenv("npm_config_aria_code_home", raising=False)
    monkeypatch.delenv("npm_config_aria_home", raising=False)
    monkeypatch.delenv("npm_config_ariacode_home", raising=False)

    checks = {check.name: check for check in npm_runtime_checks(cwd=tmp_path)}

    assert checks["npm_runtime:install_dir"].status == "warn"
    assert "source_checkout" in checks["npm_runtime:install_dir"].detail
    assert checks["npm_runtime:aria_cli"].status == "ok"
    assert checks["npm_runtime:venv"].status == "ok"


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


def test_python_drift_ok_when_versions_match_and_home_exists():
    check = analyze_python_drift("3.14.6", "3.14.6", home_exists=True)
    assert check.name == "python_venv"
    assert check.status == "ok"


def test_python_drift_warns_on_minor_version_mismatch():
    check = analyze_python_drift("3.12.4", "3.14.6", home_exists=True)
    assert check.status == "warn"
    assert "3.12.4" in check.detail and "3.14.6" in check.detail
    assert "rm -rf .venv" in check.suggestion


def test_python_drift_patch_difference_is_not_drift():
    # Patch upgrades (3.14.1 → 3.14.6) are routine; only minor drift warns.
    check = analyze_python_drift("3.14.1", "3.14.6", home_exists=True)
    assert check.status == "ok"


def test_python_drift_errs_when_base_interpreter_removed():
    # Homebrew upgraded/removed the keg the venv was built against.
    check = analyze_python_drift("3.13.2", "3.13.2", home_exists=False)
    assert check.status == "err"
    assert "base interpreter is gone" in check.detail
    assert "rm -rf .venv" in check.suggestion


def test_run_doctor_includes_python_venv_check_inside_venv(monkeypatch, tmp_path):
    # This test suite itself runs inside the project venv, so the drift check
    # must be present and healthy.
    import sys
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        import pytest
        pytest.skip("not running inside a venv")
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    report = run_doctor({}, cwd=tmp_path)
    names = {c.name: c for c in report.checks}
    assert "python_venv" in names
    assert names["python_venv"].status == "ok"


def test_run_doctor_context_check_states(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    base = {"estimated_tokens": 100, "max_tokens": 1000, "fill_pct": 10,
            "message_count": 4, "threshold": 0.78}
    ok = run_doctor({}, cwd=tmp_path, context_stats={**base, "fill_ratio": 0.10})
    warn = run_doctor({}, cwd=tmp_path, context_stats={**base, "fill_ratio": 0.80, "fill_pct": 80})
    err = run_doctor({}, cwd=tmp_path, context_stats={**base, "fill_ratio": 0.96, "fill_pct": 96})
    def ctx(report):
        return {c.name: c for c in report.checks}["context"]
    assert ctx(ok).status == "ok" and ctx(ok).suggestion == ""
    assert ctx(warn).status == "warn" and "/compact" in ctx(warn).suggestion
    assert ctx(err).status == "err"
    assert "100/1000 tokens" in ctx(ok).detail


def test_run_doctor_omits_context_check_without_stats(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    report = run_doctor({}, cwd=tmp_path)
    assert "context" not in {c.name for c in report.checks}


def test_context_health_snapshot_shape():
    from packages.aria_services.context import context_health_snapshot
    snap = context_health_snapshot(
        [{"role": "user", "content": "x" * 300}], max_tokens=2048, threshold=0.78,
    )
    assert snap["estimated_tokens"] == 100
    # ContextPolicy.normalized() floors max_tokens at 1024; 2048 passes through.
    assert snap["max_tokens"] == 2048
    assert snap["message_count"] == 1
    assert 0 < snap["fill_ratio"] < 1
