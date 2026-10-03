"""RSI must not go missing when pandas_ta does.

The native binary leaves pandas_ta out (numba/llvmlite made the Linux npm
package too large to publish), and on a Python numba has no wheel for it is
installed but cannot import. Both used to report rsi_14 = None, because the
manual fallback ran only when pandas_ta was not installed at all.
"""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np
import pandas as pd

from aria_code.tools import local_finance_tools as lft


def _history() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, 120))
    return pd.DataFrame({"Close": close, "Volume": rng.integers(1e5, 1e6, 120)})


class RsiFallsBackToTheManualCalculation(unittest.TestCase):
    def factors(self, ta) -> dict:
        ticker = mock.Mock()
        ticker.history.return_value = _history()
        with mock.patch.object(lft, "_HAS_CLOUD", False), \
             mock.patch.object(lft, "_HAS_YF", True), \
             mock.patch.object(lft, "_HAS_TA", True), \
             mock.patch.object(lft, "yf", mock.Mock(Ticker=mock.Mock(return_value=ticker)), create=True), \
             mock.patch.object(lft, "_get_market_data", return_value={"success": True}), \
             mock.patch.object(lft, "_get_pandas_ta", return_value=ta):
            return lft._calculate_factors({"symbol": "AAPL"})

    def test_installed_but_unimportable_pandas_ta_still_gives_rsi(self) -> None:
        rsi = self.factors(ta=None).get("rsi_14")
        self.assertIsNotNone(rsi)
        self.assertGreaterEqual(rsi, 0)
        self.assertLessEqual(rsi, 100)

    def test_pandas_ta_is_still_used_when_it_imports(self) -> None:
        ta = mock.Mock()
        ta.rsi.return_value = pd.Series([42.0])
        self.assertEqual(self.factors(ta=ta)["rsi_14"], 42.0)


if __name__ == "__main__":
    unittest.main()
