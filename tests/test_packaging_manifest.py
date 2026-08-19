"""守卫：pyproject 的打包清单必须与真实目录结构一致。

2026-08-19 起因：refactor_structure.py 那次迁移新建了 clients/ domain/ tools/
三个包，把 26 个根模块的真实实现搬了进去，但
``[tool.setuptools.packages.find].include`` 只补了 providers，漏了这三个。

后果不是"少装几个文件"，而是**发布出去的包是坏的**：24 个 re-export shim 在
py-modules 里有声明、会随包发布，但它们
``_import_module("clients.market_data_client")`` 的目标不在包里。在只含打包
内容的干净环境里 ``import market_data_client`` 直接
``ModuleNotFoundError: No module named 'clients'``。连同直接 import
tools./clients./domain. 的 10 处，以及三个目录自身 29 个文件、15,435 行代码，
从未随包发出去过。

清单漂移不会被任何常规测试发现——仓库里跑一切正常，因为源码目录就在那儿。
只有装出来才炸。所以这里用两条机械可查的不变量把它钉住。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_text() -> str:
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _find_include_patterns() -> list[str]:
    section = re.search(
        r"\[tool\.setuptools\.packages\.find\](.*?)(?=\n\[|\Z)",
        _pyproject_text(),
        re.S,
    )
    assert section, "pyproject.toml 缺少 [tool.setuptools.packages.find]"
    include = re.search(r"include = \[(.*?)\]", section.group(1), re.S)
    assert include, "packages.find 缺少 include"
    return re.findall(r'"([^"]+)"', include.group(1))


def _tracked_top_level_packages() -> set[str]:
    """所有带 __init__.py 的顶层目录 —— 即真正的顶层包。"""
    out = subprocess.run(
        ["git", "ls-files", "*/__init__.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return {
        line.split("/")[0]
        for line in out.splitlines()
        if line.count("/") == 1 and not line.startswith("tests/")
    }


def _declared_py_modules() -> set[str]:
    body = re.search(r"py-modules = \[(.*?)\n\]", _pyproject_text(), re.S)
    assert body, "pyproject.toml 缺少 [tool.setuptools].py-modules"
    return set(re.findall(r'"([^"]+)"', body.group(1)))


def _root_modules_imported_by_shipped_code() -> set[str]:
    """被仓库代码按顶层模块名 import 的根 .py —— 这些必须随包发布。

    含函数内 import：aria_cli 就是在函数体里 `from spreadsheet_tools import …`，
    这类调用在 import 期不报错，只有真正走到那条命令时才炸，最难发现。
    """
    root_modules = {
        p.stem for p in REPO_ROOT.glob("*.py") if not p.name.startswith("test_")
    }
    out = subprocess.run(
        ["git", "grep", "-hE", r"(^|[^.a-zA-Z_])(from|import) [a-z_][a-z_0-9]*", "--", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout
    imported = set(re.findall(r"(?:^|[^.\w])(?:from|import)\s+([a-z_][a-z_0-9]*)", out))
    return root_modules & imported


def test_root_modules_that_code_imports_are_declared_in_py_modules():
    """py-modules 漏声明 = 该文件不进包 = 安装后 import 它必然失败。

    2026-08-19：image_gen_tools 与 spreadsheet_tools 就是这么漏的——两者都有
    re-export shim、aria_cli 也在 import，但没进 py-modules，已发布的 4.3.0
    wheel 里根本没有这两个文件。
    """
    missing = sorted(_root_modules_imported_by_shipped_code() - _declared_py_modules())
    assert not missing, (
        "以下根模块被代码 import，但没有在 [tool.setuptools].py-modules 里声明，"
        f"pip 安装后不会存在：{missing}\n"
        "仓库内一切正常，只有装出来才炸。"
    )


def test_every_top_level_package_is_in_the_packaging_include_list():
    include_names = {p.rstrip("*") for p in _find_include_patterns()}
    missing = sorted(_tracked_top_level_packages() - include_names)
    assert not missing, (
        "以下目录有 __init__.py 但不在 [tool.setuptools.packages.find].include 里，"
        f"pip 安装后不会存在：{missing}\n"
        "仓库内测试照样全绿，只有装出来才炸——请把它们加进 include。"
    )


def test_every_reexport_shim_resolves_to_a_shipped_package():
    """根目录的兼容 shim 指向的目标包，必须也在打包清单里。

    比上一条更贴近真实故障：shim 自己在 py-modules 里、会随包发布，一旦目标
    包没打进去，安装环境下 import 它就是 ModuleNotFoundError。
    """
    include_names = {p.rstrip("*") for p in _find_include_patterns()}
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.glob("*.py")):
        head = path.read_text(encoding="utf-8")[:200]
        if "Compatibility import" not in head:
            continue
        target = re.search(r'_import_module\("([A-Za-z_][A-Za-z_0-9]*)\.', head)
        if target and target.group(1) not in include_names:
            offenders.append(f"{path.name} -> {target.group(1)}/")
    assert not offenders, (
        "以下 re-export shim 指向的包不在打包清单里，安装后 import 必然失败：\n  "
        + "\n  ".join(offenders)
    )
