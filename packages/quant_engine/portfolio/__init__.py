# 让本子包也能穿透到私有 Arthera 仓的同名子包。
# 父层 (packages / quant_engine) 已经做了 __path__ 拼接，但 Python 找到
# 常规子包后就停止查找，不会继续看父包 __path__ 的其它条目——所以每一层
# 都得自己声明。规则：aria-code 自带的实现优先，Arthera 补齐缺口。
# 详见 packages/quant_engine/_namespace.py 的模块文档。
from .._namespace import extend as _extend_namespace
_extend_namespace(__path__, "portfolio")

"""Portfolio optimization engine."""

from .optimizer import PortfolioOptimizer

__all__ = ["PortfolioOptimizer"]
