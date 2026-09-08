"""A gold customer is billed a negative amount.

The symptom shows up in invoice_total, but the arithmetic there is right.
"""

from billing.invoice import invoice_total, line_total

LINES = [(10.00, 3), (25.00, 2)]   # subtotal 80.00


def test_line_total():
    assert line_total(10.00, 3) == 30.00


def test_standard_tier_pays_the_subtotal():
    assert invoice_total(LINES, "standard") == 80.00


def test_gold_tier_gets_twelve_percent_off():
    assert invoice_total(LINES, "gold") == 70.40


def test_platinum_tier_gets_twenty_percent_off():
    assert invoice_total(LINES, "platinum") == 64.00


def test_an_unknown_tier_pays_full_price():
    assert invoice_total(LINES, "bronze") == 80.00
