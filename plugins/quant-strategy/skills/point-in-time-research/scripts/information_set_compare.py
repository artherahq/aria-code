#!/usr/bin/env python3
"""
information_set_compare.py — the A–D point-in-time information-set comparison.

Materializes four versions of the SAME factor panel that differ ONLY in the
data-time / data-version definition, holding factor formulas, universe, portfolio
rule and costs fixed. The contrast quantifies how much measured "alpha" is a
look-ahead/version artefact rather than genuine predictability.

  A  Extreme naive : activate at fiscal period_end ; LATEST (possibly revised) value
  B  Date corrected: activate at filing_date       ; LATEST value
  C  Strict SEC PIT: activate at filing_date       ; ORIGINAL as-filed value
  D  Tradable PIT  : next session after filing      ; ORIGINAL value

  A–B = activation-date error · B–C = version contamination · C–D = execution

Inputs (CSV):
  --facts   one row per FILED version (revisions => multiple rows per period):
              symbol, concept, period_end, period_start, filing_date, value
            concept ∈ {REVENUE, NET_INCOME, OPERATING_CASH_FLOW, TOTAL_ASSETS, SHARES}
            dates ISO (YYYY-MM-DD). period_start blank for instantaneous concepts.
  --prices  wide monthly adjusted close: first column `date`, one column per symbol.

Try it with no data:  python information_set_compare.py --demo

Depends only on pandas + numpy. The statistics (Newey–West alpha t, stationary
block bootstrap, Benjamini–Hochberg) are implemented inline — no statsmodels.
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict, namedtuple

import numpy as np
import pandas as pd

REVENUE, NET_INCOME, CFO = "REVENUE", "NET_INCOME", "OPERATING_CASH_FLOW"
TOTAL_ASSETS, SHARES = "TOTAL_ASSETS", "SHARES"
FACTORS = ["earnings_change", "revenue_growth", "profitability", "accruals", "value_composite"]
VARIANTS = ["A", "B", "C", "D"]
QV = namedtuple("QV", "event_time value")


# ───────────────────────── versions: original vs latest ─────────────────────────
def build_versions(facts: pd.DataFrame):
    """-> {symbol: {concept: {ptype: [ver,...]}}}; ver has period_end/start/filing/tradable/original/latest."""
    out = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    facts = facts.dropna(subset=["symbol", "concept", "period_end", "filing_date", "value"])
    for (sym, concept, pend), g in facts.groupby(["symbol", "concept", "period_end"], sort=False):
        g = g.sort_values("filing_date")
        pstart = g["period_start"].iloc[0] if "period_start" in g else None
        pstart = (None if pstart is None or (not isinstance(pstart, str) and pd.isna(pstart))
                  or str(pstart).strip() == "" else pd.Timestamp(pstart).date())
        pend_d = pd.Timestamp(pend).date()
        filing = pd.Timestamp(g["filing_date"].iloc[0]).date()
        tradable = np.busday_offset(np.datetime64(filing, "D"), 1, roll="forward").astype("datetime64[D]").astype(object)
        ptype = _period_type(pstart, pend_d)
        out[sym][concept][ptype].append(dict(
            period_end=pend_d, period_start=pstart, filing=filing, tradable=tradable,
            original=float(g["value"].iloc[0]), latest=float(g["value"].iloc[-1])))
    return out


def _period_type(start, end):
    if start is None:
        return "instant"
    d = (end - start).days
    if 80 <= d <= 100:
        return "quarter"
    if 350 <= d <= 380:
        return "annual"
    if 100 < d < 350:
        return "ytd"
    return "other"


def _activation(variant, ver):
    return {"A": ver["period_end"], "B": ver["filing"], "C": ver["filing"], "D": ver["tradable"]}[variant]


def _value(variant, ver):
    return ver["original"] if variant in ("C", "D") else ver["latest"]


# ───────────────────────── quarterly reconstruction ─────────────────────────
def reconstruct_quarterly(annual, ytd, quarter):
    """Continuous single-quarter series via cumulative differencing (explicit quarters win)."""
    explicit = {r.event_time: r.value for r in quarter if r.value is not None}
    result = dict(explicit)
    by_fy = defaultdict(lambda: {"q": [], "ytd": [], "annual": []})
    for r in quarter:
        by_fy[(r.event_time.year if r.event_time else 0)]["q"].append(r)
    # group cumulatives by fiscal year via period_start year is unavailable here; approximate by end-year
    for r in ytd:
        by_fy[r.event_time.year]["ytd"].append(r)
    for r in annual:
        by_fy[r.event_time.year]["annual"].append(r)
    for fy, g in by_fy.items():
        cum = []
        qs = sorted([r for r in g["q"] if r.value is not None], key=lambda r: r.event_time)
        if qs:
            cum.append((qs[0].event_time, qs[0].value))
        cum += [(r.event_time, r.value) for r in g["ytd"] if r.value is not None]
        cum += [(r.event_time, r.value) for r in g["annual"] if r.value is not None]
        seen, ordered = set(), []
        for t, v in sorted(cum):
            if t not in seen:
                seen.add(t); ordered.append((t, v))
        prev = None
        for t, v in ordered:
            single = v if prev is None else v - prev
            if t not in result:
                result[t] = single
            prev = v
    return [QV(t, result[t]) for t in sorted(result)]


def _admissible(concept_vers, variant, asof):
    recs = []
    for ptype, lst in concept_vers.items():
        for ver in lst:
            if _activation(variant, ver) <= asof:
                recs.append((ptype, QV(ver["period_end"], _value(variant, ver))))
    return recs


def _single_quarters(concept_vers, variant, asof):
    recs = _admissible(concept_vers, variant, asof)
    if not recs:
        return []
    return reconstruct_quarterly([r for p, r in recs if p == "annual"],
                                 [r for p, r in recs if p == "ytd"],
                                 [r for p, r in recs if p == "quarter"])


def _latest_instant(concept_vers, variant, asof):
    best = None
    for ptype, lst in concept_vers.items():
        for ver in lst:
            if _activation(variant, ver) <= asof and (best is None or ver["period_end"] > best["period_end"]):
                best = ver
    return _value(variant, best) if best else None


def _ttm(qv, lag=0):
    if len(qv) < 4 + lag:
        return None
    w = qv[-(4 + lag):len(qv) - lag] if lag else qv[-4:]
    vals = [r.value for r in w]
    if any(v is None for v in vals):
        return None
    for a, b in zip(w, w[1:]):
        if not (80 <= (b.event_time - a.event_time).days <= 105):
            return None
    return float(sum(vals))


def compute_factors(cv, variant, asof, shares_price_val):
    rev_q = _single_quarters(cv.get(REVENUE, {}), variant, asof)
    ni_q = _single_quarters(cv.get(NET_INCOME, {}), variant, asof)
    cfo_q = _single_quarters(cv.get(CFO, {}), variant, asof)
    assets = _latest_instant(cv.get(TOTAL_ASSETS, {}), variant, asof)
    rev_ttm, ni_ttm, cfo_ttm = _ttm(rev_q), _ttm(ni_q), _ttm(cfo_q)
    rev_ttm_1y = _ttm(rev_q, lag=4)
    out = {f: None for f in FACTORS}
    if len(ni_q) >= 8:
        chg = [ni_q[i].value - ni_q[i - 4].value for i in range(4, len(ni_q))
               if ni_q[i].value is not None and ni_q[i - 4].value is not None
               and 350 <= (ni_q[i].event_time - ni_q[i - 4].event_time).days <= 380]
        if len(chg) >= 4:
            s = np.std(chg[-8:])
            out["earnings_change"] = (chg[-1] / s) if s > 0 else None
    if rev_ttm is not None and rev_ttm_1y not in (None, 0):
        out["revenue_growth"] = rev_ttm / rev_ttm_1y - 1.0
    if ni_ttm is not None and assets not in (None, 0):
        out["profitability"] = ni_ttm / assets
    if ni_ttm is not None and cfo_ttm is not None and assets not in (None, 0):
        out["accruals"] = -((ni_ttm - cfo_ttm) / assets)
    if shares_price_val not in (None, 0):
        ep = ni_ttm / shares_price_val if ni_ttm is not None else None
        sp = rev_ttm / shares_price_val if rev_ttm is not None else None
        out["value_composite"] = ("EPSP", ep, sp)
    return out


# ───────────────────────── statistics (tested) ─────────────────────────
def winsor_z(s):
    s = s.clip(s.quantile(0.01), s.quantile(0.99))
    mu, sd = s.mean(), s.std()
    return (s - mu) / sd if sd and sd > 0 else s * 0.0


def spearman_ic(f, r):
    d = pd.concat([f, r], axis=1).dropna()
    if len(d) < 10:
        return np.nan
    rf, rr = d.iloc[:, 0].rank(), d.iloc[:, 1].rank()
    if rf.std() == 0 or rr.std() == 0:
        return np.nan
    return rf.corr(rr)


def nw_alpha_t(y, x, lags=4):
    y, x = np.asarray(y, float), np.asarray(x, float)
    m = ~(np.isnan(y) | np.isnan(x))
    y, x = y[m], x[m]
    n = len(y)
    if n < 12:
        return np.nan, np.nan
    X = np.column_stack([np.ones(n), x])
    XtXi = np.linalg.inv(X.T @ X)
    beta = XtXi @ X.T @ y
    e = y - X @ beta
    S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, lags + 1):
        w = 1.0 - L / (lags + 1)
        G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None])
        S += w * (G + G.T)
    cov = XtXi @ S @ XtXi
    se = math.sqrt(cov[0, 0])
    return beta[0], (beta[0] / se if se > 0 else np.nan)


def block_bootstrap_p(diff, block=6, n=2000, seed=42):
    d = np.asarray(diff, float)
    d = d[~np.isnan(d)]
    if len(d) < 12:
        return np.nan
    rng = np.random.default_rng(seed)
    obs = d.mean()
    boot = np.empty(n)
    for i in range(n):
        idx = []
        while len(idx) < len(d):
            start = rng.integers(0, len(d))
            idx.extend((start + np.arange(rng.geometric(1.0 / block))) % len(d))
        boot[i] = d[np.array(idx[:len(d)])].mean() - obs
    return float((np.abs(boot) >= abs(obs)).mean())


def bh_qvalues(pvals):
    p = np.asarray(pvals, float)
    q = np.full_like(p, np.nan)
    idx = np.where(~np.isnan(p))[0]
    m = len(idx)
    order = idx[np.argsort(p[idx])]
    prev = 1.0
    for rank, j in enumerate(reversed(order), start=1):
        prev = min(prev, p[j] * m / (m - rank + 1))
        q[j] = prev
    return q


# ───────────────────────── main pipeline ─────────────────────────
def run(facts_df, prices, quantile=0.2):
    prices.index = pd.to_datetime(prices.index)
    rets = prices.pct_change().shift(-1)
    vers = build_versions(facts_df)
    tickers = [t for t in prices.columns if t in vers]
    form_dates = list(prices.index)

    ls = {v: {f: {} for f in FACTORS} for v in VARIANTS}
    ic = {v: {f: [] for f in FACTORS} for v in VARIANTS}
    prev_w = {v: {f: {} for f in FACTORS} for v in VARIANTS}
    turn = {v: {f: [] for f in FACTORS} for v in VARIANTS}
    mkt = {}

    for d in form_dates:
        asof = (d + pd.offsets.MonthEnd(0)).date()
        fr = rets.loc[d]
        mkt[d] = fr[tickers].mean()
        for v in VARIANTS:
            raw = {f: {} for f in FACTORS}
            ep_raw, sp_raw = {}, {}
            for tk in tickers:
                px = prices.at[d, tk]
                if pd.isna(px):
                    continue
                sh = _latest_instant(vers[tk].get(SHARES, {}), v, asof)
                fac = compute_factors(vers[tk], v, asof, (sh * px) if sh else None)
                for f in FACTORS:
                    val = fac[f]
                    if f == "value_composite" and isinstance(val, tuple):
                        _, ep, sp = val
                        if ep is not None: ep_raw[tk] = ep
                        if sp is not None: sp_raw[tk] = sp
                    elif f != "value_composite" and val is not None and np.isfinite(val):
                        raw[f][tk] = val
            if ep_raw or sp_raw:
                comp = (winsor_z(pd.Series(ep_raw)).add(winsor_z(pd.Series(sp_raw)), fill_value=0)) / 2.0
                raw["value_composite"] = comp.to_dict()
            for f in FACTORS:
                fser = pd.Series(raw[f])
                if len(fser) < 8:
                    continue
                z = winsor_z(fser)
                r = fr.reindex(z.index)
                ic[v][f].append(spearman_ic(z, r))
                q = z.rank(pct=True)
                longs, shorts = z.index[q >= 1 - quantile], z.index[q <= quantile]
                if len(longs) == 0 or len(shorts) == 0:
                    continue
                w = pd.Series(1.0 / len(longs), index=longs).add(
                    pd.Series(-1.0 / len(shorts), index=shorts), fill_value=0.0)
                ls[v][f][d] = (w * fr.reindex(w.index)).sum()
                pw = prev_w[v][f]
                turn[v][f].append(sum(abs(w.get(k, 0.0) - pw.get(k, 0.0)) for k in set(w.index) | set(pw)) / 2.0)
                prev_w[v][f] = w.to_dict()

    mkt_s = pd.Series(mkt)
    lss = {v: {f: pd.Series(ls[v][f]).sort_index() for f in FACTORS} for v in VARIANTS}
    t1 = {}
    for v in VARIANTS:
        for f in FACTORS:
            s = lss[v][f]
            if len(s) < 12:
                t1[(f, v)] = None; continue
            a, t = nw_alpha_t(s.values, mkt_s.reindex(s.index).values)
            t1[(f, v)] = dict(alpha=a * 12 * 100, t=t,
                              sharpe=(s.mean() / s.std() * math.sqrt(12)) if s.std() > 0 else np.nan,
                              ic=np.nanmean(ic[v][f]) if ic[v][f] else np.nan,
                              turnover=np.nanmean(turn[v][f]) if turn[v][f] else np.nan, n=len(s))
    t2, pAD = {}, []
    for f in FACTORS:
        def dd(v1, v2, k):
            a, b = t1.get((f, v1)), t1.get((f, v2))
            return (a[k] - b[k]) if a and b else np.nan
        common = lss["A"][f].index.intersection(lss["D"][f].index)
        diff = (lss["A"][f].reindex(common) - lss["D"][f].reindex(common)).values if len(common) else np.array([])
        p = block_bootstrap_p(diff) if len(diff) >= 12 else np.nan
        pAD.append(p)
        t2[f] = dict(da_AD=dd("A", "D", "alpha"), ds_AD=dd("A", "D", "sharpe"),
                     da_BC=dd("B", "C", "alpha"), da_CD=dd("C", "D", "alpha"), p=p)
    for f, q in zip(FACTORS, bh_qvalues(pAD)):
        t2[f]["q"] = q
    return t1, t2, len(tickers), str(form_dates[0].date()), str(form_dates[-1].date())


def print_tables(t1, t2, n, d0, d1):
    print(f"\n=== Sample: {n} symbols, {d0}..{d1} ===")
    print("\nE1  factor x variant -> RankIC  CAPMalpha%  NW-t  Sharpe  Turnover")
    print(f"{'factor':18}{'V':>2}{'rankIC':>9}{'alpha%':>9}{'t':>7}{'Sharpe':>8}{'turn':>7}")
    for f in FACTORS:
        for v in VARIANTS:
            m = t1.get((f, v))
            print(f"{f:18}{v:>2}   n/a" if not m else
                  f"{f:18}{v:>2}{m['ic']:9.3f}{m['alpha']:9.2f}{m['t']:7.2f}{m['sharpe']:8.2f}{m['turnover']:7.2f}")
    print("\nE2  factor -> A-D dAlpha  A-D dSharpe  B-C dAlpha  C-D dAlpha  bootP  BH-q")
    print(f"{'factor':18}{'AD_da':>8}{'AD_dS':>8}{'BC_da':>8}{'CD_da':>8}{'p':>8}{'q':>8}")
    for f in FACTORS:
        r = t2[f]
        print(f"{f:18}{r['da_AD']:8.2f}{r['ds_AD']:8.2f}{r['da_BC']:8.2f}{r['da_CD']:8.2f}{r['p']:8.3f}{r['q']:8.3f}")
    print("\nRead E2's A-D column: a positive gap is look-ahead inflation. It should be")
    print("largest for timing-sensitive factors (earnings_change, accruals).")


def make_demo():
    """
    Synthetic panel that deliberately embeds a look-ahead edge.

    Each quarter has an earnings "surprise" that (a) moves reported net income and
    (b) drives the stock's return during the [period_end, filing) window — the run-up
    a naive period-end-dated backtest captures but a strict point-in-time one cannot.
    So the earnings_change factor should show inflated alpha under variant A and a
    much smaller one under variant D. The harness recovers exactly that.
    """
    rng = np.random.default_rng(7)
    syms = [f"S{i:02d}" for i in range(40)]
    months = pd.date_range("2018-01-01", "2023-12-01", freq="MS")
    mret = rng.normal(0.004, 0.05, (len(months), len(syms)))      # base returns
    qtrs = [("01-01", "03-31"), ("04-01", "06-30"), ("07-01", "09-30"), ("10-01", "12-31")]
    rows = []
    for si, s in enumerate(syms):
        base = 1e9 * (1 + 0.3 * si / len(syms))
        g_s = rng.uniform(-0.05, 0.20)                            # per-symbol annual revenue trend (cross-sectional dispersion)
        prev_ni = None
        for y in range(2016, 2024):
            for q, (ms, me) in enumerate(qtrs):
                pend = pd.Timestamp(f"{y}-{me}")
                filed = pend + pd.Timedelta(days=45)              # ~45d filing lag
                surprise = rng.normal(0, 1)
                ni = base * (0.1 + 0.02 * q) * (1 + 0.15 * surprise)
                rev = base * (1 + 0.05 * q) * (1 + g_s) ** (y - 2016)
                for concept, val in [(REVENUE, rev), (NET_INCOME, ni),
                                     (CFO, ni * 0.9), (TOTAL_ASSETS, base * 2)]:
                    pstart = "" if concept == TOTAL_ASSETS else f"{y}-{ms}"
                    rows.append(dict(symbol=s, concept=concept, period_end=pend.date(),
                                     period_start=pstart, filing_date=filed.date(), value=val))
                rows.append(dict(symbol=s, concept=SHARES, period_end=pend.date(),
                                 period_start="", filing_date=filed.date(), value=1e8))
                # inject the leak: surprise drives returns only in [period_end, filing)
                for mi, mdate in enumerate(months):
                    if pend.date() <= mdate.date() < filed.date():
                        mret[mi, si] += 0.05 * surprise
                prev_ni = ni
    px = pd.DataFrame(100 * np.exp(np.cumsum(mret, axis=0)), index=months, columns=syms)
    return pd.DataFrame(rows), px


def main():
    ap = argparse.ArgumentParser(description="A–D point-in-time information-set comparison.")
    ap.add_argument("--facts", help="CSV of filed fundamental versions")
    ap.add_argument("--prices", help="CSV wide monthly close (date + one col per symbol)")
    ap.add_argument("--quantile", type=float, default=0.2, help="long/short tail (0.2 = quintile)")
    ap.add_argument("--demo", action="store_true", help="run on a built-in synthetic panel")
    a = ap.parse_args()
    if a.demo:
        facts, prices = make_demo()
    elif a.facts and a.prices:
        facts = pd.read_csv(a.facts)
        prices = pd.read_csv(a.prices, index_col=0)
    else:
        ap.error("provide --facts and --prices, or --demo")
    print_tables(*run(facts, prices, quantile=a.quantile))


if __name__ == "__main__":
    main()
