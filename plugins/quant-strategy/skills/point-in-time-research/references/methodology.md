# Methodology: Time Semantics, Admissibility, and Statistics

Formal backing for the SKILL.md workflow. Read this when you need the precise
definitions or the exact statistical procedures the comparison harness uses.

## 1. Time semantics

A financial fact has four independent time dimensions; collapsing them is the
root cause of look-ahead bias.

| Dimension | Meaning | Research use |
|---|---|---|
| **Valid time** | the economic period the fact describes (`period_start..period_end`) | duration classification |
| **Public time** | earliest verifiable moment the market could observe it | best-observable information set |
| **Knowledge time** | interval during which a *specific version* was current in the DB | as-of historical reconstruction |
| **Tradable time** | earliest execution point under the strategy's rule | signal activation, return alignment |

For a disclosure with several verifiable channels (8-K exhibit, press release,
IR page, 10-Q/10-K), the best-observable public time is the **minimum** over
channels whose timestamp and content can be substantiated. The conservative
baseline uses the SEC acceptance timestamp when no earlier channel is verifiable.

Tradable time is a deterministic function of public time, the exchange calendar,
the session, and a disclosed execution lag — e.g. a pre-open release → that day's
open; an after-close release → next session's open; an intraday release → next
bar after a processing delay. This mapping is part of the *strategy spec*, not a
hidden data-cleaning step.

## 2. The admissibility indicator

A fact version `j` is admissible for a signal at time `t` iff:

```
Admissible(F_j, t) = 1{ X_j ≤ t }                      # tradable-from ≤ t
                   · 1{ K_start_j ≤ t < K_end_j }       # the version known at t
                   · 1{ identities valid at t }
                   · 1{ all parent facts admissible at t }
                   · 1{ quality = pass }
```

This is deliberately stricter than a date join: temporal availability, version
validity, and data quality are evaluated together, by the *same* function that
factor generation and portfolio construction call — so no later stage can bypass
the rule.

## 3. The four information-set variants

Given, per `(symbol, concept, period_end)`, the set of filed versions, define:

- `original` = value from the earliest `filing_date`
- `latest`   = value from the latest `filing_date` (may post-date `t`)
- `filing`   = earliest `filing_date`
- `tradable` = next trading session after `filing`

| Variant | admissible if | value used |
|---|---|---|
| A | `period_end ≤ t` | `latest` |
| B | `filing ≤ t` | `latest` |
| C | `filing ≤ t` | `original` |
| D | `tradable ≤ t` | `original` |

Hold the factor formula, universe, portfolio construction, and costs **fixed**
across A–D. The only thing that changes is the information set. Report levels per
variant and the paired differences A–B, B–C, C–D, A–D.

## 4. Factor construction (per formation date, per variant)

- Income-statement flows (revenue, net income, operating cash flow): reconstruct
  continuous single quarters via cumulative differencing (Q4 = annual − 9-month
  YTD), then form TTM as the sum of the last four *contiguous* quarters. Refuse to
  bridge gaps — report missing rather than fabricate a TTM across a hole.
- Balance-sheet instants (assets, shares, equity): take the most recent
  admissible value.
- Cross-section: winsorize (1%/99%) and standardize (z-score) before ranking.
  Orient each factor so higher score = expected higher return (e.g. flip accruals).

## 5. Statistics (what the harness reports)

- **Rank IC** — monthly Spearman correlation of factor vs forward return; report
  the time-series mean.
- **CAPM alpha** — intercept of the monthly long-short return regressed on the
  market proxy, annualized. The proxy is the **equal-weight mean return of the
  test universe** (not an external index), so read the alpha as "selection on top
  of the equal-weight universe," not "beat the market." Significance via
  **Newey–West** HAC t-stat (default 4 lags), because monthly factor returns are
  serially dependent and heteroskedastic.
- **Sharpe** — annualized mean/stdev of the long-short return.
- **Turnover** — one-way fraction of the book traded per rebalance.
- **Paired differences** — for A–D etc., the mean of the per-month return
  difference, with a two-sided **stationary block bootstrap** p-value (preserves
  local dependence) rather than an i.i.d. test.
- **Multiple testing** — the harness reports **Benjamini–Hochberg** q-values
  across the factor family to control the false-discovery rate. A deflated Sharpe
  (Bailey & López de Prado) is the recommended manual follow-up once the number
  and dependence of trials can be estimated; the harness does not compute it.

## 6. Honest boundaries

- A strict PIT database has *lower* coverage because uncertain/late facts are
  excluded — this is correct, not a defect.
- A current-membership or small universe inflates performance *levels* via
  survivorship; the A–D *paired differences* largely cancel it (same universe
  across variants), so prefer the differences for the headline claim.
- Temporal integrity does not remove all bias — corporate actions, delisting
  returns, security-master errors, data snooping, and capacity remain separate
  obligations.

## References

Ball & Brown (1968); Bernard & Thomas (1989, PEAD); Sloan (1996, accruals);
Fama & French (1992, 1993, 2015); Newey & West (1987); Politis & Romano (1994,
stationary bootstrap); Benjamini & Hochberg (1995); Bailey & López de Prado
(2014, deflated Sharpe); Harvey, Liu & Zhu (2016).
