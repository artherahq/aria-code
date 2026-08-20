# 让本子包也能穿透到私有 Arthera 仓的同名子包。
# 父层 (packages / quant_engine) 已经做了 __path__ 拼接，但 Python 找到
# 常规子包后就停止查找，不会继续看父包 __path__ 的其它条目——所以每一层
# 都得自己声明。规则：aria-code 自带的实现优先，Arthera 补齐缺口。
# 详见 packages/quant_engine/_namespace.py 的模块文档。
from .._namespace import extend as _extend_namespace
_extend_namespace(__path__, "stochastic")

"""
Arthera Stochastic Calculus Module
随机微积分模块

Components:
  - ito_calculus.py      : 伊藤引理 / 伊藤积分 / 随机微分方程
  - gbm_enhanced.py      : 增强几何布朗运动（多资产/跳跃扩散/随机波动率）
  - stochastic_processes : OU / CIR / Vasicek / Hull-White 过程
  - monte_carlo_advanced : 方差缩减蒙特卡罗（Antithetic/Control/Quasi-MC）
  - kelly_criterion      : 凯利公式（连续时间 / 多资产 / Robust版本）
"""

from .ito_calculus import ItoCalculus, ItoProcess, apply_ito_lemma
from .gbm_enhanced import EnhancedGBM
from .stochastic_processes import (
    OrnsteinUhlenbeck, CIRProcess, VasicekModel, HullWhiteModel
)
from .monte_carlo_advanced import MonteCarloEngine, VarianceReduction
from .kelly_criterion import KellyCriterion

__all__ = [
    "ItoCalculus", "ItoProcess", "apply_ito_lemma",
    "EnhancedGBM",
    "OrnsteinUhlenbeck", "CIRProcess", "VasicekModel", "HullWhiteModel",
    "MonteCarloEngine", "VarianceReduction",
    "KellyCriterion",
]
