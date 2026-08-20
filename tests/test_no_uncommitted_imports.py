"""已提交的模块不得 import 未提交的文件。

2026-08-19 同一个错误犯了两次：packages/adk_bridge/__init__.py 里的
`from .code_review_tools import CodeReviewTools` 两次被误提交，而
code_review_tools.py 本身是未跟踪文件（维护者尚未完成的工作）。

两次都源于同一个操作：用 `git add -u -- '*.py'` 批量暂存，它会把**所有**已修改
的已跟踪文件卷进来，包括别人正在改、还没准备好提交的那些。

后果是仓库自身不自洽：本机因为文件在磁盘上看不出来，CI 和任何全新 clone 都
`ModuleNotFoundError`，且会让整个测试文件在 collection 阶段就 ERROR——比单个
用例失败更糟，因为它会中断整轮收集。

2026-08-20 又发现同一个错误的第三种形态，而且已经随 4.4.0 发出去了：
apps/cli/commands/workflow_cmds.py 的 cmd_review 里
``from agents.code_review import CodeReviewAgent``——绝对导入、不在 __init__.py
里、目标文件未跟踪。原来那版守卫只查 __init__.py 的同级相对导入（level == 1），
两条都不在覆盖范围内，所以放过了。

它的后果比 collection ERROR 更隐蔽：那行 import 外面裹着 ``except Exception``，
干净 clone 里 ImportError 被吞掉，``/review`` 静默丢掉确定性首轮检查，用户只拿到
纯 LLM 审查，也不会看到任何提示。

所以下面有两条守卫：一条查相对导入（原有），一条查所有已提交 .py 的绝对导入。
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


def _namespace_extended_packages(tracked: set[str]) -> set[str]:
    """在 __init__.py 里动过 __path__ 的包——其子树在运行期可能来自另一个仓库。

    aria-code 的 ``packages.quant_engine`` 就是这样：它 append 了 Arthera 侧的同名
    目录，于是 ``packages.quant_engine.services.*`` 这类"只有 Arthera 才有"的模块能
    透明解析。这些导入在本仓库里当然找不到对应文件，但那是设计如此，不是缺陷。
    """
    extended: set[str] = set()
    for rel in (f for f in tracked if f.endswith("__init__.py")):
        if "__path__" in (REPO_ROOT / rel).read_text(encoding="utf-8"):
            extended.add(rel[: -len("/__init__.py")].replace("/", "."))
    return extended


def _repo_top_level_names(tracked: set[str]) -> set[str]:
    """本仓库的顶层包名与顶层模块名——只有这些前缀才值得当作仓库内导入来核对。"""
    names = {f.split("/")[0] for f in tracked if f.endswith("__init__.py") and f.count("/") == 1}
    names |= {f[:-3] for f in tracked if f.endswith(".py") and "/" not in f}
    return names


def test_committed_modules_do_not_import_untracked_repo_modules():
    """绝对导入版：任何已提交的 .py 都不得 import 本仓库里未跟踪的模块。

    函数体内的导入同样计入——``/review`` 那处就在函数体里，而且被 except 吞掉，
    是这三种形态里最难靠运行发现的一种。
    """
    tracked = _tracked_files()
    top_level = _repo_top_level_names(tracked)
    namespaces = _namespace_extended_packages(tracked)
    offenders: list[str] = []

    for rel in sorted(f for f in tracked if f.endswith(".py")):
        try:
            tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                dotted_names = [node.module]
            elif isinstance(node, ast.Import):
                dotted_names = [alias.name for alias in node.names]
            else:
                continue
            for dotted in dotted_names:
                if dotted.split(".")[0] not in top_level or "." not in dotted:
                    continue  # 第三方包，或顶层本身（顶层必然已跟踪）
                if any(dotted == ns or dotted.startswith(ns + ".") for ns in namespaces):
                    continue  # 跨仓命名空间，运行期从别的仓库解析
                parts = dotted.split("/") if "/" in dotted else dotted.split(".")
                candidates = {"/".join(parts) + ".py", "/".join(parts) + "/__init__.py"}
                if not (candidates & tracked):
                    offenders.append(f"{rel}:{node.lineno} → {dotted} (未提交)")

    assert not offenders, (
        "以下已提交的模块导入了本仓库里未跟踪的模块，全新 clone、CI 与已发布的包都会失败：\n  "
        + "\n  ".join(offenders)
        + "\n\n若这行 import 裹在 try/except 里，失败会被吞掉、功能静默降级，比直接报错更难发现。"
        "\n要么把被导入的文件一并提交，要么把这行导入撤回。"
    )
