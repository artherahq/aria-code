from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime

@dataclass
class Order:
    symbol: str
    quantity: float
    price: float
    order_type: str = "MARKET"
    direction: str = "BUY"  # BUY or SELL
    status: str = "PENDING"
    timestamp: Optional[datetime] = None

@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0

    @property
    def market_value(self, current_price: float) -> float:
        return self.quantity * current_price

    def update(self, quantity: float, price: float, is_buy: bool):
        if is_buy:
            total_cost = (self.quantity * self.avg_price) + (quantity * price)
            self.quantity += quantity
            self.avg_price = total_cost / self.quantity if self.quantity > 0 else 0
        else:
            self.quantity -= quantity
            if self.quantity <= 0:
                self.avg_price = 0.0
                self.quantity = 0.0

class Portfolio:
    def __init__(self, initial_cash: float = 100000.0):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}
        self.history: List[Dict] = []

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def total_value(self, current_prices: Dict[str, float]) -> float:
        pos_value = sum(
            pos.quantity * current_prices.get(sym, pos.avg_price)
            for sym, pos in self.positions.items()
        )
        return self.cash + pos_value
