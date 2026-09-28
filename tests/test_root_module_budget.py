"""Guard the src-layout introduced by the aria_code package migration."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _root_modules() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(path for path in result.stdout.splitlines() if "/" not in path)


def test_python_modules_stay_out_of_repository_root():
    # The image service is an intentionally standalone subprocess entrypoint.
    assert _root_modules() == ["image_service_runner.py"]


def test_package_discovery_points_to_src_layout():
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)["tool"]["setuptools"]

    assert config["package-dir"] == {"": "src"}
    assert config["packages"]["find"]["where"] == ["src"]
    assert "aria_code*" in config["packages"]["find"]["include"]
    for module in ("aria_cli", "doctor", "data_service", "artifacts", "report_generator"):
        assert (REPO_ROOT / "src" / "aria_code" / f"{module}.py").is_file()
