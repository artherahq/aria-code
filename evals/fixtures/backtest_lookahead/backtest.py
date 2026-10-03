"""Moving-average crossover backtest over prices.csv.

Long when the 5-day average of closes is above the 30-day average, flat
otherwise. Day t earns position[t] × (close[t] / close[t-1] − 1).
"""

import csv
from pathlib import Path

PRICES = Path(__file__).with_name("prices.csv")
FAST = 5
SLOW = 30


def load_closes(path=PRICES):
    with open(path, newline="") as handle:
        return [float(row["close"]) for row in csv.DictReader(handle)]


def _mean(values):
    return sum(values) / len(values)


def positions(closes, fast=FAST, slow=SLOW):
    """The position held on each day: 1 long, 0 flat."""
    held = []
    for t in range(len(closes)):
        window = closes[: t + 1]
        if len(window) < slow:
            held.append(0)
            continue
        held.append(1 if _mean(window[-fast:]) > _mean(window[-slow:]) else 0)
    return held


def total_return(closes, fast=FAST, slow=SLOW):
    held = positions(closes, fast, slow)
    equity = 1.0
    for t in range(1, len(closes)):
        equity *= 1 + held[t] * (closes[t] / closes[t - 1] - 1)
    return equity - 1


if __name__ == "__main__":
    print(f"total return: {total_return(load_closes()):.1%}")
