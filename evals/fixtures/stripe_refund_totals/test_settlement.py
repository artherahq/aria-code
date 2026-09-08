"""Settlement arithmetic, and the refunds it will happily accept.

The fee model is right and stays right: Stripe does not return the percentage
or the fixed fee when a charge is refunded, so a refund reduces the payout by
the refunded amount only.

What is missing is that `net_settlement` believes every refund it is handed.
A refund against a charge outside this period, or one larger than the charge
it refunds, silently produces a smaller payout number that reconciles against
nothing — the kind of bug that balances on the screen and not in the bank.
"""

from settlement import net_settlement, processing_fee

CHARGES = [
    {"id": "ch_3PqR8s2eZvKYlo2C0aBcDeFg", "amount": 100.00},
    {"id": "ch_3PqR8s2eZvKYlo2C0aBcDeFh", "amount": 250.00},
    {"id": "ch_3PqR8s2eZvKYlo2C0aBcDeFi", "amount": 40.00},
]


def test_fee_is_percent_plus_fixed():
    assert processing_fee(100.00) == 3.20


def test_no_refunds():
    expected = round(390.00 - (3.20 + 7.55 + 1.46), 2)
    assert net_settlement(CHARGES, []) == expected


def test_full_refund_does_not_return_the_fee():
    refunds = [{"id": "re_1", "charge_id": CHARGES[0]["id"], "amount": 100.00}]
    # Gross 390 - all three fees (12.21) - the 100 refunded = 277.79.
    assert net_settlement(CHARGES, refunds) == 277.79


def test_partial_refund():
    refunds = [{"id": "re_2", "charge_id": CHARGES[1]["id"], "amount": 50.00}]
    assert net_settlement(CHARGES, refunds) == 327.79


def test_refund_of_an_unknown_charge_is_rejected(): 
    refunds = [{"id": "re_3", "charge_id": "ch_not_in_this_period", "amount": 10.00}]
    try:
        net_settlement(CHARGES, refunds)
    except ValueError:
        return
    raise AssertionError("a refund against a charge outside the period must be rejected")


def test_refund_cannot_exceed_its_charge():
    refunds = [{"id": "re_4", "charge_id": CHARGES[2]["id"], "amount": 999.00}]
    try:
        net_settlement(CHARGES, refunds)
    except ValueError:
        return
    raise AssertionError("a refund larger than its charge must be rejected")
