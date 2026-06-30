"""Regression guard for the bundled A–D point-in-time harness.

The skill instructs the agent to *run* information_set_compare.py, so its output
contract matters as much as its math. These tests lock in three properties that
were verified by hand and are easy to silently regress:

  1. --demo runs cleanly — no numpy RuntimeWarning flood (degenerate-factor IC).
  2. The documented CSV interface (--facts/--prices) is byte-identical to the
     in-memory --demo path — date parsing, empty period_start, ingestion.
  3. The look-ahead leak is actually detected: the earnings factor's A–D alpha
     gap is large and significant, while a no-leak factor shows ~no gap.

Pandas/numpy are optional in this repo; skip cleanly where they are absent.
"""
import io
import warnings
from contextlib import redirect_stdout

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

import information_set_compare as m  # noqa: E402  (after importorskip)


def _capture_run(facts, prices):
    buf = io.StringIO()
    with redirect_stdout(buf):
        m.print_tables(*m.run(facts, prices))
    return buf.getvalue()


def test_demo_emits_no_runtime_warnings():
    facts, prices = m.make_demo()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        # Must not raise: a degenerate-factor IC used to divide by a zero stddev.
        _capture_run(facts, prices)


def test_csv_path_matches_in_memory(tmp_path):
    facts, prices = m.make_demo()
    in_memory = _capture_run(facts, prices)

    facts_csv = tmp_path / "facts.csv"
    prices_csv = tmp_path / "prices.csv"
    facts.to_csv(facts_csv, index=False)
    prices.to_csv(prices_csv)

    facts2 = pd.read_csv(facts_csv)
    prices2 = pd.read_csv(prices_csv, index_col=0)
    from_csv = _capture_run(facts2, prices2)

    assert from_csv == in_memory, "documented CSV interface diverged from --demo"


def test_lookahead_is_detected_and_no_false_positive():
    facts, prices = m.make_demo()
    t1, t2, *_ = m.run(facts, prices)

    # Injected leak: earnings factor must show a large, significant A–D gap.
    ec = t2["earnings_change"]
    assert ec["da_AD"] > 50.0, f"look-ahead under-detected: A-D={ec['da_AD']:.1f}"
    assert ec["q"] < 0.01, f"look-ahead not significant: q={ec['q']:.3f}"

    # No leak tied to revenue growth: A–D gap must be ~0 (no false positive).
    rg = t2["revenue_growth"]
    assert abs(rg["da_AD"]) < 5.0, f"false-positive leak on revenue_growth: A-D={rg['da_AD']:.1f}"


def test_earnings_alpha_collapses_under_pit():
    t1, *_ = m.run(*m.make_demo())
    naive = t1[("earnings_change", "A")]   # period-end dated, revised values
    strict = t1[("earnings_change", "D")]  # tradable PIT
    assert naive["alpha"] > 40.0, "naive variant should show inflated alpha"
    assert abs(strict["alpha"]) < 10.0, "strict PIT alpha should largely collapse"
