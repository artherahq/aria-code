from pathlib import Path


def test_ci_uses_a_blocking_secret_scanner_without_a_missing_baseline():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()

    assert "gitleaks/gitleaks-action@v2" in workflow
    assert "fetch-depth: 0" in workflow
    assert ".secrets.baseline" not in workflow
    assert "detect-secrets" not in workflow
    security_job = workflow.split("\n  security:\n", 1)[1]
    assert "|| true" not in security_job
