import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1] / 'packages'))

import pandas as pd
import numpy as np
from quant_engine.portfolio import PortfolioOptimizer

def demo_portfolio_optimizer():
    # 1. Create fake data for 3 stocks
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", "2023-12-31", freq='B')

    # AAPL: strong return, medium vol
    aapl = np.random.normal(0.001, 0.02, len(dates))
    # MSFT: steady return, low vol
    msft = np.random.normal(0.0005, 0.015, len(dates))
    # TSLA: high return, extreme vol
    tsla = np.random.normal(0.0015, 0.04, len(dates))

    prices_df = pd.DataFrame({
        'AAPL': 150 * np.exp(np.cumsum(aapl)),
        'MSFT': 250 * np.exp(np.cumsum(msft)),
        'TSLA': 200 * np.exp(np.cumsum(tsla))
    }, index=dates)

    opt = PortfolioOptimizer()

    print("--- 传统量化模型 (Markowitz) ---")
    print("Min Variance Weights:", opt.min_variance(prices_df))
    print("Max Sharpe Weights:  ", opt.max_sharpe(prices_df))
    print("Risk Parity Weights: ", opt.risk_parity(prices_df))

    print("\n--- Agent 信号融合层 (Agent Confidence Overlay) ---")
    max_sharpe_w = opt.max_sharpe(prices_df)

    # Agent 给出非常低迷的信心 (熊市/有风险)
    agent_confidence_bear = {'AAPL': 0.3, 'MSFT': 0.8, 'TSLA': 0.1}
    print("熊市 Agent 情绪:", agent_confidence_bear)
    print("融合后实际仓位:  ", opt.combine_agent_signals(max_sharpe_w, agent_confidence_bear))

    # Agent 给出强劲买入信心 (牛市主升浪)
    agent_confidence_bull = {'AAPL': 1.0, 'MSFT': 0.9, 'TSLA': 1.0}
    print("\n牛市 Agent 情绪:", agent_confidence_bull)
    print("融合后实际仓位:  ", opt.combine_agent_signals(max_sharpe_w, agent_confidence_bull))

if __name__ == "__main__":
    demo_portfolio_optimizer()
