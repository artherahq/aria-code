"""A backtest may only trade on what was known before the trade.

The position held over day t earns close[t-1] → close[t], so it has to be
decided from closes up to and including close[t-1]. A signal that sees
close[t] is trading on the return it is about to be paid — on this random
walk that is the difference between +12% and +32%.
"""

import math

from backtest import load_closes, positions, total_return


def test_a_position_does_not_depend_on_that_days_close():
    closes = load_closes()
    base = positions(closes)
    for t in range(40, len(closes), 37):
        future = closes[:t] + [c * (3.0 if (i % 2) else 0.3) for i, c in enumerate(closes[t:])]
        changed = positions(future)
        assert changed[: t + 1] == base[: t + 1], f"position on day {t} moved when only day {t} onward changed"


def test_the_strategy_still_trades():
    held = positions(load_closes())
    assert 100 < sum(held) < len(held) - 100, "fixing look-ahead does not mean never holding a position"


def test_return_without_look_ahead():
    assert math.isclose(total_return(load_closes()), 0.1204, abs_tol=0.0005)
