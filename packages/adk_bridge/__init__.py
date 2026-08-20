"""Safe integration points between Aria Code services and Google ADK.

The bridge deliberately exports only read-only, bounded tools. Trading, broker
credentials, shell access, and filesystem mutation stay outside the ADK tool
surface and continue to be governed by Aria's existing runtime.
"""

from .code_review_tools import CodeReviewTools
from .market_tools import MarketResearchTools

__all__ = ["CodeReviewTools", "MarketResearchTools"]
