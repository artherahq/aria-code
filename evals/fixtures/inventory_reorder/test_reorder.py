"""Grades reorder.json against the replenishment policy the prompt states.

    safety stock  = z · √(L·σ_d² + d̄²·σ_L²),  z = Φ⁻¹(0.95)
    reorder point = d̄·L + safety stock
    position      = on_hand + on_order − backorder
    order qty     = ROP + d̄·7 − position, when position ≤ ROP; otherwise 0

Reference values below were computed from skus.csv with the sample standard
deviation. The population standard deviation moves every figure by under 1%,
and rounding up to whole units (which a planner should) moves small ones by a
unit; within 5% or one unit admits both. The traps below miss by far more.

Each SKU is there to catch one mistake:
    A500  open purchase order covers it   — ignoring on_order reorders ~275
    A600  backorders consume the stock    — ignoring backorder reorders 0
    A400  9 days of history               — a number here is a guess
    A700  no movement at all              — reordering dead stock
"""

import json
import math
from pathlib import Path

import pytest

ANSWER = Path(__file__).with_name("reorder.json")

#       safety stock, reorder point, order qty
EXPECTED = {
    "A100": (70.9, 352.9, 334.9),
    "A200": (104.3, 340.1, 245.1),
    "A300": (12.2, 72.1, 0),
    "A500": (37.9, 195.9, 0),
    "A600": (27.4, 116.3, 160.1),
}
TOLERANCE = 0.05
ROUNDING = 1.0


def _answer():
    assert ANSWER.exists(), "reorder.json was not written"
    data = json.loads(ANSWER.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "reorder.json must map SKU -> result"
    return data


def _close(actual, expected):
    if expected == 0:
        return actual == 0
    return math.isclose(float(actual), expected, rel_tol=TOLERANCE, abs_tol=ROUNDING)


def test_every_sku_is_answered():
    assert set(_answer()) == {"A100", "A200", "A300", "A400", "A500", "A600", "A700"}


@pytest.mark.parametrize("sku", sorted(EXPECTED))
def test_policy_numbers(sku):
    item = _answer()[sku]
    safety, rop, qty = EXPECTED[sku]
    assert _close(item["safety_stock"], safety), f"{sku} safety_stock {item['safety_stock']} != ~{safety}"
    assert _close(item["reorder_point"], rop), f"{sku} reorder_point {item['reorder_point']} != ~{rop}"
    assert _close(item["order_qty"], qty), f"{sku} order_qty {item['order_qty']} != ~{qty}"


def test_open_purchase_order_is_counted():
    assert _answer()["A500"]["order_qty"] == 0


def test_backorders_are_counted():
    assert _answer()["A600"]["order_qty"] > 0


def test_thin_history_is_flagged_not_guessed():
    item = _answer()["A400"]
    assert item.get("status") == "insufficient_history"
    for key in ("safety_stock", "reorder_point"):
        assert item.get(key) in (None, ""), f"A400 has only 9 days of history but got {key}={item.get(key)}"
    assert not item.get("order_qty"), "A400 must not get an order quantity from 9 days of history"


def test_dead_stock_is_not_reordered():
    assert not _answer()["A700"].get("order_qty")
