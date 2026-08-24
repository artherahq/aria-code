"""Skills 目录 —— 从 aria_cli.py 抽出的纯数据。

SKILLS 字面量原本在 aria_cli.py 的 1772-2035 行（264 行）。同 model_catalog：
纯字面量、零耦合，用普通 import 即可，core_cmds / diagnostic_cmds 的裸名
引用不受影响。
"""

from __future__ import annotations

SKILLS = [
    {
        "command": "/morning-brief",
        "name": "Morning Brief",
        "category": "research",
        "description": "Daily market briefing with key events and outlook",
        "args": "[focus_area]",
        "prompt": (
            "Generate a comprehensive morning market briefing:\n"
            "1. US market futures and overnight moves\n"
            "2. Key economic events and earnings today\n"
            "3. Global markets overview (Asia, Europe)\n"
            "4. Top sector movers and themes\n"
            "5. Trading outlook and key levels to watch\n"
            "{extra}"
        ),
        "tools_hint": ["web_search", "get_market_indices", "get_sector_performance", "analyze_news"],
    },
    {
        "command": "/deep-analysis",
        "name": "Deep Analysis",
        "category": "analysis",
        "description": "Multi-factor stock deep dive (technical + fundamental + sentiment)",
        "args": "<symbol>",
        "prompt": (
            "Perform a comprehensive multi-factor analysis of {symbol}:\n"
            "1. Technical Analysis: trend, support/resistance, indicators (RSI, MACD, Bollinger)\n"
            "2. Fundamental Analysis: PE, PB, revenue growth, margins, debt ratios\n"
            "3. Sentiment Analysis: recent news sentiment, analyst ratings, social buzz\n"
            "4. Risk Assessment: VaR, beta, max drawdown potential\n"
            "5. Verdict: Bull/Bear/Neutral with confidence level and price targets"
        ),
        "tools_hint": ["web_search", "get_market_data", "calculate_factors", "analyze_news", "peer_comparison", "piotroski_fscore", "get_risk_metrics"],
    },
    {
        "command": "/trade-idea",
        "name": "Trade Idea",
        "category": "strategy",
        "description": "AI-generated trade ideas with entry/exit levels",
        "args": "[market_or_sector]",
        "prompt": (
            "Generate 3 actionable trade ideas{context}:\n"
            "For each idea provide:\n"
            "1. Symbol and direction (Long/Short)\n"
            "2. Entry zone, stop loss, and 2 take-profit levels\n"
            "3. Risk-reward ratio\n"
            "4. Catalyst: what's driving the trade\n"
            "5. Timeframe (swing/position/day)\n"
            "6. Confidence level (1-10)"
        ),
        "tools_hint": ["web_search", "get_market_data", "analyze_news", "recommend_strategy"],
    },
    {
        "command": "/risk-report",
        "name": "Risk Report",
        "category": "risk",
        "description": "Portfolio risk analysis with VaR, stress tests, and correlation",
        "args": "[symbols...]",
        "prompt": (
            "Generate a comprehensive risk report for portfolio: {symbols}\n"
            "1. Portfolio VaR (95%, 99%) — daily and monthly\n"
            "2. Correlation matrix between holdings\n"
            "3. Concentration risk by sector/geography\n"
            "4. Stress test scenarios (2008 crisis, COVID crash, rate hike)\n"
            "5. Tail risk analysis\n"
            "6. Recommendations: rebalancing suggestions to reduce risk"
        ),
        "tools_hint": ["assess_portfolio_risk", "get_risk_metrics", "stress_test_strategy"],
    },
    {
        "command": "/sector-rotation",
        "name": "Sector Rotation",
        "category": "strategy",
        "description": "Sector rotation analysis with economic cycle positioning",
        "args": "",
        "prompt": (
            "Analyze current sector rotation dynamics:\n"
            "1. Current economic cycle phase (early/mid/late/recession)\n"
            "2. All 11 GICS sectors: performance, momentum, relative strength\n"
            "3. Leading vs lagging sectors and why\n"
            "4. Sector rotation strategy: which sectors to overweight/underweight\n"
            "5. Top stock picks from the strongest sectors\n"
            "6. Historical analog: which past period is most similar"
        ),
        "tools_hint": ["get_sector_performance", "get_market_indices", "analyze_news"],
    },
    {
        "command": "/macro-outlook",
        "name": "Macro Outlook",
        "category": "research",
        "description": "Macroeconomic analysis: rates, inflation, growth & cycle",
        "args": "[region]",
        "prompt": (
            "Provide a macroeconomic outlook{context}:\n"
            "1. GDP growth forecast and trends\n"
            "2. Inflation trajectory (CPI, PCE) and central bank response\n"
            "3. Interest rate path: current level and expectations\n"
            "4. Employment situation: jobs, wages, participation\n"
            "5. Key risks: geopolitical, financial, systemic\n"
            "6. Asset class implications: equities, bonds, commodities, crypto"
        ),
        "tools_hint": ["web_search", "get_world_bank_reports", "get_bonds_data", "analyze_news"],
    },
    {
        "command": "/factor-screen",
        "name": "Factor Screen",
        "category": "quant",
        "description": "Factor-based stock screening (value, momentum, quality, etc.)",
        "args": "<factor_type>",
        "prompt": (
            "Screen US stocks using {factor} factor strategy:\n"
            "1. Define the factor criteria and thresholds\n"
            "2. Top 10 stocks ranking highest on {factor}\n"
            "3. For each: symbol, score, key metrics, sector\n"
            "4. Historical factor performance: how has {factor} performed\n"
            "5. Current factor environment: is {factor} in favor?\n"
            "6. Combined multi-factor overlay suggestion"
        ),
        "tools_hint": ["calculate_factors", "get_alpha158_factors", "get_market_data"],
    },
    {
        "command": "/crypto-scan",
        "name": "Crypto Scanner",
        "category": "crypto",
        "description": "Cryptocurrency market scan with top movers and DeFi trends",
        "args": "[focus]",
        "prompt": (
            "Scan the cryptocurrency market:\n"
            "1. BTC and ETH: price, trend, dominance, key levels\n"
            "2. Top 5 gainers and top 5 losers (24h)\n"
            "3. Market sentiment: Fear & Greed index, funding rates\n"
            "4. DeFi and Layer-2 highlights\n"
            "5. Upcoming catalysts: halvings, upgrades, token unlocks\n"
            "6. Trading opportunities with risk levels\n"
            "{extra}"
        ),
        "tools_hint": ["get_crypto_data", "analyze_news"],
    },
    {
        "command": "/backtest-report",
        "name": "Backtest Report",
        "category": "quant",
        "description": "Run and analyze a strategy backtest with detailed metrics",
        "args": "<strategy> <symbol> [start] [end]",
        "prompt": (
            "Run a detailed backtest of '{strategy}' strategy on {symbol} from {start} to {end}:\n"
            "1. Performance summary: total return, annualized, Sharpe, Sortino\n"
            "2. Risk metrics: max drawdown, VaR, downside deviation\n"
            "3. Trade analysis: win rate, avg win/loss, profit factor\n"
            "4. Monthly returns breakdown\n"
            "5. Comparison vs buy-and-hold and benchmark (SPY)\n"
            "6. Optimization suggestions: parameter sensitivity"
        ),
        "tools_hint": ["backtest_strategy", "get_market_data", "get_risk_metrics"],
    },
    {
        "command": "/watchlist-scan",
        "name": "Watchlist Scan",
        "category": "tools",
        "description": "Scan all watchlist stocks for signals and alerts",
        "args": "",
        "prompt": (
            "Scan my watchlist ({symbols}) and for each stock provide:\n"
            "1. Current price and daily change\n"
            "2. Technical signal: Buy/Sell/Hold based on key indicators\n"
            "3. Any earnings or events upcoming\n"
            "4. News sentiment (positive/neutral/negative)\n"
            "5. Overall alert level: Green/Yellow/Red\n"
            "Sort by urgency of action needed."
        ),
        "tools_hint": ["get_market_data", "analyze_news"],
    },
    {
        "command": "/gen-strategy",
        "name": "Generate Strategy Code",
        "category": "code",
        "description": "Generate complete Python trading strategy code",
        "args": "<strategy_type> [symbol]",
        "prompt": (
            "Generate a complete, production-ready Python backtrader trading strategy.\n"
            "Strategy type: {strategy}\n"
            "Target symbol: {symbol}\n\n"
            "Requirements:\n"
            "1. Full backtrader Strategy class with __init__, next, notify_order\n"
            "2. Proper indicator initialization (use bt.indicators)\n"
            "3. Entry/exit logic with clear conditions\n"
            "4. Position sizing (percent sizer or fixed)\n"
            "5. Risk management: stop-loss and take-profit\n"
            "6. Logging via self.log()\n"
            "7. Complete cerebro setup code at the bottom\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "Include inline comments explaining the logic."
        ),
        "tools_hint": ["recommend_strategy", "backtest_strategy"],
    },
    {
        "command": "/gen-analysis",
        "name": "Generate Analysis Script",
        "category": "code",
        "description": "Generate a Python analysis/visualization script",
        "args": "<topic> [symbols...]",
        "prompt": (
            "Generate a Python script for financial analysis and visualization.\n"
            "Topic: {topic}\n"
            "Symbols: {symbols}\n\n"
            "Requirements:\n"
            "1. Use pandas, numpy, matplotlib/plotly, yfinance\n"
            "2. Fetch real market data with yfinance\n"
            "3. Compute relevant metrics/indicators\n"
            "4. Create informative charts/plots\n"
            "5. Print a summary table of key findings\n"
            "6. Include error handling for data fetching\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "Include inline comments."
        ),
        "tools_hint": ["get_market_data", "calculate_factors"],
    },
    {
        "command": "/gen-bot",
        "name": "Generate Trading Bot",
        "category": "code",
        "description": "Generate live trading bot with exchange API (ccxt)",
        "args": "<exchange> <strategy>",
        "prompt": (
            "Generate a Python trading bot for live execution.\n"
            "Exchange: {exchange}\n"
            "Strategy: {strategy}\n\n"
            "Requirements:\n"
            "1. Use ccxt library for exchange connection\n"
            "2. Market data fetching and order execution\n"
            "3. Signal generation based on the strategy logic\n"
            "4. Risk management: max position size, daily loss limit\n"
            "5. Logging with timestamps\n"
            "6. Graceful shutdown handling (SIGINT)\n"
            "7. Configuration via environment variables (API keys)\n"
            "8. Paper trading mode toggle\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "NEVER include actual API keys. Use env vars."
        ),
        "tools_hint": ["recommend_strategy"],
    },
    {
        "command": "/train-status",
        "name": "Training Status",
        "category": "tools",
        "description": "Check Aria model training and data pipeline status",
        "args": "",
        "prompt": (
            "Check the current Aria model training status.\n"
            "1. Locate the project root via the ARIA_PROJECT_ROOT environment variable, or "
            "search upward from the current directory for a 'packages/ml/llm/training' folder.\n"
            "2. List checkpoint directories inside 'packages/ml/llm/training/outputs/' "
            "(any subdirectory containing 'trainer_state.json').\n"
            "3. Read the latest checkpoint's trainer_state.json: report current step, "
            "total steps, epoch, eval_loss, and best_model_checkpoint.\n"
            "4. Check for model_versions.json in the training outputs and report the "
            "currently deployed version if present.\n"
            "5. List recent training data files under 'data/training/' (newest 5 files).\n"
            "Summarize: training progress (step/total, %), eval_loss trend, "
            "deployed version, and data pipeline status."
        ),
        "tools_hint": ["read_file", "list_files"],
    },
]
