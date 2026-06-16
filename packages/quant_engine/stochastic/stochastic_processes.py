"""
Stochastic Processes for Interest Rates and Mean-Reverting Assets
包含均值回归过程：OU, CIR, Vasicek, Hull-White
"""

import numpy as np
from typing import Optional, Tuple

class OrnsteinUhlenbeck:
    """
    Ornstein-Uhlenbeck Process (均值回归过程)
    dX_t = kappa * (theta - X_t) * dt + sigma * dW_t
    
    常用于建模：
      - 利率 (Vasicek Model)
      - 波动率 (Heston 模型中的方差回归)
      - 配对交易中的价差 (Spread)
    """
    def __init__(self, kappa: float, theta: float, sigma: float, x0: float):
        self.kappa = kappa
        self.theta = theta
        self.sigma = sigma
        self.x0 = x0

    def simulate(self, T: float, n_steps: int, n_paths: int = 1, seed: Optional[int] = None) -> np.ndarray:
        if seed is not None: np.random.seed(seed)
        dt = T / n_steps
        paths = np.zeros((n_paths, n_steps + 1))
        paths[:, 0] = self.x0
        
        for t in range(n_steps):
            dW = np.random.normal(0, np.sqrt(dt), n_paths)
            paths[:, t+1] = paths[:, t] + self.kappa * (self.theta - paths[:, t]) * dt + self.sigma * dW
            
        return paths

class CIRProcess:
    """
    Cox-Ingersoll-Ross Process (平方根均值回归过程)
    dX_t = kappa * (theta - X_t) * dt + sigma * sqrt(X_t) * dW_t
    
    优点：保证 X_t 始终非负（若 2*kappa*theta > sigma^2 则永远不触碰0）
    """
    def __init__(self, kappa: float, theta: float, sigma: float, x0: float):
        self.kappa = kappa
        self.theta = theta
        self.sigma = sigma
        self.x0 = x0

    def simulate(self, T: float, n_steps: int, n_paths: int = 1, seed: Optional[int] = None) -> np.ndarray:
        if seed is not None: np.random.seed(seed)
        dt = T / n_steps
        paths = np.zeros((n_paths, n_steps + 1))
        paths[:, 0] = self.x0
        
        for t in range(n_steps):
            dW = np.random.normal(0, np.sqrt(dt), n_paths)
            # 使用 Full Truncation 保证数值稳定性
            x_plus = np.maximum(paths[:, t], 0)
            paths[:, t+1] = paths[:, t] + self.kappa * (self.theta - x_plus) * dt + self.sigma * np.sqrt(x_plus) * dW
            
        return paths

class VasicekModel(OrnsteinUhlenbeck):
    """Vasicek 利率模型 (即 OU 过程用于利率)"""
    pass

class HullWhiteModel:
    """
    Hull-White Model (带时变参数的均值回归)
    dr_t = (theta(t) - a * r_t) * dt + sigma * dW_t
    """
    def __init__(self, a: float, sigma: float, r0: float):
        self.a = a
        self.sigma = sigma
        self.r0 = r0

    def simulate(self, theta_func, T: float, n_steps: int, n_paths: int = 1, seed: Optional[int] = None) -> np.ndarray:
        if seed is not None: np.random.seed(seed)
        dt = T / n_steps
        t_grid = np.linspace(0, T, n_steps + 1)
        paths = np.zeros((n_paths, n_steps + 1))
        paths[:, 0] = self.r0
        
        for t in range(n_steps):
            dW = np.random.normal(0, np.sqrt(dt), n_paths)
            theta_t = theta_func(t_grid[t])
            paths[:, t+1] = paths[:, t] + (theta_t - self.a * paths[:, t]) * dt + self.sigma * dW
            
        return paths
