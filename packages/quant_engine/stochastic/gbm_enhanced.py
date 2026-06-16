"""
增强型几何布朗运动 (Enhanced Geometric Brownian Motion)

包含：
  1. 多资产相关 GBM (Multi-asset GBM with Cholesky Decomposition)
  2. 默顿跳跃扩散模型 (Merton Jump-Diffusion Model)
  3. Heston 随机波动率模型 (Heston Stochastic Volatility)
  4. 分数布朗运动 (Fractional Brownian Motion) - 选配
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple, List, Union


class EnhancedGBM:
    """
    增强型 GBM 仿真器
    """

    @staticmethod
    def simulate_multi_asset(
        s0: np.ndarray,
        mu: np.ndarray,
        sigma: np.ndarray,
        corr_matrix: np.ndarray,
        T: float = 1.0,
        n_steps: int = 252,
        n_paths: int = 1000,
        seed: Optional[int] = None
    ) -> np.ndarray:
        """
        多资产相关 GBM 模拟

        使用 Cholesky 分解生成相关随机增量：
        dS_i = μ_i S_i dt + σ_i S_i dW_i
        E[dW_i dW_j] = ρ_{ij} dt

        Args:
            s0          : 初始价格向量, shape (n_assets,)
            mu          : 年化漂移率向量
            sigma       : 年化波动率向量
            corr_matrix : 相关系数矩阵, shape (n_assets, n_assets)
            T           : 终止时间（年）
            n_steps     : 时间步数
            n_paths     : 路径数
            seed        : 随机种子

        Returns:
            paths: shape (n_paths, n_assets, n_steps + 1)
        """
        if seed is not None:
            np.random.seed(seed)

        n_assets = len(s0)
        dt = T / n_steps
        
        # Cholesky 分解: L @ L.T = Correlation
        L = np.linalg.cholesky(corr_matrix)
        
        paths = np.zeros((n_paths, n_assets, n_steps + 1))
        paths[:, :, 0] = s0

        for i in range(n_steps):
            # 生成独立标准正态分布 Z ~ N(0, 1)
            Z = np.random.standard_normal((n_paths, n_assets))
            
            # 转化为相关增量 ε = Z @ L.T
            epsilon = Z @ L.T
            
            # 伊藤漂移修正
            drift = (mu - 0.5 * sigma**2) * dt
            diffusion = sigma * np.sqrt(dt) * epsilon
            
            # 更新价格: S_{t+dt} = S_t * exp(drift + diffusion)
            paths[:, :, i + 1] = paths[:, :, i] * np.exp(drift + diffusion)

        return paths

    @staticmethod
    def simulate_merton_jump_diffusion(
        s0: float,
        mu: float,
        sigma: float,
        lambda_j: float,
        mu_j: float,
        sigma_j: float,
        T: float = 1.0,
        n_steps: int = 252,
        n_paths: int = 1000,
        seed: Optional[int] = None
    ) -> np.ndarray:
        """
        默顿跳跃扩散模型 (Merton Jump-Diffusion)
        
        dS/S = (μ - λk)dt + σdW + (J - 1)dN
        其中 N 是强度为 λ 的泊松过程，ln(J) ~ N(μ_j, σ_j²)
        k = E[J - 1] = exp(μ_j + 0.5σ_j²) - 1

        Args:
            lambda_j: 跳跃强度（每年平均跳跃次数）
            mu_j    : 跳跃幅度的对数均值
            sigma_j : 跳跃幅度的对数标准差
        """
        if seed is not None:
            np.random.seed(seed)

        dt = T / n_steps
        k = np.exp(mu_j + 0.5 * sigma_j**2) - 1
        
        paths = np.zeros((n_paths, n_steps + 1))
        paths[:, 0] = s0

        for i in range(n_steps):
            # 扩散部分 (Diffusion)
            Z = np.random.standard_normal(n_paths)
            drift = (mu - 0.5 * sigma**2 - lambda_j * k) * dt
            diffusion = sigma * np.sqrt(dt) * Z
            
            # 跳跃部分 (Jump)
            # 1. 产生泊松分布的跳跃次数 (通常 dt 很小，N 主要是 0 或 1)
            N = np.random.poisson(lambda_j * dt, n_paths)
            
            # 2. 对每个跳跃计算幅度
            jump_factor = np.ones(n_paths)
            for path_idx in range(n_paths):
                if N[path_idx] > 0:
                    # 总跳跃幅度 = Σ ln(J_i)
                    total_jump_log = np.random.normal(mu_j, sigma_j, N[path_idx]).sum()
                    jump_factor[path_idx] = np.exp(total_jump_log)
            
            # 更新价格
            paths[:, i + 1] = paths[:, i] * np.exp(drift + diffusion) * jump_factor

        return paths

    @staticmethod
    def simulate_heston(
        s0: float,
        v0: float,
        mu: float,
        kappa: float,
        theta: float,
        sigma_v: float,
        rho: float,
        T: float = 1.0,
        n_steps: int = 252,
        n_paths: int = 1000,
        seed: Optional[int] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Heston 随机波动率模型
        
        dS = μS dt + √v S dW1
        dv = κ(θ - v)dt + σ_v √v dW2
        E[dW1 dW2] = ρ dt

        Args:
            v0     : 初始方差 (Initial Variance)
            kappa  : 均值回归速度 (Mean Reversion Speed)
            theta  : 长期平均方差 (Long-term Variance)
            sigma_v: 波动率的波动率 (Vol of Vol)
            rho    : 价格与波动率的相关性
        """
        if seed is not None:
            np.random.seed(seed)

        dt = T / n_steps
        
        s_paths = np.zeros((n_paths, n_steps + 1))
        v_paths = np.zeros((n_paths, n_steps + 1))
        s_paths[:, 0] = s0
        v_paths[:, 0] = v0

        for i in range(n_steps):
            # 生成相关随机变量
            Z1 = np.random.standard_normal(n_paths)
            Z2 = np.random.standard_normal(n_paths)
            W1 = Z1
            W2 = rho * Z1 + np.sqrt(1 - rho**2) * Z2
            
            v_curr = v_paths[:, i]
            # 保证方差非负 (使用 Full Truncation 或 Reflection)
            v_plus = np.maximum(v_curr, 0)
            
            # 更新方差 (Euler-Maruyama)
            v_paths[:, i + 1] = v_curr + kappa * (theta - v_plus) * dt + \
                               sigma_v * np.sqrt(v_plus * dt) * W2
            
            # 更新价格
            s_paths[:, i + 1] = s_paths[:, i] * np.exp(
                (mu - 0.5 * v_plus) * dt + np.sqrt(v_plus * dt) * W1
            )

        return s_paths, v_paths
