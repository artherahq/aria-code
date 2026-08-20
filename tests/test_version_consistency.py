"""守卫：版本号在三个地方手写，必须一致。

2026-08-19：aria-code 的版本号同时出现在

    pyproject.toml   version = "..."        ← PyPI 包版本
    npm/package.json "version": "..."       ← npm 包版本
    aria_cli.py      __version__ = "..."    ← CLI 自报版本（--version）

三处手写、无任何校验。这跟今天修掉的两个打包 bug 是同一类问题——一份事实
被抄了多份，靠人记得同步，漂移了也没人发现：pyproject 说 4.3.1 而 CLI 打印
4.3.0，用户报 bug 时给出的版本号就是错的，排查会被带偏；npm 与 PyPI 版本
不一致则会直接发出两个内容不同却同名的包。

因为发布是人工步骤，这类漂移不会被任何功能测试碰到——只能靠这条守卫。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # 只取 [project] 段落里的 version，避免撞上依赖约束里的 ">=4.3.0"
    project = re.search(r"^\[project\]$(.*?)^\[", text, re.S | re.M)
    assert project, "pyproject.toml 缺少 [project] 段"
    m = re.search(r'^version\s*=\s*"([^"]+)"', project.group(1), re.M)
    assert m, "[project] 段里没有 version"
    return m.group(1)


def _npm_version() -> str:
    return json.loads((REPO_ROOT / "npm" / "package.json").read_text(encoding="utf-8"))["version"]


def _cli_version() -> str:
    text = (REPO_ROOT / "aria_cli.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    assert m, "aria_cli.py 缺少 __version__"
    return m.group(1)


def test_pyproject_npm_and_cli_report_the_same_version():
    versions = {
        "pyproject.toml": _pyproject_version(),
        "npm/package.json": _npm_version(),
        "aria_cli.py": _cli_version(),
    }
    assert len(set(versions.values())) == 1, (
        "三处版本号不一致，发布出去会是两个同名不同内容的包、"
        "且 --version 打印的值是错的：\n  "
        + "\n  ".join(f"{k}: {v}" for k, v in versions.items())
    )


def test_changelog_documents_the_current_version():
    """CHANGELOG 必须有当前版本的条目——否则发版时使用者无从知道改了什么。"""
    version = _pyproject_version()
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{version}]" in changelog, (
        f"CHANGELOG.md 里没有 [{version}] 条目。"
        "（历史遗留：4.3.0 就是这样发出去的，没有任何变更说明。）"
    )
