"""Insurance exposure by lane, in USD."""

import csv
from pathlib import Path

# Rates as of the reporting date.
FX_TO_USD = {
    "USD": 1.0,
    "EUR": 1.09,
    "GBP": 1.27,
}


def load(name="shipments.csv"):
    with open(Path(__file__).parent / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def exposure_by_lane(rows=None):
    """Total declared value per origin→destination lane, in USD.

    Returns {"SHA->LAX": 20100.0, ...} sorted by descending exposure.
    """
    rows = rows if rows is not None else load()
    totals = {}
    for row in rows:
        lane = f"{row['origin']}->{row['destination']}"
        totals[lane] = totals.get(lane, 0) + float(row["declared_value"])
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))
