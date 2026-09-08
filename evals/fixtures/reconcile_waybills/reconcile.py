"""Compare carrier invoices against what the waybills say we should be billed."""

import csv
from pathlib import Path

TOLERANCE = 0.01


def load(name):
    with open(Path(__file__).parent / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def expected_total(waybill):
    """What this shipment should cost."""
    return float(waybill["base_rate"])


def find_discrepancies(waybills=None, invoice=None):
    """Return one entry per waybill whose billed total is wrong.

    Each entry: {"waybill": str, "expected": float, "billed": float, "delta": float}
    """
    waybills = waybills if waybills is not None else load("waybills.csv")
    invoice = invoice if invoice is not None else load("invoice.csv")
    billed = {row["waybill"]: float(row["billed_total"]) for row in invoice}

    out = []
    for row in waybills:
        expected = expected_total(row)
        actual = billed.get(row["waybill"])
        if actual is None:
            continue
        if abs(actual - expected) > TOLERANCE:
            out.append({
                "waybill": row["waybill"],
                "expected": round(expected, 2),
                "billed": round(actual, 2),
                "delta": round(actual - expected, 2),
            })
    return out
