"""quant_engine 子包必须能穿透到私有 Arthera 仓，且不改变既有解析结果。

packages/__init__.py 与 packages/quant_engine/__init__.py 早就做了 __path__
拼接，但**只做了这两层**。Python 找到一个常规子包（带 __init__.py 的目录）后
就停止查找，不会继续看父包 __path__ 里的其它条目——于是同一个包的不同子模块，
解析规则不一致：

    packages.quant_engine.services.*     → 穿透到 Arthera ✅（aria-code 没这目录）
    packages.quant_engine.stochastic.*   → 只看 aria-code ❌（aria-code 有这目录）

这个不一致本身就是 bug 来源：Arthera 里 3202 行的 backtest/engine.py（内部有
18 个文件依赖）在 aria-code 侧被 93 行的同名文件完全遮蔽；而 sports/tracker.py
又是反过来的（aria-code 665 行、Arthera 454 行），说明两边都被单独改过。
改哪份生效取决于从哪个目录启动进程，这类 bug 极难发现。
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = REPO_ROOT.parent / "Arthera" / "packages" / "quant_engine"

_SUBPACKAGES = ("backtest", "portfolio", "sports", "stochastic")


def _module_file(dotted: str) -> Path:
    return Path(inspect.getfile(importlib.import_module(dotted)))


@pytest.mark.parametrize("sub", _SUBPACKAGES)
def test_subpackage_declares_namespace_extension(sub):
    """每一层都得自己声明——父层的拼接不会向下传递。"""
    init = REPO_ROOT / "src" / "aria_code" / "packages" / "quant_engine" / sub / "__init__.py"
    text = init.read_text(encoding="utf-8")
    assert "_extend_namespace(__path__" in text, (
        f"{sub}/__init__.py 没有扩展 __path__；Arthera 侧该子包的独有模块会不可达"
    )


@pytest.mark.parametrize("sub", _SUBPACKAGES)
def test_subpackage_imports_without_private_repo(sub):
    """公开仓的用户没有 Arthera checkout，缺它绝不能让 import 失败。"""
    importlib.import_module(f"aria_code.packages.quant_engine.{sub}")


@pytest.mark.parametrize(
    "dotted",
    [
        "aria_code.packages.quant_engine.backtest.engine",
        "aria_code.packages.quant_engine.portfolio.optimizer",
        "aria_code.packages.quant_engine.stochastic.ito_calculus",
    ],
)
def test_bundled_modules_still_win(dotted):
    """本次改动只让"Arthera 独有的模块"可达，不改变任何已能解析的模块指向谁。

    用 append 而非 insert(0) 正是为此——公开仓自带的实现继续优先。
    """
    if not PRIVATE_ROOT.is_dir():
        pytest.skip("需要同级 Arthera checkout 才能验证优先级")
    assert "/aria-code/" in str(_module_file(dotted)), (
        f"{dotted} 现在解析到了 Arthera —— 优先级被改变了，这是回归"
    )


@pytest.mark.parametrize(
    "dotted",
    [
        "aria_code.packages.quant_engine.backtest.walk_forward_test",
        "aria_code.packages.quant_engine.portfolio.position_manager",
    ],
)
def test_private_only_modules_are_reachable(dotted):
    """本次修复的目标：这两个模块此前是 ModuleNotFoundError。"""
    if not PRIVATE_ROOT.is_dir():
        pytest.skip("需要同级 Arthera checkout")
    assert "/Arthera/" in str(_module_file(dotted))
