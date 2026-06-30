# Point-in-Time Audit Checklist

Run this before reporting any backtest, factor, or signal result. Each flag is a
common, *silent* way a backtest claims an edge that no investor could have had.
Resolve the flag or disclose it explicitly — never bury it.

## A. Data-time leaks

- [ ] **Period-end as activation.** Are fundamentals joined to prices on
  `period_end`? They must activate on filing/acceptance time. Red flag: a signal
  reacts before the announcement that created it.
- [ ] **Fixed lag instead of event time.** A blanket "+45 days" hides per-issuer
  variation (filer status, late filings, pre-announcements). Prefer event-specific
  timestamps; document any fallback hierarchy.
- [ ] **Latest value backfilled.** Does the database return the *current* value
  for a historical period (restatements overwriting originals)? Use the
  as-originally-filed version for the historical date.
- [ ] **Same-session execution.** Is an after-close release traded at that day's
  close? Map public time → tradable time (calendar + execution lag).
- [ ] **Derived facts without lineage.** Reconstructed Q4 (= annual − 9-month
  YTD) inherits the *later* parent's availability time. Is that respected, and is
  the derived fact flagged distinct from issuer-reported?

## B. Universe and survivorship

- [ ] **Current-membership universe.** Is the universe today's constituents
  applied to the past? That backfills survivors and inflates *levels*. Use an
  effective-dated universe that retains delisted/acquired/bankrupt names through
  their validity, or disclose the bias and lean on A–D *paired differences*.
- [ ] **Backfilled classifications.** Are sector/industry/identifier mappings the
  current ones projected backward? They must be effective-dated.

## C. Cost and execution realism

- [ ] **No transaction costs.** Re-run on the cost ladder (0/0 → 30/500 bps).
  Does the edge degrade gracefully or vanish?
- [ ] **Free shorting.** Does the short leg ignore borrow cost / hard-to-borrow?
- [ ] **Unrealistic fills.** Full size at the close, no slippage, no capacity cap?

## D. Statistical honesty

- [ ] **Isolated Sharpe.** Is the headline a single Sharpe with no account of how
  many factors/parameters were tried? Record the trial count; report BH q or a
  deflated Sharpe.
- [ ] **i.i.d. inference on dependent data.** Monthly factor returns are serially
  dependent. Use Newey–West and a stationary/block bootstrap, not plain t-tests.
- [ ] **In-sample optimization sold as result.** Was the config chosen on the
  same data it is evaluated on? Use walk-forward (`/wf`).

## E. Interpretation

- [ ] **Beta sold as alpha.** Did the "alpha" survive market-neutralization?
  Price-only momentum usually does not.
- [ ] **Sector bet sold as selection.** Did it survive sector-neutralization?
- [ ] **Failures deleted.** Are rejected factors and dead configs kept in the
  research log, or quietly dropped?

> The standard: a backtest is point-in-time only when **every** input can be
> shown to have been observable, admissible, and executable at the moment the
> strategy used it.
