from __future__ import annotations

import io
import zipfile

import pytest

from aria_code.project_review import ProjectReviewError, review_project_archive


def _zip(entries: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return output.getvalue()


def test_project_review_reports_findings_and_project_advice():
    payload = _zip({
        "sample/app.py": "from fastapi import FastAPI\nAPI_KEY = 'super-secret-value'\ntry:\n    run()\nexcept:\n    pass\n",
        "sample/pyproject.toml": "[project]\nname='sample'\n",
    })
    report = review_project_archive(payload, "sample.zip")
    assert report["schema_version"] == "aria.project-review.v1"
    assert report["summary"]["reviewed_files"] == 2
    assert any(item["rule"] == "hardcoded-secret" for item in report["findings"])
    assert any(item["category"] == "testing" for item in report["recommendations"])
    assert "backend_api" in report["product_assessment"]["product_types"]


def test_project_review_skips_sensitive_files():
    report = review_project_archive(_zip({".env": "TOKEN=secret", "src/main.py": "print('ok')"}), "sample.zip")
    assert report["summary"]["skipped_sensitive_files"] == 1
    assert all(item.get("file") != ".env" for item in report["findings"])


def test_project_review_rejects_path_traversal():
    with pytest.raises(ProjectReviewError, match="unsafe path"):
        review_project_archive(_zip({"../outside.py": "print('no')"}), "sample.zip")
