---
name: point-in-time-research
description: >-
  Enforce point-in-time data discipline for any quant research task. Trigger
  this skill whenever the user: (1) backtests a strategy or factor, (2) asks
  for a Sharpe / alpha / IC / return — even as a quick one-liner ("just give
  me the Sharpe", "run this backtest"), (3) reviews or asks you to confirm /
  audit / validate quant or backtest code ("is my code PIT clean?", "confirm
  this is correct", "check my backtest for look-ahead"), (4) evaluates whether
  a signal or edge is real, or (5) screens on fundamentals. Fire even when the
  user claims they already fixed PIT issues — check specifically for whichever
  of the three silent leaks (period-end dating, latest-value overwrite,
  same-session execution) may remain. Do NOT trigger for generic finance
  questions that involve no simulation or data join.
---

# Point-in-Time Quant Research

A historical price series is not enough to make a backtest point-in-time. The
moment a strategy reads a financial fact, that fact must already have been
**observable, admissible, and executable** by a real investor. Most "great"
backtests are not fraudulent — they are *contaminated* by information that did
not exist yet. This skill is the discipline that prevents it.

## When this matters

Trigger this skill for any research that turns data into a claim about future
returns: factor IC, long-short alpha, Sharpe, a screener edge, an ML signal, a
strategy backtest. The user usually will **not** say "look-ahead bias" — they
will say "does this strategy work?", "backtest momentum on SPY", "is this factor
predictive?". That is exactly when the leaks below do their quiet damage.

## The three silent leaks

1. **Period-end dating.** A fact for the quarter ended 31 Mar describes activity
   through 31 Mar, but the market only learns it weeks later (earnings release,
   10-Q in May). Joining on `period_end` hands the simulated investor weeks of
   free foresight. Anchor on the **filing/acceptance** time, never the period end.
2. **Latest-value overwrite.** Databases answer "what is the value for this
   period *now*?" — overwriting the originally-reported number with later
   restatements. A backtest then trades on a revision that did not exist at the
   time. Use the **originally-filed** version, not the latest.
3. **Same-session execution.** A release at 16:05 ET is public that day but
   cannot be traded at the 16:00 close. Map public time → **tradable time** with
   an exchange calendar and an explicit execution lag.

## The admissibility rule (apply before every signal)

A fact version `j` is usable for a signal evaluated at time `t` only if **all**
hold:

- its tradable-from timestamp ≤ `t` (observable and executable),
- the version was the one known at `t` — a later revision is **not** backfilled,
- its issuer/security/identifier mappings are valid at `t`,
- every parent of a derived fact (e.g. a reconstructed Q4) was itself admissible,
- it passed the declared quality gates.

If you cannot establish all five, exclude the fact and record why. Reduced
coverage from honest exclusion is **not** a defect; higher coverage bought by
backfilling is.

## Workflow — order matters

Temporal integrity sits **upstream** of every other robustness check.
Neutralization, costs, and multiple-testing corrections cannot rescue a result
whose inputs were never available. Always in this order:

1. **Reconstruct the historical information set first** (apply the admissibility
   rule; anchor on filing/acceptance time; keep original versions).
2. **Then** compute the factor and test predictability.
3. **Then** apply economic and statistical robustness (the gauntlet below).

## Quantify the distortion: the A–D information sets

Materialize four versions of the *same* panel, changing **only** the data-time /
data-version definition while factor formula, universe, portfolio rule and costs
stay fixed. The contrast isolates how much "alpha" is really a timing artefact.

| Variant | Activation time | Value version | What it isolates |
|---|---|---|---|
| **A** Extreme naive | fiscal period end | latest (revised) | upper-bound look-ahead stress |
| **B** Date corrected | filing date | latest (revised) | fixes date error, keeps version contamination |
| **C** Strict SEC PIT | acceptance time | original as-filed | conservative, auditable |
| **D** Tradable PIT | next session after filing | original as-filed | best observable + execution-aware |

`A–B` = activation-date error · `B–C` = version contamination · `C–D` = execution
alignment. A positive `A–D` alpha gap is the look-ahead inflation; it is largest
for timing-sensitive factors (earnings surprise, accruals) and small for slow
valuation levels.

**Run it now.** Do not just describe the A–D framework — execute the harness:

```bash
# Verify the harness works (no data required):
python scripts/information_set_compare.py --demo

# With real data:
python scripts/information_set_compare.py \
  --facts your_facts.csv \
  --prices your_prices.csv
```

The script produces A–D levels and paired differences (CAPM alpha + Newey–West
t, Sharpe, rank IC, turnover, block-bootstrap p-values, Benjamini–Hochberg
q-values). Run `--help` for the exact CSV column contract. Read
`references/methodology.md` for the formal time semantics and statistical
procedures.

## The validation gauntlet

A surviving factor must clear every gate; report each result, including
failures. Within Aria these map onto existing commands.

| Gate | Question | Aria command |
|---|---|---|
| Market-neutral | Does return survive removing market beta? | `/backtest`, `/ptbt` |
| Sector-neutral | Stock selection, or an industry bet? | `/factor`, `/sector` |
| Transaction costs | Survives turnover cost? | cost ladder (below) |
| Borrow costs | Is the short leg economic? | cost ladder (below) |
| Subperiods | One regime, or persistent? | `/wf` (walk-forward) |
| OOS selection | Does a past-chosen config persist? | `/wf` anchored/rolling |

**Cost ladder.** Re-price the long-short net of `0/0, 10/50, 20/100, 30/200,
30/500` bps (transaction / annual borrow) and check the edge degrades gracefully
rather than vanishing. Because the inflated (A) and strict (D) variants trade at
near-identical rates, costs subtract almost equally from both — so an A–D gap
that survives the ladder is genuinely a timing effect, not a cost artefact.

## Honest reporting

- **Real vs fake alpha.** Price-only momentum is usually beta contamination, not
  alpha — it collapses under market-neutralization. A value composite (E/P + S/P)
  that survives sector-neutralization and the cost ladder is a real candidate.
  State plainly which is which.
- **Report what failed.** A factor that died after neutralization is a finding,
  not a deletion. Keep it in the log.
- **Multiple testing.** Record the number of factor/parameter trials. Interpret
  the final statistic against the search (Benjamini–Hochberg q, deflated Sharpe),
  never as an isolated Sharpe ratio.
- **Pilot ≠ population.** A small or current-membership universe carries
  survivorship bias in the *levels*; the A–D *paired differences* are far more
  robust (same universe across variants). Say so; do not over-claim magnitude.

## Code review mode

When the user provides actual code for review, do not re-explain the general
framework — they know it. Instead:

1. **Scan specifically for whichever of the three leaks remains unresolved.** If
   the user says "I already fixed period-end dating and latest-value overwrite,"
   take that at face value and check only same-session execution.
2. **Same-session execution in code.** If the merge joins on `filing_date` and
   the trade happens at that same day's close (or open), flag it: 8-K / 10-Q
   filings often arrive after the 16:00 ET close, so that close is not an
   admissible execution time. Fix: `np.busday_offset(filing_date, 1)` → trade
   next session's open or close.
3. **Derived-fact lineage.** If a derived column (e.g. reconstructed Q4 =
   annual − 9-month YTD) does not track its own `filing_date` separately, flag
   it — the derived fact inherits the *later* parent's availability time.
4. **Give the minimal diff, not a rewrite.** One targeted `busday_offset` fix is
   the answer; a full code rewrite is not.

## When the user dismisses methodology

If a user says "I know all this, just give me the Sharpe" or "skip the lecture":

- Do NOT simply comply and return an uninspected number.
- Do NOT give a long lecture either.
- One sentence: state why the raw Sharpe is uninterpretable without a data
  contract (look-ahead inflation can be 2–3× on valuation factors).
- Then make a concrete offer: "Run `python scripts/information_set_compare.py
  --demo` to see the distortion magnitude, then share your data contract and I
  will run the full gauntlet — `/backtest`, `/wf`, `/factor`, cost ladder."
- The offer must reference Aria commands, not generic third-party tools.

## Audit checklist

Before reporting any backtest result, run the red-flag audit in
`references/audit_checklist.md`. If any flag is unresolved, fix it or disclose it
— never bury it.

## Bundled resources

- `scripts/information_set_compare.py` — the A–D four-variant comparison harness
  (CAPM/NW, Sharpe, IC, turnover, block bootstrap, BH). Stdlib + pandas/numpy.
- `references/methodology.md` — formal time semantics, the admissibility
  indicator, and the exact statistical procedures.
- `references/audit_checklist.md` — the pre-report look-ahead / survivorship /
  cost red-flag checklist.
