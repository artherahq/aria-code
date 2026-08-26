class InsufficientFunds(Exception):
    pass


def transfer(balances, source, target, amount):
    """Move `amount` from source to target. Returns the new balances."""
    balances = dict(balances)
    balances[source] -= amount
    balances[target] = balances.get(target, 0) + amount
    return balances
