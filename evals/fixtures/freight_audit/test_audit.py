"""Grades audit.json: the overcharges, and only the switches that are safe.

waybills.csv has three lanes, 20 shipments per carrier per lane. The answers
are built in at a size no reasonable method can miss or misread:

    W1007  SF   SHA→PEK  12.40/kg against a lane norm of ~4.0     flag
    W1093  STO  CAN→HGH  10.90/kg against a lane norm of ~3.5     flag
    W1052  YTO  SZX→CTU  400 kg, the largest bill in the file,
                          but 5.05/kg against a norm of ~5.0       do not flag

    SHA→PEK  ZTO is ~25% cheaper than SF but on time 11/20 vs 19/20  no switch
    SZX→CTU  JD is ~16% cheaper than YTO and on time 19/20 vs 15/20  switch

The two traps are the same mistake from opposite directions: judging a
waybill by its total rather than its rate, and recommending the cheaper
carrier without asking what it costs in late deliveries.
"""

import json
import re
from pathlib import Path

ANSWER = Path(__file__).with_name("audit.json")


def _answer():
    assert ANSWER.exists(), "audit.json was not written"
    data = json.loads(ANSWER.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "audit.json must be an object with 'flagged' and 'switches'"
    return data


def _lane(value):
    """'SZX→CTU', 'SZX-CTU', 'szx to ctu' -> ('SZX', 'CTU')."""
    codes = re.findall(r"[A-Za-z]{3}", str(value).replace("to", " "))
    return tuple(c.upper() for c in codes[:2])


def _switches():
    out = set()
    for item in _answer().get("switches") or []:
        out.add((_lane(item["lane"]), str(item["from"]).upper(), str(item["to"]).upper()))
    return out


def test_flags_exactly_the_overcharged_waybills():
    assert set(_answer().get("flagged") or []) == {"W1007", "W1093"}


def test_a_heavy_shipment_at_a_normal_rate_is_not_flagged():
    assert "W1052" not in (_answer().get("flagged") or [])


def test_recommends_the_switch_that_is_cheaper_and_as_reliable():
    assert (("SZX", "CTU"), "YTO", "JD") in _switches()


def test_does_not_trade_reliability_for_price():
    assert not any(lane == ("SHA", "PEK") for lane, _, _ in _switches()), \
        "ZTO is cheaper on SHA→PEK but on time 11/20 against SF's 19/20"


def test_recommends_nothing_else():
    assert len(_switches()) == 1
