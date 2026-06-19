import pathlib
import sys
import json


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


def _fake_prices(symbols):
    out = {}
    for idx, sym in enumerate(symbols):
        out[sym] = {"price": 100 + idx, "pct_change": idx - 2.5}
    return out


def test_dashboard_brief_mode_is_market_focused(monkeypatch, tmp_path):
    import dashboard_generator as dg

    monkeypatch.setattr(dg, "_load_portfolio", lambda: ([], []))
    monkeypatch.setattr(dg, "_load_alerts", lambda: [])
    monkeypatch.setattr(dg, "_load_recent_artifacts", lambda limit=10: [])
    monkeypatch.setattr(dg, "_fetch_prices", _fake_prices)

    out = dg.generate(mode="brief", output_path=tmp_path / "brief.html")
    html = out.read_text(encoding="utf-8")

    assert "MODE: BRIEF" in html
    assert "TOP MOVERS" in html
    assert "WATCHLIST" in html
    assert "PORTFOLIO SUMMARY" not in html
    assert "OPEN POSITIONS" not in html


def test_dashboard_portfolio_mode_is_position_focused(monkeypatch, tmp_path):
    import dashboard_generator as dg

    positions = [
        {"symbol": "AAPL", "avg_cost": 90.0, "net_qty": 10, "cost_basis": 900.0},
    ]
    realized = [{"total_pnl": 12.0}]

    monkeypatch.setattr(dg, "_load_portfolio", lambda: (positions, realized))
    monkeypatch.setattr(dg, "_load_alerts", lambda: [{"symbol": "AAPL", "condition": "gt", "value": 100, "trigger_count": 1, "active": True}])
    monkeypatch.setattr(dg, "_load_recent_artifacts", lambda limit=10: [{"name": "report.html", "path": "/tmp/report.html", "category": "html", "size_kb": 10, "mtime": "2026-06-18 10:00"}])
    monkeypatch.setattr(dg, "_fetch_prices", _fake_prices)

    out = dg.generate(mode="portfolio", output_path=tmp_path / "portfolio.html")
    html = out.read_text(encoding="utf-8")

    assert "MODE: PORTFOLIO" in html
    assert "PORTFOLIO SUMMARY" in html
    assert "OPEN POSITIONS" in html
    assert "PRICE ALERTS" in html
    assert "RECENT GENERATED FILES" in html
    assert "WATCHLIST" not in html
    assert "TOP MOVERS" not in html


def test_dashboard_default_output_uses_user_generated_dir(monkeypatch, tmp_path):
    import dashboard_generator as dg

    monkeypatch.setenv("ARIA_USER_OUTPUT_ROOT", str(tmp_path / "user-output"))
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "project-artifacts"))
    monkeypatch.setattr(dg, "_load_portfolio", lambda: ([], []))
    monkeypatch.setattr(dg, "_load_alerts", lambda: [])
    monkeypatch.setattr(dg, "_load_recent_artifacts", lambda limit=10: [])
    monkeypatch.setattr(dg, "_fetch_prices", _fake_prices)

    out = dg.generate(mode="brief")

    assert out.is_file()
    assert str(tmp_path / "user-output" / "generated" / "dashboards") in str(out)
    metadata_path = out.with_suffix(".metadata.json")
    assert metadata_path.is_file()
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "dashboard"
    assert payload["artifact"]["path"] == str(out)
