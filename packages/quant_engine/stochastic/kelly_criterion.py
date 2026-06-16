"""
Kelly Criterion Implementation
包含：
  1. 连续时间凯利公式 (Continuous Time)
  2. 多资产矩阵形式 (Multi-Asset Matrix Form) — 含协方差收缩
  3. 带方差不确定性的 Robust 凯利 (Fractional Kelly)
  4. from_returns() — 直接从历史收益率矩阵构建 Kelly 权重 (LedoitWolf 收缩)

修复 (v2.0):
  - multi_asset_kelly: 默认启用 Tikhonov 正则化防止奇异协方差矩阵
  - from_returns(): 优先使用 LedoitWolf 协方差估计，回退到样本协方差
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Union
from scipy.optimize import minimize

# LedoitWolf 协方差收缩（可选）
try:
    from sklearn.covariance import LedoitWolf as _LedoitWolf
    LEDOIT_WOLF_AVAILABLE = True
except ImportError:
    LEDOIT_WOLF_AVAILABLE = False


class KellyCriterion:
    """
    凯利公式服务
    目标：最大化财富对数增长率
    """

    @staticmethod
    def continuous_time_kelly(mu: float, r: float, sigma: float) -> float:
        """
        标准连续时间凯利公式：
        f* = (mu - r) / sigma^2
        """
        if sigma <= 0:
            return 0.0
        return (mu - r) / (sigma ** 2)

    @staticmethod
    def multi_asset_kelly(
        mu_vec: np.ndarray,
        rf: float,
        cov_matrix: np.ndarray,
        allow_short: bool = False,
        max_leverage: float = 1.0,
        shrinkage: bool = True,
        shrinkage_alpha: Optional[float] = None,
    ) -> np.ndarray:
        """
        多资产凯利公式 (矩阵形式):
        f* = Σ^(-1) * (μ - rf)

        Args:
            mu_vec:          期望收益率向量 (n,)
            rf:              无风险收益率
            cov_matrix:      协方差矩阵 (n, n)
            allow_short:     是否允许做空
            max_leverage:    最大总杠杆
            shrinkage:       True → Tikhonov 正则化 (cov + alpha*I)
                             有效防止近奇异协方差矩阵带来的极端权重
            shrinkage_alpha: 正则化强度；None → 自动选为 1% of trace/n
        """
        n = len(mu_vec)
        cov = np.array(cov_matrix, dtype=float)

        if shrinkage:
            if shrinkage_alpha is None:
                # 自动正则化强度：trace 的 1 %
                shrinkage_alpha = max(np.trace(cov) / n * 0.01, 1e-6)
            cov = cov + shrinkage_alpha * np.eye(n)

        try:
            excess_ret = np.array(mu_vec, dtype=float) - rf
            cov_inv    = np.linalg.inv(cov)
            kelly_f    = cov_inv @ excess_ret

            # 约束
            if not allow_short:
                kelly_f = np.maximum(kelly_f, 0.0)

            total_leverage = float(np.sum(kelly_f))
            if total_leverage > max_leverage:
                kelly_f = kelly_f * (max_leverage / total_leverage)

            return kelly_f

        except np.linalg.LinAlgError:
            # 即使加了正则化还是奇异 → 退化为等权
            return np.ones(n) / n

    @classmethod
    def from_returns(
        cls,
        returns_df: pd.DataFrame,
        rf: float = 0.02,
        allow_short: bool = False,
        max_leverage: float = 1.0,
        fraction: float = 1.0,
        use_ledoit_wolf: bool = True,
    ) -> Dict[str, float]:
        """
        直接从历史日收益率矩阵计算 Kelly 权重。

        协方差估计优先级：
          1. LedoitWolf 收缩估计（sklearn，若可用）
          2. 样本协方差（兜底）

        Args:
            returns_df:      每列为一个资产的日收益率 DataFrame
            rf:              年化无风险收益率（自动转为日化）
            allow_short:     是否允许做空
            max_leverage:    最大杠杆
            fraction:        分段凯利系数（0 < f ≤ 1.0）
            use_ledoit_wolf: 是否优先使用 LedoitWolf 收缩

        Returns:
            {symbol: weight} 字典
        """
        symbols = list(returns_df.columns)
        R       = returns_df.dropna().values  # (T, n)
        dt      = 1.0 / 252

        mu_daily = R.mean(axis=0)
        mu_ann   = mu_daily / dt          # 日化 → 年化
        rf_daily = rf * dt

        # -- 协方差矩阵 --
        if use_ledoit_wolf and LEDOIT_WOLF_AVAILABLE:
            lw  = _LedoitWolf().fit(R)
            cov_daily = lw.covariance_           # (n, n)
        else:
            cov_daily = np.cov(R, rowvar=False)  # 样本协方差

        cov_ann = cov_daily / dt  # 日化协方差 → 年化

        weights = cls.multi_asset_kelly(
            mu_vec      = mu_ann,
            rf          = rf,
            cov_matrix  = cov_ann,
            allow_short = allow_short,
            max_leverage= max_leverage,
            shrinkage   = True,  # always apply Tikhonov on top
        )

        # 应用分段凯利
        weights = weights * fraction

        return dict(zip(symbols, weights.tolist()))

    @staticmethod
    def robust_log_kelly(
        returns_df: pd.DataFrame,
        rf: float = 0.02,
        fraction: float = 0.5,
    ) -> Dict[str, float]:
        """
        基于历史分布的非参数对数凯利 (含分段凯利/Fractional Kelly)
        """
        symbols      = returns_df.columns
        n            = len(symbols)
        hist_returns = returns_df.values
        dt           = 1 / 252

        def obj(w: np.ndarray) -> float:
            port_rets = np.dot(hist_returns, w)
            growth    = 1.0 + port_rets + (1.0 - np.sum(w)) * (rf * dt)
            if np.any(growth <= 0):
                return 1e10
            return -float(np.mean(np.log(growth)))

        cons   = [{"type": "ineq", "fun": lambda x: 1.0 - np.sum(x)}]
        bounds = [(0, 1) for _ in range(n)]
        res    = minimize(obj, np.ones(n) / n, bounds=bounds, constraints=cons)

        # 应用分段凯利 (Robust 修正)
        final_weights = res.x * fraction
        return dict(zip(symbols, final_weights.tolist()))
