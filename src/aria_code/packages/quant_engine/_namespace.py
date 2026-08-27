"""让 quant_engine 的子包也能穿透到私有 Arthera 仓。

背景：packages/__init__.py 与 packages/quant_engine/__init__.py 都做了
``__path__.append(<Arthera 对应目录>)``，于是 ``packages.quant_engine.services``
这类"只有 Arthera 才有"的模块可以透明解析。但拼接**只做了这两层**。

Python 的包解析在找到一个常规子包（带 __init__.py 的目录）后就停止查找，
不会继续看父包 __path__ 里的其它条目。所以一旦 aria-code 自己有
``stochastic/`` 这个目录，``packages.quant_engine.stochastic.<任何模块>``
就只会在 aria-code 里找——哪怕 Arthera 有同名子包、有额外的模块。

后果是同一个包的不同子模块，解析规则不一致：

    packages.quant_engine.services.*      → 穿透到 Arthera ✅（aria-code 没这个目录）
    packages.quant_engine.stochastic.*    → 只看 aria-code ❌（aria-code 有这个目录）

实测确认过：删掉 aria-code 的 stochastic/ito_calculus.py 之后，
``import packages.quant_engine.stochastic.ito_calculus`` 直接
ModuleNotFoundError，而不是回落到 Arthera 那份同名文件；只有把整个
stochastic/ 目录删掉才会穿透。

这个不一致本身就是 bug 来源：Arthera 里 3202 行的 backtest/engine.py（内部有
18 个文件依赖它）在 aria-code 侧被 93 行的同名文件完全遮蔽，而
``sports/tracker.py`` 又是反过来的（aria-code 665 行、Arthera 454 行），
说明两边都被单独改过、已经分叉。改了哪份生效取决于从哪个目录启动进程，
这类 bug 极难发现。

extend() 把规则统一成一句话：**aria-code 自带的实现优先，Arthera 补齐缺口。**
append（而非 insert(0)）保证公开仓的版本仍然赢——这维持了既有行为，本次改动
只是让"Arthera 独有的模块"在所有层级都能被找到，不改变任何已能解析的模块
指向谁。
"""

from __future__ import annotations

from pathlib import Path
from typing import List

__all__ = ["extend"]

# src/aria_code/packages/quant_engine/_namespace.py → 上溯 5 层到 aria-code/ 的父目录，再取同级 Arthera
_PRIVATE_ROOT = Path(__file__).resolve().parents[5] / "Arthera" / "packages" / "quant_engine"


def extend(package_path: List[str], subpackage: str) -> None:
    """把 Arthera 侧的同名子包目录追加进 ``__path__``。

    子包 ``__init__.py`` 里调用::

        from .._namespace import extend
        extend(__path__, "stochastic")

    Arthera checkout 不存在时静默跳过——公开仓的用户本来就没有它，
    那不是错误状态。
    """
    candidate = _PRIVATE_ROOT / subpackage
    try:
        if candidate.is_dir() and str(candidate) not in package_path:
            package_path.append(str(candidate))
    except OSError:
        # 路径不可访问（权限、断开的符号链接）不该让 import 失败
        pass
