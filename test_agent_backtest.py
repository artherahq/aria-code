import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent / 'packages'))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from quant_engine.backtest import BacktestEngine

def run_test():
    # 1. Generate fake price data (1 year)
    dates = pd.date_range(start="2023-01-01", end="2023-12-31", freq='B')
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, len(dates))
    prices = 150.0 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({'close': prices}, index=dates)

    # 2. Generate some mock agent signals
    signals = [
        {'date': '2023-01-15', 'signal': 'BUY', 'confidence': 0.8, 'symbol': 'AAPL'},
        {'date': '2023-03-20', 'signal': 'BUY', 'confidence': 0.5, 'symbol': 'AAPL'},
        {'date': '2023-06-10', 'signal': 'SELL', 'confidence': 0.6, 'symbol': 'AAPL'},
        {'date': '2023-09-05', 'signal': 'BUY', 'confidence': 0.9, 'symbol': 'AAPL'},
        {'date': '2023-11-20', 'signal': 'SELL', 'confidence': 1.0, 'symbol': 'AAPL'}
    ]

    # 3. Run backtest
    engine = BacktestEngine(initial_cash=100000.0)
    print("Running Backtest...")
    equity_curve = engine.run_signals(df, signals)

    # 4. Show metrics
    metrics = engine.calculate_metrics(equity_curve)
    print("\n=== Backtest Metrics ===")
    for k, v in metrics.items():
        print(f"{k}: {v}")

    print("\n=== Final Portfolio ===")
    print(f"Cash: ${engine.portfolio.cash:,.2f}")
    for sym, pos in engine.portfolio.positions.items():
        print(f"Position {sym}: {pos.quantity} shares @ {pos.avg_price:.2f}")

if __name__ == "__main__":
    run_test()
