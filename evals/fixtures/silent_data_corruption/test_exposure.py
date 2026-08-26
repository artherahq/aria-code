"""Exposure must be reported in one currency.

Nothing here crashes. The report sums declared values across three currencies
as if they were all dollars, so every lane touching EUR or GBP is understated
and the ranking is wrong — the numbers look plausible and reconcile against
nothing.
"""

from exposure import exposure_by_lane


def test_usd_lane_is_a_plain_sum():
    assert exposure_by_lane()["SHA->LAX"] == 20100.00


def test_eur_lane_is_converted():
    assert exposure_by_lane()["SHA->HAM"] == 16786.00


def test_gbp_lane_is_converted():
    assert exposure_by_lane()["SZX->LHR"] == 66040.00


def test_lanes_are_ranked_by_converted_exposure():
    # Unconverted, SZX->HAM (74000) outranks SZX->LHR (52000). Converted, the
    # order flips — which is the whole point of the report.
    assert list(exposure_by_lane())[:2] == ["SZX->HAM", "SZX->LHR"]


def test_an_unknown_currency_is_rejected():
    rows = [{"origin": "A", "destination": "B", "declared_value": "10", "currency": "XYZ"}]
    try:
        exposure_by_lane(rows)
    except (KeyError, ValueError):
        return
    raise AssertionError("an unrecognised currency must not be silently treated as USD")
