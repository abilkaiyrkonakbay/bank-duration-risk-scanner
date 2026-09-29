# Methodology v0.2 — funding pressure and deposit-run scenarios

This is an educational securities-loss and funding-risk screen. It is not a probability-of-failure model, regulatory liquidity ratio, bank valuation, or full interest-rate-risk model. The changes below are motivated by research; their numerical weights and scenario assumptions are not estimated from that research.

## Research translated into model changes

1. **Losses and runnable funding belong together.** Jiang, Matvos, Piskorski and Seru study the interaction of asset-value declines and uninsured-depositor runs in [Monetary Tightening and U.S. Bank Fragility](https://www.nber.org/papers/w31048). We retain separate loss and uninsured metrics, add a joint warning and an interaction diagnostic, and simulate withdrawals against available resources. Our securities-only screen does not replicate their broader asset-marking exercise or their failure estimates.
2. **Funding pass-through depends on the rate cycle.** Dias and Scott's [Monetary Policy and Bank Funding Costs](https://www.federalreserve.gov/econres/feds/monetary-policy-and-bank-funding-costs.htm) documents cycle-dependent funding betas and differences between deposit and nondeposit liabilities. Our implementation inference is to keep raw beta descriptive and score funding-spread compression instead of treating high beta as uniformly adverse. This is a transparent proxy, not their estimated model.
3. **Liquidity resources and funding substitution matter.** Glancy and coauthors' [The 2023 Banking Turmoil and the Bank Term Funding Program](https://www.federalreserve.gov/econres/feds/the-2023-banking-turmoil-and-the-bank-term-funding-program.htm) links uninsured funding and securities losses with outflows, and studies borrowing's liquidity role. We therefore add cash coverage, borrowing reliance and an explicit funding waterfall. No access to the historical BTFP, discount window or any future facility is assumed.

## Accounting and time-series controls

Amounts remain in reported thousands of dollars. TCE is equity less preferred equity and intangibles. Missing deductions make TCE unavailable. Securities gains are floored at zero for loss exposure. Loss/TCE requires strictly positive TCE; nonpositive TCE or HTM-adjusted TCE instead triggers a separate capital-priority flag.

HTM-adjusted TCE subtracts HTM losses only. AFS marks already affect GAAP equity through AOCI, so subtracting them again would double count. These are deliberately simple pre-tax proxies: tax effects and existing HTM credit allowances are not reconciled into a regulatory capital calculation. Realized HTM losses below use gross amortized cost and may overstate incremental losses where allowances already exist.

Domestic deposit interest expense is YTD. Q1 starts with reported YTD expense; Q2–Q4 use current minus previous quarter's YTD expense. Negative differences are flagged as potential restatements or data issues, not negative funding costs. Annualized cost is four times quarterly expense divided by beginning/end average domestic deposits; it includes noninterest-bearing domestic deposits in the denominator.

Adjacent reports must be exactly one calendar quarter apart. If assets or domestic deposits change by more than 30% in absolute terms (configurable), the period is flagged and cost comparisons are suppressed. This conservative heuristic catches possible mergers, disposals or major flows; it cannot identify the cause, and genuine runs may also trigger it. The outflow diagnostic itself remains visible.

A four-quarter cost comparison needs six consecutive quarter-end filings, because each endpoint's quarterly cost needs a beginning deposit balance. The five intervals must have no flagged balance discontinuity. All five quarterly EFFR observations are required. Eight displayed quarters therefore download five extra warm-up quarters. FRED's daily DFF observations are averaged across the entire calendar quarter; incomplete daily coverage is rejected. Exports never include dates after `--as-of`, though the public filings can be subsequently revised—this is not a point-in-time vintage database.

## Cycle-aware funding score

```text
raw deposit beta = (deposit cost[t] − deposit cost[t−4]) / (EFFR[t] − EFFR[t−4])
funding spread compression = Δ deposit cost − Δ EFFR
```

Raw beta remains N/A when the absolute EFFR change is below 25 bps. Compression does not divide by the benchmark change, so it remains usable in flat-rate periods when the history is otherwise valid. Rising, falling, mixed and small-change windows are labeled. A change of more than 1 bp between adjacent quarter-average EFFRs counts toward the up/down path label.

For example, with a 200 bp rate cut, a bank cutting deposit cost by only 40 bp has 160 bp of compression; cutting cost by 160 bp produces only 40 bp. The former is scored as more exposed to funding pressure. This is an EFFR-relative funding proxy, not a forecast of loan repricing, asset yield or net interest margin. Within a given quarter EFFR is common to all banks, so this factor's ordering is equivalent to ranking their deposit-cost changes.

Scores use same-quarter asset peers: under $1bn, $1–10bn, $10–100bn, and $100bn or more. Each component uses average ranks for ties, divided by its nonmissing peer count. Weights are 50% combined loss/TCE, 30% uninsured concentration and 20% funding compression. Full scores require all three components; partial scores divide the available weighted sum by available weight. Coverage reports that weight, not a count of fields. Missing uninsured deposits are never imputed.

Peer counts are exported alongside percentiles. Small peer groups can produce unstable ranks; a singleton ranks at 100, and all-zero tied observations need not rank at zero. Demo scores are consequently not evidence of discriminatory power. Partial-score tiers are visibly labeled partial and should not be compared mechanically with full scores. Capital flags take review priority; otherwise full scores precede partial scores in the default export. Flags do not overwrite percentiles or manufacture a full score.

## Additional diagnostics

- `cash_uninsured_coverage`: cash and balances / estimated uninsured deposits; N/A for zero or missing uninsured deposits. It is not the LCR or verified immediately accessible liquidity.
- `borrowings_assets`: other borrowings / assets. It excludes some wholesale funding categories, such as federal funds and repos; no claim is made that it measures all wholesale liabilities.
- `deposit_growth_qoq`, `deposit_outflow_qoq`: adjacent-quarter deposit change and its negative portion, respectively. Outflow flags default to 5% / 10%.
- `net_interest_margin`, `nim_change_qoq`: reported quarterly NIM and its change. This is a contextual profitability indicator, not an extra score component.
- `loss_funding_interaction`: combined loss/TCE multiplied by uninsured concentration. The joint warning requires both metrics above their configured amber thresholds. It is diagnostic, not a calibrated coefficient.

The additional FDIC inputs are `CHBAL`, `OTHBOR`, and `NIMYQ`; the first two are $000, and NIMYQ is converted from percentage points to decimal. See the official [FDIC field definitions](https://api.fdic.gov/banks/docs/risview_properties.yaml). The direct FFIEC adapters do not yet populate these additional liquidity/NIM fields; their active-source stress cases remain unavailable unless those inputs are supplied, not implicitly zero.

## Deposit-run simulator

Edit `config/stress_scenario.yml`, or copy it to define a named scenario. All fractions must be between zero and one; sale discounts must be strictly below one. Defaults are illustrations, not estimated run probabilities:

- 40% uninsured and 5% remaining domestic-deposit runoff.
- 80% of reported cash, 75% of AFS fair value and 0% of HTM fair value usable.
- Additional sale discounts from reported fair value of 3% for AFS and 5% for HTM.
- Incremental borrowing capacity of 0% of assets; if enabled, an assumed rate of EFFR + 200 bps, floored at zero.
- Twelve months for the incremental replacement-funding premium. This is not the assumed speed of a run.

The remainder of domestic deposits is treated as insured for this simplified scenario. Missing, negative or inconsistent uninsured balances invalidate the liquidity run. Cash can include balances outside the domestic entity's immediately usable pool; usable fractions must reflect analyst judgment about accessibility and encumbrance. If borrowing is collateralized by securities, reduce the saleable fractions to avoid double counting that collateral. The model does not infer pledges or actual committed credit lines.

Resources are applied in order, each capped at the remaining runoff: usable cash → assumed incremental borrowing → saleable AFS proceeds → permitted HTM proceeds → uncovered funding gap. Existing borrowings are not new funding capacity and are not added to cash a second time.

```text
runoff = uninsured × uninsured runoff + (domestic − uninsured) × insured runoff
AFS proceeds capacity = AFS fair value × usable share × (1 − extra discount)
HTM proceeds capacity = HTM fair value × permitted share × (1 − extra discount)
AFS incremental loss = fair value sold − cash proceeds
HTM loss proxy = max(amortized cost × fair value sold / total fair value − proceeds, 0)
TCE after sales = TCE − incremental AFS loss − realized HTM loss proxy
funding premium = new borrowing × (replacement rate − prior deposit cost) × months / 12
TCE less funding premium = TCE after sales − funding premium
```

Only the sold HTM share is marked into the scenario equity bridge. AFS's existing amortized-cost/fair-value gap is not charged again. A negative funding premium represents savings, not a loss. The premium calculation isolates a funding-cost effect: no ongoing earnings, tax, lost asset yield or changes to remaining deposit rates are forecast. It is not total interest expense or stressed net income.

Missing TCE leaves liquidity outputs available but suppresses equity results. Missing EFFR or deposit cost suppresses the premium when borrowing is positive; zero borrowing correctly has zero premium without those rates. A missing active resource input makes the waterfall unavailable; a disabled resource does not require its balance. An uncovered funding gap remains visible even if scenario TCE is positive.

## Reproducibility, limits and next research step

Implementation is organized into four calculation stages in `metrics.py`; the pipeline runs those stages and the simulator once, then passes prepared results to CSV and Excel. This cleanup does not change the v0.2 formulas, weights or assumptions. Regulatory mappings are defined directly in their source adapters. The former duplicate `config/mdrm_mapping.yml` was documentation-only and was not loaded by the application.

The workbook's editable `Stress Test` reproduces the Python formulas. `Bank Detail` controls its bank selector. Workbook edits are local sensitivity analysis; they do not mutate SQLite or CSVs. The `simulate` command writes a separate scenario CSV and assumptions/source-hash JSON, preserving baseline outputs. Each baseline run records the model version, full configuration, configuration hash and filing lineage including warm-up observations. SQLite metric rows are an idempotent latest calculation per model version, not an immutable history of every scenario; retain manifests/outputs for that purpose.

Version 0.2 adds nullable SQLite columns automatically and uses new FDIC cache filenames so legacy cached data cannot masquerade as populated liquidity inputs. The demo uses a separate database. Except for the stated SVB Q4 regression targets, demo trends and simulator liquidity/NIM inputs are illustrative, not historical evidence.

Not modeled: loan fair values, derivatives and hedges, deposit franchise value, depositor network concentration, intraday liquidity, HTM classification consequences, loan sale capacity, capital issuance, tax effects, credit allowance reconciliation or management responses. The default waterfall does not produce a duration estimate or a full economic-value-of-equity shock. It would be misleading to invent those exposures from these aggregate fields.

The next validation step is a point-in-time, multi-cycle backtest against independently defined outcomes, with failure/merger treatment, publication lags, holdout periods and coverage stratification. Until then, neither score thresholds nor simulator outputs should be described as empirically calibrated predictors.
