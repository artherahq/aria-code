"""CLI command metadata and thin command adapters."""
from .broker_cmds import BrokerCommandsMixin
from .backtest_cmds import BacktestCommandsMixin
from .workspace_cmds import WorkspaceCommandsMixin
from .model_cmds import ModelCommandsMixin
from .market_cmds import MarketCommandsMixin
from .portfolio_cmds import PortfolioCommandsMixin

__all__ = [
    "BrokerCommandsMixin",
    "BacktestCommandsMixin",
    "WorkspaceCommandsMixin",
    "ModelCommandsMixin",
    "MarketCommandsMixin",
    "PortfolioCommandsMixin",
]
