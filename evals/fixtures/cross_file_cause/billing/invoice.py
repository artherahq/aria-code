"""Invoice totals."""

from billing.rates import discount_for


def line_total(unit_price, quantity):
    return round(unit_price * quantity, 2)


def invoice_total(lines, tier="standard"):
    """Subtotal minus the customer's tier discount."""
    subtotal = sum(line_total(p, q) for p, q in lines)
    return round(subtotal * (1 - discount_for(tier)), 2)
