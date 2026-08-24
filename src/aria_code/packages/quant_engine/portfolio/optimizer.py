import numpy as np
import pandas as pd
from scipy.optimize import minimize
from typing import Dict, List, Tuple

class PortfolioOptimizer:
    """Quantitative Portfolio Optimization Engine"""

    def __init__(self, risk_free_rate: float = 0.02):
        self.risk_free_rate = risk_free_rate

    def _get_returns_stats(self, prices_df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        returns = prices_df.pct_change().dropna()
        mean_returns = returns.mean().values * 252
        cov_matrix = returns.cov().values * 252
        return mean_returns, cov_matrix

    def portfolio_performance(self, weights: np.ndarray, mean_returns: np.ndarray, cov_matrix: np.ndarray) -> Tuple[float, float]:
        returns = np.sum(mean_returns * weights)
        std = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
        return returns, std

    def min_variance(self, prices_df: pd.DataFrame) -> Dict[str, float]:
        """Minimize portfolio variance"""
        symbols = prices_df.columns.tolist()
        num_assets = len(symbols)
        _, cov_matrix = self._get_returns_stats(prices_df)

        args = (cov_matrix,)
        def portfolio_volatility(weights, cov):
            return np.sqrt(np.dot(weights.T, np.dot(cov, weights)))

        constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
        bounds = tuple((0.0, 1.0) for asset in range(num_assets))
        initial_guess = num_assets * [1. / num_assets,]

        result = minimize(portfolio_volatility, initial_guess, args=args,
                          method='SLSQP', bounds=bounds, constraints=constraints)

        return {sym: round(weight, 4) for sym, weight in zip(symbols, result.x)}

    def max_sharpe(self, prices_df: pd.DataFrame) -> Dict[str, float]:
        """Maximize Sharpe Ratio"""
        symbols = prices_df.columns.tolist()
        num_assets = len(symbols)
        mean_returns, cov_matrix = self._get_returns_stats(prices_df)

        def neg_sharpe_ratio(weights, mean_ret, cov, rf_rate):
            p_ret, p_std = self.portfolio_performance(weights, mean_ret, cov)
            return -(p_ret - rf_rate) / p_std

        args = (mean_returns, cov_matrix, self.risk_free_rate)
        constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
        bounds = tuple((0.0, 1.0) for asset in range(num_assets))
        initial_guess = num_assets * [1. / num_assets,]

        result = minimize(neg_sharpe_ratio, initial_guess, args=args,
                          method='SLSQP', bounds=bounds, constraints=constraints)

        return {sym: round(weight, 4) for sym, weight in zip(symbols, result.x)}

    def risk_parity(self, prices_df: pd.DataFrame) -> Dict[str, float]:
        """Inverse volatility weighting (naive risk parity)"""
        returns = prices_df.pct_change().dropna()
        volatilities = returns.std() * np.sqrt(252)
        inv_vol = 1.0 / volatilities
        weights = inv_vol / inv_vol.sum()

        return {sym: round(weights[sym], 4) for sym in prices_df.columns}

    def combine_agent_signals(self, optimal_weights: Dict[str, float], agent_confidences: Dict[str, float]) -> Dict[str, float]:
        """
        Adjust optimal weights based on Agent confidence.
        For example, if max_sharpe gives AAPL 0.6, but Agent confidence is 0.5,
        we scale it down and keep remaining in cash.
        """
        final_weights = {}
        for sym, w in optimal_weights.items():
            conf = agent_confidences.get(sym, 1.0)
            final_weights[sym] = round(w * conf, 4)

        cash_weight = round(1.0 - sum(final_weights.values()), 4)
        final_weights['CASH'] = max(0.0, cash_weight)

        return final_weights
