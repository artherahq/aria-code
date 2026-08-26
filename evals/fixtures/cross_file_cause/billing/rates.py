"""Rate card. Values are percentages expressed as whole numbers."""

TIER_DISCOUNT = {
    "standard": 0,
    "silver": 5,
    "gold": 12,
    "platinum": 20,
}


def discount_for(tier):
    """Discount for a tier, as a fraction of the subtotal."""
    return TIER_DISCOUNT.get(tier, 0)
