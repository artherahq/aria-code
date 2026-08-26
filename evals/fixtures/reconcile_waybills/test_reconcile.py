"""The billing rules this carrier contract actually specifies.

Expected total = base_rate * (1 + fuel_pct), rounded to cents.

Under that rule three of the four waybills are billed correctly and one is
overbilled — the reconciliation must find exactly that one.
"""

from reconcile import expected_total, find_discrepancies


def test_expected_total_includes_the_fuel_surcharge():
    row = {"base_rate": "86.00", "fuel_pct": "0.14"}
    assert round(expected_total(row), 2) == 98.04


def test_finds_only_the_genuinely_overbilled_waybill():
    found = find_discrepancies()
    assert [d["waybill"] for d in found] == ["1Z999AA10123456784"]


def test_reports_the_size_of_the_overbill():
    (only,) = find_discrepancies()
    assert only["expected"] == 249.90
    assert only["billed"] == 265.50
    assert only["delta"] == 15.60


def test_correctly_billed_waybills_are_not_flagged():
    flagged = {d["waybill"] for d in find_discrepancies()}
    assert "SF1234567890123" not in flagged
    assert "SF1234567890124" not in flagged
    assert "1Z999AA10123456785" not in flagged
