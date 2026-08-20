"""已提交的模块不得 import 未提交的文件。

2026-08-19 同一个错误犯了两次：packages/adk_bridge/__init__.py 里的
`from .code_review_tools import CodeReviewTools` 两次被误提交，而
code_review_tools.py 本身是未跟踪文件（维护者尚未完成的工作）。

两次都源于同一个操作：用 `git add -u -- '*.py'` 批量暂存，它会把**所有**已修改
的已跟踪文件卷进来，包括别人正在改、还没准备好提交的那些。

后果是仓库自身不自洽：本机因为文件在磁盘上看不出来，CI 和任何全新 clone 都
`ModuleNotFoundError`，且会让整个测试文件在 collection 阶段就 ERROR——比单个
用例失败更糟，因为它会中断整轮收集。

这条守卫检查所有已提交的 Python 文件，确认它们的相对导入目标也在版本控制里。
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return set(out.split())


def test_committed_modules_do_not_import_untracked_siblings():
    tracked = _tracked_files()
    offenders: list[str] = []

    for rel in sorted(f for f in tracked if f.endswith("__init__.py")):
        path = REPO_ROOT / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue

        package_dir = Path(rel).parent
        for node in ast.walk(tree):
            # 只查同级相对导入（level == 1），跨包引用由打包守卫负责
            if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
                continue
            target = node.module.split(".")[0]
            candidates = {
                str(package_dir / f"{target}.py"),
                str(package_dir / target / "__init__.py"),
            }
            if not (candidates & tracked):
                offenders.append(f"{rel}:{node.lineno} → .{node.module} (未提交)")

    assert not offenders, (
        "以下已提交的模块导入了未跟踪的文件，全新 clone 与 CI 会 ImportError：\n  "
        + "\n  ".join(offenders)
        + "\n\n通常是 `git add -u` 批量暂存时把别人未完成的工作卷了进来。"
        "\n要么把被导入的文件一并提交，要么把这行导入撤回。"
    )
