"""
伊藤积分与伊藤引理实现
Itô's Lemma and Itô Stochastic Integral

理论基础：
  设 X(t) 为伊藤过程：dX = μ(X,t)dt + σ(X,t)dW
  对光滑函数 f(X,t)，伊藤引理给出：
  df = (∂f/∂t + μ·∂f/∂X + ½σ²·∂²f/∂X²)dt + σ·∂f/∂X·dW

关键应用：
  1. GBM → S(t) = S₀·exp((μ - σ²/2)t + σW(t))
  2. Black-Scholes PDE推导
  3. Feynman-Kac定理（PDE ↔ 期望）
  4. 测度变换（Girsanov定理）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple, List
import warnings


@dataclass
class ItoProcess:
    """
    伊藤过程：dX = μ(X,t)dt + σ(X,t)dW

    参数：
        mu_func  : 漂移系数函数 μ(x, t) -> float
        sigma_func: 扩散系数函数 σ(x, t) -> float
        x0       : 初始值
        name     : 过程名称
    """
    mu_func: Callable[[float, float], float]
    sigma_func: Callable[[float, float], float]
    x0: float = 1.0
    name: str = "ItoProcess"

    def simulate_path(
        self,
        T: float = 1.0,
        n_steps: int = 252,
        seed: Optional[int] = None,
        method: str = "euler_maruyama"
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        模拟伊藤过程路径

        Args:
            T      : 终止时间（年）
            n_steps: 时间步数
            seed   : 随机种子
            method : "euler_maruyama"（一阶）或 "milstein"（带二阶修正）

        Returns:
            (t_grid, X_path): 时间网格和过程路径
        """
        if seed is not None:
            np.random.seed(seed)

        dt = T / n_steps
        sqrt_dt = np.sqrt(dt)
        t_grid = np.linspace(0, T, n_steps + 1)
        X = np.zeros(n_steps + 1)
        X[0] = self.x0

        dW = np.random.normal(0, sqrt_dt, n_steps)

        for i in range(n_steps):
            x_i = X[i]
            t_i = t_grid[i]
            mu_i = self.mu_func(x_i, t_i)
            sigma_i = self.sigma_func(x_i, t_i)

            if method == "euler_maruyama":
                # Euler-Maruyama：X_{n+1} = X_n + μ·Δt + σ·ΔW
                X[i + 1] = x_i + mu_i * dt + sigma_i * dW[i]

            elif method == "milstein":
                # Milstein 方法：加入二阶修正项 ½σ·σ'·((ΔW)² - Δt)
                # σ' ≈ (σ(x+δ,t) - σ(x-δ,t)) / 2δ（数值微分）
                delta = x_i * 1e-5 if x_i != 0 else 1e-5
                sigma_prime = (self.sigma_func(x_i + delta, t_i) -
                               self.sigma_func(x_i - delta, t_i)) / (2 * delta)
                milstein_correction = 0.5 * sigma_i * sigma_prime * (dW[i] ** 2 - dt)
                X[i + 1] = x_i + mu_i * dt + sigma_i * dW[i] + milstein_correction

            else:
                raise ValueError(f"Unknown method: {method}")

        return t_grid, X

    def simulate_ensemble(
        self,
        T: float = 1.0,
        n_steps: int = 252,
        n_paths: int = 1000,
        method: str = "euler_maruyama",
        antithetic: bool = False,
    ) -> np.ndarray:
        """
        模拟多条路径（支持对偶变量方差缩减）

        Returns:
            paths: shape (n_paths, n_steps+1)
        """
        dt = T / n_steps
        sqrt_dt = np.sqrt(dt)
        paths = np.zeros((n_paths, n_steps + 1))
        paths[:, 0] = self.x0

        if antithetic:
            half = n_paths // 2
            dW_base = np.random.normal(0, sqrt_dt, (half, n_steps))
            dW_all = np.vstack([dW_base, -dW_base])  # 对偶变量
        else:
            dW_all = np.random.normal(0, sqrt_dt, (n_paths, n_steps))

        t_grid = np.linspace(0, T, n_steps + 1)

        for i in range(n_steps):
            x_i = paths[:, i]
            t_i = t_grid[i]
            mu_i = np.vectorize(self.mu_func)(x_i, t_i)
            sigma_i = np.vectorize(self.sigma_func)(x_i, t_i)

            if method == "euler_maruyama":
                paths[:, i + 1] = x_i + mu_i * dt + sigma_i * dW_all[:, i]
            elif method == "milstein":
                delta = np.where(x_i != 0, np.abs(x_i) * 1e-5, 1e-5)
                sig_p = np.vectorize(self.sigma_func)(x_i + delta, t_i)
                sig_m = np.vectorize(self.sigma_func)(x_i - delta, t_i)
                sigma_prime = (sig_p - sig_m) / (2 * delta)
                corr = 0.5 * sigma_i * sigma_prime * (dW_all[:, i] ** 2 - dt)
                paths[:, i + 1] = x_i + mu_i * dt + sigma_i * dW_all[:, i] + corr

        return paths


class ItoCalculus:
    """
    伊藤微积分工具集

    提供：
      1. 伊藤积分（离散近似）
      2. 伊藤引理（数值验证）
      3. 测度变换（Girsanov）
      4. Feynman-Kac 期望定理
    """

    @staticmethod
    def ito_integral(
        integrand: np.ndarray,
        brownian_increments: np.ndarray
    ) -> float:
        """
        伊藤积分：∫₀ᵀ f(t)dW(t) 的离散近似

        使用左端点 Riemann-Stieltjes 求和（伊藤型）：
          I = Σ f(t_i) · ΔW_i

        注意：与 Stratonovich 积分的区别在于使用左端点（非中点）

        Args:
            integrand          : f(t_i) 在各时间节点的值，shape (n,)
            brownian_increments: ΔW_i = W(t_{i+1}) - W(t_i)，shape (n,)

        Returns:
            伊藤积分近似值
        """
        if len(integrand) != len(brownian_increments):
            raise ValueError("integrand and brownian_increments must have same length")
        return float(np.sum(integrand * brownian_increments))

    @staticmethod
    def ito_isometry_check(
        integrand: np.ndarray,
        dt: float,
        n_trials: int = 10000
    ) -> dict:
        """
        验证伊藤等距性：E[（∫f dW）²] = E[∫f² dt] = ∫E[f²]dt

        Args:
            integrand: 确定性被积函数（测试用）
            dt       : 时间步长
            n_trials : 蒙特卡罗验证次数

        Returns:
            {'theoretical': float, 'empirical': float, 'error_pct': float}
        """
        n = len(integrand)
        theoretical = float(np.sum(integrand ** 2) * dt)

        squared_integrals = []
        for _ in range(n_trials):
            dW = np.random.normal(0, np.sqrt(dt), n)
            I = np.sum(integrand * dW)
            squared_integrals.append(I ** 2)

        empirical = float(np.mean(squared_integrals))
        error_pct = abs(empirical - theoretical) / max(abs(theoretical), 1e-10) * 100

        return {
            "theoretical": theoretical,
            "empirical": empirical,
            "error_pct": error_pct,
            "isometry_verified": error_pct < 5.0
        }

    @staticmethod
    def stratonovich_to_ito(
        integrand: np.ndarray,
        sigma_deriv: np.ndarray,
        dt: float
    ) -> np.ndarray:
        """
        Stratonovich 积分转化为伊藤积分

        关系：∫f ∘ dW = ∫f dW + ½∫f'·f dt
        修正项 = ½·σ·(∂σ/∂X)·dt（二次变差修正）

        Args:
            integrand  : Stratonovich 被积函数 f(t_i)
            sigma_deriv: ∂σ/∂X 在各时间节点的值
            dt         : 时间步长

        Returns:
            等价的伊藤积分被积函数
        """
        ito_correction = 0.5 * integrand * sigma_deriv * dt
        return integrand - ito_correction


def apply_ito_lemma(
    f: Callable[[float, float], float],
    df_dt: Callable[[float, float], float],
    df_dx: Callable[[float, float], float],
    d2f_dx2: Callable[[float, float], float],
    mu: Callable[[float, float], float],
    sigma: Callable[[float, float], float],
    x_path: np.ndarray,
    t_grid: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    伊藤引理数值实现

    对伊藤过程 dX = μ(X,t)dt + σ(X,t)dW，
    函数 f(X,t) 满足：
      df = [∂f/∂t + μ·∂f/∂X + ½σ²·∂²f/∂X²]dt + σ·∂f/∂X·dW
           |-----------漂移项（ITO漂移）------------|  |扩散项|

    Args:
        f       : 目标函数 f(x, t)
        df_dt   : ∂f/∂t
        df_dx   : ∂f/∂X（一阶偏导）
        d2f_dx2 : ∂²f/∂X²（二阶偏导）
        mu      : 漂移系数 μ(x, t)
        sigma   : 扩散系数 σ(x, t)
        x_path  : X 的路径，shape (n_steps+1,)
        t_grid  : 时间网格，shape (n_steps+1,)

    Returns:
        (f_path, drift_components, diffusion_components)
        f_path: f(X(t)) 的路径
        drift : 漂移项 dt 系数在各时间点的值
        diffusion: 扩散项 dW 系数在各时间点的值

    示例（GBM 验证）：
        X = S（股价），f = ln(S)
        μ_lnS = μ - σ²/2    ← 伊藤修正项 -σ²/2
        σ_lnS = σ
        → ln S(T) = ln S(0) + (μ - σ²/2)T + σW(T)  ✓
    """
    n = len(x_path)
    f_path = np.zeros(n)
    drift_components = np.zeros(n)
    diffusion_components = np.zeros(n)

    for i in range(n):
        x_i = x_path[i]
        t_i = t_grid[i]
        f_path[i] = f(x_i, t_i)

        mu_i = mu(x_i, t_i)
        sigma_i = sigma(x_i, t_i)

        # 伊藤漂移：∂f/∂t + μ·∂f/∂X + ½σ²·∂²f/∂X²
        drift_components[i] = (
            df_dt(x_i, t_i)
            + mu_i * df_dx(x_i, t_i)
            + 0.5 * sigma_i ** 2 * d2f_dx2(x_i, t_i)
        )
        # 扩散项：σ·∂f/∂X
        diffusion_components[i] = sigma_i * df_dx(x_i, t_i)

    return f_path, drift_components, diffusion_components


class GirsanovTransform:
    """
    Girsanov 测度变换（风险中性测度）

    在风险中性世界中，股价过程从：
      dS = μS dt + σS dW^P  （真实世界）
    变换为：
      dS = rS dt + σS dW^Q  （风险中性世界）

    Girsanov 密度（Radon-Nikodym 导数）：
      dQ/dP = exp(-θ·W(T) - ½θ²·T)
      其中 θ = (μ - r) / σ  （市场价格风险）
    """

    def __init__(self, mu: float, r: float, sigma: float):
        """
        Args:
            mu   : 真实世界漂移率（年化）
            r    : 无风险利率（年化）
            sigma: 波动率（年化）
        """
        self.mu = mu
        self.r = r
        self.sigma = sigma
        self.theta = (mu - r) / sigma  # 市场价格风险

    def radon_nikodym(self, W_T: float, T: float) -> float:
        """
        计算 Radon-Nikodym 导数（测度变换权重）

        dQ/dP|_T = exp(-θ·W(T) - ½θ²·T)
        """
        return np.exp(-self.theta * W_T - 0.5 * self.theta ** 2 * T)

    def real_to_risk_neutral_brownian(
        self,
        W_path: np.ndarray,
        t_grid: np.ndarray
    ) -> np.ndarray:
        """
        将真实世界布朗运动 W^P 转化为风险中性布朗运动 W^Q

        Girsanov 定理：W^Q(t) = W^P(t) + θ·t
        """
        return W_path + self.theta * t_grid

    def price_option_by_measure_change(
        self,
        S0: float,
        K: float,
        T: float,
        n_paths: int = 50000,
        n_steps: int = 252
    ) -> dict:
        """
        通过 Girsanov 测度变换定价欧式看涨期权

        在 Q 测度下：S(T) = S0·exp((r - σ²/2)T + σW^Q(T))
        C = e^{-rT}·E^Q[max(S(T)-K, 0)]

        Returns:
            {'price': float, 'delta': float, 'se': float}
        """
        dt = T / n_steps
        sqrt_dt = np.sqrt(dt)

        # 在 Q 测度下模拟（使用风险中性漂移 r - σ²/2）
        dW = np.random.normal(0, sqrt_dt, (n_paths, n_steps))
        log_returns = (self.r - 0.5 * self.sigma ** 2) * dt + self.sigma * dW
        S_T = S0 * np.exp(np.sum(log_returns, axis=1))

        payoffs = np.maximum(S_T - K, 0)
        discounted = np.exp(-self.r * T) * payoffs

        price = float(np.mean(discounted))
        se = float(np.std(discounted) / np.sqrt(n_paths))

        # 数值 Delta：ΔC/ΔS ≈ (C(S+ε) - C(S-ε)) / 2ε
        eps = S0 * 0.01
        payoffs_up = np.maximum(S_T * (1 + eps / S0) - K, 0)
        payoffs_dn = np.maximum(S_T * (1 - eps / S0) - K, 0)
        delta = float(np.exp(-self.r * T) * np.mean(payoffs_up - payoffs_dn) / (2 * eps))

        return {"price": price, "delta": delta, "standard_error": se}


class FeynmanKac:
    """
    Feynman-Kac 定理：随机过程期望 ↔ PDE

    对于 SDE: dX = μ(X,t)dt + σ(X,t)dW，
    边值问题 u_t + μ·u_x + ½σ²·u_xx - r·u = -g(x,t)
    的解为：u(x,t) = E[∫_t^T e^{-r(s-t)} g(X_s,s)ds + e^{-r(T-t)} Φ(X_T) | X_t=x]

    应用：期权定价（Black-Scholes PDE ↔ 风险中性期望）
    """

    def __init__(
        self,
        mu: Callable,
        sigma: Callable,
        r: float = 0.0
    ):
        self.mu = mu
        self.sigma = sigma
        self.r = r

    def solve_by_mc(
        self,
        x0: float,
        t0: float,
        T: float,
        terminal_condition: Callable[[float], float],
        running_cost: Optional[Callable[[float, float], float]] = None,
        n_paths: int = 50000,
        n_steps: int = 252,
        antithetic: bool = True,
    ) -> dict:
        """
        用蒙特卡罗方法求解 Feynman-Kac 公式

        u(x,t) ≈ (1/N) Σ [e^{-r(T-t)}·Φ(X_T^i) + ∫running_cost dt]

        Args:
            x0                : 初始状态
            t0                : 初始时间
            T                 : 终止时间
            terminal_condition: Φ(X_T) 终端条件（如期权 payoff）
            running_cost      : g(X_t, t) 持续成本（默认为0）
            n_paths           : 路径数
            n_steps           : 时间步数
            antithetic        : 是否使用对偶变量

        Returns:
            {'value': float, 'se': float, 'ci_95': tuple}
        """
        process = ItoProcess(
            mu_func=self.mu,
            sigma_func=self.sigma,
            x0=x0
        )
        tau = T - t0
        paths = process.simulate_ensemble(
            T=tau, n_steps=n_steps, n_paths=n_paths,
            method="euler_maruyama", antithetic=antithetic
        )

        # 终端 payoff
        X_T = paths[:, -1]
        terminal_values = np.array([terminal_condition(x) for x in X_T])
        discounted_terminal = np.exp(-self.r * tau) * terminal_values

        # 运行成本（积分）
        if running_cost is not None:
            t_grid = np.linspace(t0, T, n_steps + 1)
            dt = tau / n_steps
            running_costs = np.zeros(n_paths)
            for j in range(n_steps):
                g_vals = np.array([running_cost(paths[k, j], t_grid[j]) for k in range(n_paths)])
                running_costs += np.exp(-self.r * (t_grid[j] - t0)) * g_vals * dt
            total_values = discounted_terminal + running_costs
        else:
            total_values = discounted_terminal

        value = float(np.mean(total_values))
        se = float(np.std(total_values) / np.sqrt(n_paths))
        ci_lower = value - 1.96 * se
        ci_upper = value + 1.96 * se

        return {
            "value": value,
            "standard_error": se,
            "ci_95": (ci_lower, ci_upper),
            "n_paths": n_paths,
        }
