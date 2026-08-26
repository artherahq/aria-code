"""Net settlement for a Stripe payout period."""

STRIPE_PERCENT = 0.029
STRIPE_FIXED = 0.30


def processing_fee(amount):
    """Stripe's fee on a charge of `amount`."""
    return round(amount * STRIPE_PERCENT + STRIPE_FIXED, 2)


def net_settlement(charges, refunds):
    """What lands in the bank for this period.

    charges: [{"id": "ch_...", "amount": float}]
    refunds: [{"id": "re_...", "charge_id": "ch_...", "amount": float}]
    """
    gross = sum(c["amount"] for c in charges)
    fees = sum(processing_fee(c["amount"]) for c in charges)
    refunded = sum(r["amount"] for r in refunds)
    return round(gross - fees - refunded, 2)
