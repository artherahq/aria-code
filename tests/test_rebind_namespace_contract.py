"""守卫：mixin 裸名必须能在 aria_cli 的运行期命名空间里解析。

`_rebind_mixin_globals()` 用 ``FunctionType(code, globals(), …)`` 重建每个 mixin
方法——第二个参数是 **aria_cli 的 __dict__**，而且是整体替换。后果常被误解：

    mixin 文件自己模块级的 import 和 def，对该文件里的 rebound 方法完全不可见。

所以 ``from packages.aria_core.paths import aria_home`` 写在 broker_cmds.py 顶部
不会让 ``aria_home()`` 在它的方法里可用；同文件 ``def format_backtest_data_error``
也一样看不见。名字只能来自 aria_cli 的命名空间（或方法体内的局部导入）。

这类错误静态工具抓不到：ruff 对 ``apps/cli/commands/*.py`` 关掉了 F821（因为裸名
本来就是运行期解析的），于是整条契约无人看守。2026-08-20 一次全量核对查出 7 个名
字、19 处引用，其中 8 处是硬崩溃——``/config``、``/setup``、``/architecture``、
``/export`` 在任何装了 rich 的正常安装上必崩，其余 11 处被 ``except Exception``
吞成静默降级。

两条作用域规则（跟 _rebind_mixin_globals 的实现一一对应）：
  * 普通方法    → 被重建，只能用 aria_cli 命名空间 + builtins + 方法内局部名。
  * static/class→ ``vars(cls)`` 里不是 FunctionType，**跳过重建**，仍用本模块命名空间。
"""

from __future__ import annotations

import ast
import builtins
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILTINS = set(dir(builtins))


def _rebound_mixin_names() -> set[str]:
    """从 aria_cli.py 源码里读出实际被重绑的 mixin 类名。"""
    src = (REPO_ROOT / "aria_cli.py").read_text(encoding="utf-8")
    return {m.group(1) for m in re.finditer(r"_rebind_mixin_globals\((\w+)\)", src)}


def _module_bindings(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _free_names(fn: ast.AST) -> set[str]:
    """函数体里到运行期才解析的裸名（排掉参数、局部赋值、局部导入、嵌套定义）。"""
    local: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.arg):
            local.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            local.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local.add(node.name)
    loaded = {
        node.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return loaded - local


def test_every_rebound_mixin_bare_name_resolves_in_aria_cli_namespace():
    aria_cli = pytest.importorskip("aria_cli")
    if not getattr(aria_cli, "HAS_RICH", False):
        # Console / Panel / Syntax / Table 等是 `if HAS_RICH:` 下的条件导入。rich 缺席时
        # 命名空间天然不完整，此时报错全是环境噪音而非真缺陷。rich 是硬依赖，正常
        # 装出来的环境不会走到这里。
        pytest.skip("rich 未安装，aria_cli 命名空间不完整，跳过契约核对")

    host = set(vars(aria_cli))
    rebound = _rebound_mixin_names()
    assert rebound, "没解析到任何 _rebind_mixin_globals(...) 调用——正则或架构变了"

    offenders: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / "apps/cli/commands").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        own = _module_bindings(tree)
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in rebound]:
            for fn in [m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                decorators = {d.id for d in fn.decorator_list if isinstance(d, ast.Name)}
                # static/classmethod 不被重建，仍看得见本模块命名空间
                scope = own if decorators & {"staticmethod", "classmethod"} else host
                for name in _free_names(fn):
                    if name in BUILTINS or name in scope or name.startswith("__"):
                        continue
                    offenders.setdefault(name, []).append(f"{path.name}:{cls.name}.{fn.name}")

    assert not offenders, (
        "以下裸名在运行期解析不到，调用到就是 NameError（被 except 吞掉则是静默降级）：\n"
        + "\n".join(f"  {name}  ←  {', '.join(sites)}" for name, sites in sorted(offenders.items()))
        + "\n\n修法二选一：把名字导入 aria_cli.py（普通方法只认那个命名空间），"
          "或在方法体内局部导入。写在 mixin 文件模块级是无效的。"
    )
