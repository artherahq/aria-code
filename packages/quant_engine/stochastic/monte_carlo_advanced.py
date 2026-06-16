"""
Advanced Monte Carlo Engine with Variance Reduction Techniques
包含对偶变量、控制变量、拟蒙特卡罗、重要性采样
"""

import numpy as np
import scipy.stats as stats
from typing import Optional, Callable, Tuple, List, Dict
from enum import Enum

class VarianceReduction(Enum):
    NONE = "none"
    ANTITHETIC = "antithetic"
    CONTROL_VARIATE = "control_variate"
    QUASI_MONTE_CARLO = "quasi_mc"
    IMPORTANCE_SAMPLING = "importance_sampling"

class MonteCarloEngine:
    """
    高级蒙特卡罗模拟引擎
    支持：
      1. 路径模拟 (Path Simulation)
      2. 衍生品定价 (Derivative Pricing)
      3. 风险度量 (Risk Metrics: VaR, CVaR)
    """
    
    @staticmethod
    def simulate_gbm(
        s0: float, mu: float, sigma: float, T: float, 
        n_steps: int, n_paths: int, 
        method: VarianceReduction = VarianceReduction.NONE
    ) -> np.ndarray:
        """
        GBM 路径模拟 - 带方差缩减选项
        """
        dt = T / n_steps
        
        if method == VarianceReduction.ANTITHETIC:
            half_paths = n_paths // 2
            dW = np.random.normal(0, np.sqrt(dt), (half_paths, n_steps))
            dW_full = np.concatenate([dW, -dW], axis=0)
        elif method == VarianceReduction.QUASI_MONTE_CARLO:
            # 使用 Sobol 序列 (拟蒙特卡罗)
            from scipy.stats import qmc
            sampler = qmc.Sobol(d=n_steps, scramble=True)
            u = sampler.random(n=n_paths)
            z = stats.norm.ppf(u)
            dW_full = z * np.sqrt(dt)
        else:
            dW_full = np.random.normal(0, np.sqrt(dt), (n_paths, n_steps))
            
        # 路径累加
        log_returns = (mu - 0.5 * sigma**2) * dt + sigma * dW_full
        cumulative_log_returns = np.cumsum(log_returns, axis=1)
        # 加入起始点
        paths = s0 * np.exp(np.hstack([np.zeros((n_paths, 1)), cumulative_log_returns]))
        
        return paths

    @staticmethod
    def price_european_option(
        s0: float, K: float, T: float, r: float, sigma: float,
        option_type: str = "call", n_paths: int = 100000,
        reduction: VarianceReduction = VarianceReduction.ANTITHETIC
    ) -> Dict[str, float]:
        """
        蒙特卡罗定价欧式期权
        """
        # 使用 1 步模拟即可（对欧式期权，中间路径不影响结果）
        if reduction == VarianceReduction.ANTITHETIC:
            z = np.random.normal(0, 1, n_paths // 2)
            z_full = np.concatenate([z, -z])
        else:
            z_full = np.random.normal(0, 1, n_paths)
            
        st = s0 * np.exp((r - 0.5 * sigma**2) * T + sigma * np.sqrt(T) * z_full)
        
        if option_type == "call":
            payoff = np.maximum(st - K, 0)
        else:
            payoff = np.maximum(K - st, 0)
            
        price = np.exp(-r * T) * np.mean(payoff)
        std_err = np.exp(-r * T) * np.std(payoff) / np.sqrt(n_paths)
        
        return {"price": float(price), "standard_error": float(std_err)}
    
    @staticmethod
    def calculate_var_cvar(returns: np.ndarray, confidence: float = 0.95) -> Tuple[float, float]:
        """
        计算历史/模拟收益率的 VaR 和 CVaR
        """
        var = np.percentile(returns, (1 - confidence) * 100)
        cvar = returns[returns <= var].mean()
        return float(var), float(cvar)
