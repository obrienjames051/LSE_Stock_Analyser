# LSE Screener — Research & Strategy Documentation

This document records the backtesting research conducted to determine the optimal trading strategy for the LSE screener. It covers the confirmed strategy rules, key findings from each experiment, things tested and rejected, and the baseline performance figures used in the live programme.

---

## Version History

| Version | Key change |
|---------|-----------|
| v1.0 | Initial build |
| v2.0 | ATR stop system |
| v3.0 | Kelly sizing |
| v4.0 | News sentiment layer |
| v5.0 | Macro sentiment layer |
| v6.0 | Calibration system |
| v7.0 | Sector diversification (1 per sector) |
| v8.0 | Tuesday open / Monday close strategy confirmed |
| **v9.0** | **Strategy E: cap 85 div→cap selection; prob formula rescaled to /84; BACKTEST_BASELINE_RETURN auto-updated by backtest** |

---

## Confirmed Strategy (v9.0)

| Parameter | Value |
|-----------|-------|
| **Run programme** | Tuesday morning, before market open |
| **Data used** | Monday close as the final bar |
| **Entry** | Tuesday open, all 5 picks |
| **Stop price** | Monday close − 1× ATR (known before open) |
| **Stop monitoring** | Manual check each day Tuesday–Friday, just before close |
| **Stop exit** | If daily close ≤ stop price → sell at market before that day's close |
| **Normal exit** | Sell before Monday close |
| **Upper limits** | None |
| **Hard stop orders** | Not used — mental stops only |
| **Baseline return** | +0.760% per pick per week |

---

## Baseline Backtest Setup

All backtests share the following parameters unless stated otherwise:

- **Universe**: LSE screener picks, 5 per week
- **Weeks**: 51–52 (depending on data cutoff — see individual tests)
- **Capital per pick**: £1,000 (Kelly-weighted sizing not used in backtests for comparability)
- **Data end date**: `BACKTEST_END_DATE = "2026-03-02"` (Monday) — fixed for reproducibility
- **Ticker handling**: Try `<ticker>.L` first, fall back to raw ticker
- **Probability format**: Stored as percentage (e.g. `63.0`), divided by 100 before use

A critical early discovery was that the original backtest used a single Friday snapshot, ignoring whether stops or limits had been hit mid-week. All scripts below use proper daily close checking.

---

## Backtest Results Summary

### 1. Exit Strategy Comparison
**Script**: `backtest_exit_strategy.py`  
**Purpose**: Establish which combination of stops and limits produces the best outcome.  
**Setup**: Monday open entry → Friday close exit, 52 weeks, 260 picks total.

| Variant | Stops | Upper Limits | Avg Return | Worst Week |
|---------|-------|--------------|------------|------------|
| A | No | No | +0.642% | −8.031% |
| B | No | Yes | −0.01% | — |
| C | **Yes** | **No** | **+0.696%** | **−3.888%** |
| D | Yes | Yes | +0.28% | — |

**Finding**: Upper limits destroy returns. Stop losses add both return and downside protection.  
**Decision**: Adopt Variant C — stops only, hold to exit.

---

### 2. Stop Width Testing
**Script**: `backtest_stop_width.py`  
**Purpose**: Find the optimal ATR multiplier for stop distance.  
**Setup**: Variant C, Monday open entry, 52 weeks.

| Stop Width | Avg Return |
|------------|------------|
| 1.0× ATR | +0.854% ← best |
| 1.5× ATR | lower |
| 2.0× ATR | lower |
| 2.5× ATR | lower |

**Finding**: Tighter stops at 1.0× ATR outperform wider stops at every tested width.  
**Decision**: Keep `STOP_MULTIPLIER = 1.0`.

---

### 3. Stop Execution Method
**Script**: `backtest_stop_execution.py`  
**Purpose**: Determine how to execute when a stop is triggered — sell at trigger-day close vs. next open.  
**Setup**: 1.0× ATR stop, Monday open entry, 52 weeks.

| Method | Description | Avg Return |
|--------|-------------|------------|
| 1 | Sell at triggering day's close (manual) | **+0.854%** ← best |
| 2 | Sell at next open regardless | +0.820% |
| 3 | Sell at next open, hold if gap up over stop | +0.740% |

**Finding**: Gap-up recoveries don't add value. Selling at the triggering close is best.  
**Decision**: Mental stops — monitor each close and sell same-day if triggered. No hard stop orders with broker.

---

### 4. Calibration Method
**Script**: `backtest_calibration_method.py`  
**Purpose**: Compare static calibration vs. moving walk-forward calibration.  
**Setup**: Variant C, 1.0× ATR, Method 1 stops, 52 weeks.

| Method | Avg Return |
|--------|------------|
| Static calibration | +0.854% |
| Moving walk-forward | +0.854% |

**Finding**: Both methods produce identical returns — calibration only affects Kelly position sizing, not whether a trade is entered.  
**Decision**: Use moving walk-forward calibration for methodological consistency.

**Note on live vs. backtest correction**: The live system shows a +5.3pp correction; the backtest shows −9pp. The ~14pp disagreement is explained by the news sentiment layer which is active in live but not in backtest. Use the live correction figure for the running programme.

---

### 5. Variant A vs. C Final Validation
**Script**: `backtest_a_vs_c.py`  
**Purpose**: Clean head-to-head comparison after all bug fixes applied.  
**Setup**: Monday open entry → Friday close, 52 weeks, 260 picks.

| Variant | Avg Return | Worst Week | Picks < −5% |
|---------|------------|------------|-------------|
| A (no stops) | +0.642% | −8.031% | 15 |
| C (stops only) | +0.696% | −3.888% | 4 |
| **C minus A** | **+0.054pp** | **+4.143pp improvement** | **−11 picks** |

**Verdict**: Variant C confirmed — stops add both return and risk protection.

---

### 6. Weekly Trading Window Comparison
**Script**: `backtest_weekly_window.py`  
**Purpose**: Determine which day-of-week entry/exit window produces the best returns.  
**Setup**: Variant C, 1.0× ATR, 51 weeks (14-day data cutoff for fair cross-window comparison).

| Window | Entry | Exit | Avg Return |
|--------|-------|------|------------|
| 1 | Monday open | Friday close | +0.713% |
| **2** | **Tuesday open** | **Monday close** | **+0.760%** ← best |
| 3 | Wednesday open | Tuesday close | +0.654% |
| 4 | Thursday open | Wednesday close | +0.625% |
| 5 | Friday open | Thursday close | +0.483% |
| 6 | Friday close | Following Friday open | +0.297% |

**Finding**: Returns decrease monotonically as the entry window shifts later in the week.  
**Finding**: Window 6 (holding over the weekend) costs approximately −0.416pp vs. Window 5. Stocks systematically gap down over weekends.  
**Decision**: Adopt Window 2 — Tuesday open entry, Monday close exit.

**Implementation note**: Stop price for Window 2 is `Monday close − 1× ATR`, which is calculable before Tuesday's open. Entry-day close is included in stop monitoring.

---

### 7. Gap-Down Filter
**Script**: `backtest_gap_filter.py`  
**Purpose**: Test whether skipping picks that gap down through the stop on entry day improves returns.  
**Setup**: Window 2, 1.0× ATR, 51 weeks. Gap-down defined as: Tuesday open ≤ Monday close − 1× ATR.

| Option | Behaviour | Picks | Avg Return |
|--------|-----------|-------|------------|
| A | Buy all regardless | 255 | **+0.760%** ← best |
| B | Skip gap-downs, no replacement | 250 | +0.629% |
| C | Skip gap-downs, replace with next best | 255 | +0.600% |

**Gap-down pick detail** (Option A): 5 picks (2.0% of total), avg return +0.732%, all 5 positive outcomes.  
**Finding**: Gap-down picks perform in line with the overall average. Skipping them reduces returns.  
**Decision**: Buy all 5 picks every week regardless of gap.

---

### 8. Sector Diversification Rule
**Script**: `backtest_sector_diversification.py`  
**Purpose**: Determine whether relaxing the 1-pick-per-sector rule improves returns by allowing the model to select multiple highly-scored picks from the same sector.  
**Setup**: Window 2 (Tuesday open → Monday close), 1.0× ATR stops, 52 weeks, 255 picks per strategy.

| Metric | A: Max 1/sector | B: Max 2/sector | C: No limit |
|--------|----------------|----------------|-------------|
| Avg return / pick | **+0.740%** | +0.540% | +0.363% |
| Profitable trades | **52.5%** | 50.6% | 48.6% |
| Directional accuracy | **54.9%** | 54.5% | 52.9% |
| Target hit rate | 29.8% | **30.2%** | 27.8% |
| Stop-out rate | **21.2%** | 23.5% | 25.1% |
| Best week | 11.05% | 11.05% | 11.05% |
| Worst week | −4.86% | **−4.54%** | **−4.54%** |
| Std dev (picks) | 4.348% | 4.046% | **4.020%** |
| Std dev (weekly) | 2.625% | **2.530%** | 2.570% |
| Top sector concentration | **18.0%** | 30.2% | 34.9% |

**Finding**: The current 1-per-sector rule produces the highest average return by a significant margin (+0.74% vs +0.54% vs +0.36%), despite having the highest sector std dev and worst single-week loss.

**Finding**: Relaxing the sector limit increases stop-out rate. When the model scores multiple stocks highly within one sector, those signals are partially correlated — the same sector momentum is being counted multiple times. Forcing diversification selects stocks with more independent signals, which hold up better mid-week.

**Finding**: The improvement in worst-week from A to B/C (−4.86% vs −4.54%) is modest (+0.32pp) and does not justify sacrificing 0.20pp of average weekly return.

**Decision**: Keep the current `max 1 per sector` rule. The diversification constraint is not just a risk management nicety — it actively improves returns by forcing the model to find genuinely independent signals across sectors.

---

### 9. Score Threshold Analysis
**Script**: `analyse_score_threshold.py`  
**Purpose**: Determine whether low-scoring picks drag down overall returns, and whether there is a minimum score threshold below which picks should be skipped.  
**Setup**: Analysis of existing `lse_backtest_technical.csv` — no new backtest required.

**Score range in backtest**: 58–110, avg 84.4, median 85.

| Quartile | Score range | Picks | Avg return |
|----------|-------------|-------|------------|
| Q1 (lowest 25%) | 58–80 | 63 | **+1.893%** |
| Q2 | 80–85 | 63 | +0.948% |
| Q3 | 85–90 | 63 | +0.200% |
| Q4 (highest 25%) | 90–110 | 66 | −0.133% |

**Finding**: Returns decline monotonically as score increases. The lowest-scoring picks outperform the highest-scoring picks by over 2pp.

**Finding**: Every candidate minimum threshold tested made returns worse — it would retain more high-scoring picks while removing low-scoring ones that actually outperform.

**Decision**: Do not add a minimum score threshold. The problem is an upper bound, not a lower bound.

---

### 10. Score Sweet Spot Analysis
**Script**: `analyse_score_sweetspot.py`  
**Purpose**: Identify the optimal score range more precisely and test whether it is stable across time.  
**Setup**: Analysis of existing backtest CSV, split into two chronological halves.

| Score band | Picks | Avg return |
|------------|-------|------------|
| 65–70 | 5 | +4.854% (only 5 picks — likely noise) |
| 75–80 | 35 | +0.955% |
| 80–85 | 41 | **+1.143%** |
| 85–90 | 99 | +0.712% |
| 90–95 | 38 | −0.399% |
| 95–100 | 30 | −0.129% |

**Finding**: The genuine sweet spot is **75–88**, consistent across both halves of the backtest. Returns turn negative consistently around **score 90** in both halves — this is the meaningful ceiling, not a target to aim for.

**Temporal stability**: Peak band shifts by only 10 points between halves — classified as stable. A fixed cap at 90 is reliable.

**Overall return trend**: First half avg +1.160%, second half +0.291%. Returns declined in the second half, possibly reflecting a shift to more range-bound market conditions in late 2025.

---

### 11. Ranking Method Comparison
**Script**: `backtest_ranking_method.py`  
**Purpose**: Test whether an ideal score ranking (proximity to 80) or hard selection cap outperforms the current highest-score-wins approach.  
**Setup**: 52 weeks, 4 strategies, sector diversification applied before ranking in all cases.

| Metric | A: Current | B: Capped <90 | C: Ideal ~80 | D: Ideal+Cap |
|--------|-----------|--------------|-------------|-------------|
| Avg return / pick | +0.72% | **+0.903%** | +0.749% | +0.748% |
| Profitable trades | 52.2% | **54.9%** | 52.9% | 53.3% |
| Directional accuracy | 54.5% | **56.9%** | 54.9% | 55.3% |
| Stop-out rate | **21.2%** | 22.0% | 22.0% | 22.0% |
| Worst week | **−4.86%** | −7.192% | −5.794% | −5.794% |
| Std dev (weekly) | **2.599%** | 2.936% | 2.832% | 2.862% |

**Finding**: Hard cap at 90 (B) produces the highest avg return (+0.903% vs +0.720% — +0.183pp improvement) by forcibly replacing over-extended picks with different stocks.

**Finding**: Ideal score ranking (C/D) outperforms current but underperforms the cap. Proximity-to-80 unnecessarily penalises picks scoring 81–89, which are all in the healthy range.

**Finding**: C and D are virtually identical, confirming the cap adds no value on top of proximity ranking.

**Key trade-off**: Strategy B's worst week (−7.192%) is significantly worse than A (−4.860%). Requires further investigation before implementation.

---

### 12. Signal Contribution Analysis
**Script**: `analyse_signals.py`  
**Purpose**: Identify which signals drive over-extension in 90+ picks and which are genuinely predictive of positive returns.

**Per-signal performance (avg return when fired):**

| Signal | Points | Avg ret when fired | Avg ret when not fired |
|--------|--------|--------------------|----------------------|
| BB near lower band | 8 | **+9.687%** | +0.610% |
| EMA partial | 10 | **+3.802%** | +0.604% |
| Momentum − | 5 | **+2.087%** | +0.575% |
| MACD rising | 15 | +1.580% | +0.384% |
| EMA full align | 20 | +0.814% | +0.456% |
| RSI healthy | 20 | +0.731% | −1.080% |
| OBV rising | 10 | +0.716% | +0.731% |
| Momentum + | 10 | +0.542% | +1.415% |
| Stochastic | 10 | +0.538% | +1.532% |
| BB mid-band | 15 | +0.285% | +1.166% |
| **MACD crossover** | **25** | **−0.020%** | **+1.362%** |

**Finding**: MACD crossover is the worst-performing signal (−0.020% when fired) yet carries the highest point value (25). It fires in **94% of 90+ picks** vs only **29% of sweet spot picks** — a +65.3pp gap, the largest of any signal.

**Finding**: The best signals are early-stage or mean-reversion: BB near lower band, EMA partial, Momentum −. They fire before the move. MACD crossover fires after it.

**Finding**: Firing more signals simultaneously does not improve returns — 4-signal picks (+6.350%) significantly outperform 6-signal picks (+0.669%).

---

### 13. MACD Weighting Comparison
**Script**: `backtest_macd_weighting.py`  
**Purpose**: Test whether reducing or contextualising the MACD crossover weight fixes over-extension more elegantly than a hard selection cap.

| Metric | A: Current | B: Reduced +8 | C: Capped score | D: Context-aware |
|--------|-----------|--------------|----------------|-----------------|
| MACD crossover % | 48% | 4% | 48% | 0% |
| Avg return / pick | +0.720% | **+0.725%** | +0.689% | +0.680% |
| Worst week | **−4.860%** | −7.192% | **−4.860%** | −7.192% |
| Std dev (weekly) | **2.599%** | 2.634% | 2.624% | 2.656% |

**Finding**: Reducing MACD crossover to +8 gives only +0.005pp return improvement while worsening worst-week by 2.3pp and stop-out rate by 3.5pp. Not worth it.

**Finding**: Score capping (C) selects the same picks as A — stocks that score 95 still rank highly after capping because the cap compresses scores but does not change selection order.

**Key insight**: Changing MACD weighting does not meaningfully change which stocks get selected. The stocks that score 90+ would mostly still be top picks on their other signals alone. The only mechanism that improved returns was forcibly replacing 90+ picks with different stocks.

**Conclusion**: The hard **selection cap at 90** (ranking method test Strategy B) is the correct implementation. Signal weight changes are the wrong lever — selection is the right lever.

**Note**: This conclusion was revised following Tests 14 and 15 — see below.

---

### 14. Score Cap Optimisation
**Script**: `backtest_score_cap.py`  
**Purpose**: Find the optimal cap value by testing caps 90, 89, 88, 87, 86, 85, 83 against a no-cap baseline.  
**Setup**: Cap applied before diversification (cap-then-diversify ordering). 52 weeks.

**Key discovery**: Scores in the LSE universe only exist at multiples of 5 (because all signal point values are multiples of 5). This means caps 86–90 are all equivalent (exclude the same stocks), and caps 83–85 are all equivalent. The effective cap choices are therefore only three distinct options: cap at 90+, cap at 85+, or cap at 80+.

| Cap | Effective exclusion | Avg return | Worst week |
|-----|--------------------|-----------|-----------:|
| No cap | Nothing | +0.720% | −4.860% |
| 90–86 | Scores 90, 95, 100, 105 | +0.707% | −7.192% |
| 85–83 | Also excludes score 85 | +0.775% | −4.822% |

**Finding**: Cap at 85 (cap-then-diversify) beats no cap on both return and worst week. Cap at 90 (cap-then-diversify) underperforms no cap on return while worsening worst week — suggesting the cap-then-diversify ordering is not optimal.

---

### 15. Cap Ordering Comparison
**Script**: `backtest_cap_ordering.py`  
**Purpose**: Directly compare cap-then-diversify (X) vs diversify-then-cap (Y) orderings at cap 85 and cap 90.  
**Setup**: 5 strategies, 52 weeks.

| Strategy | Ordering | Avg return | Worst week | Std dev (weekly) |
|----------|---------|-----------|-----------|-----------------|
| A: No cap | — | +0.720% | −4.860% | 2.599% |
| B: Cap 90 | cap→div | +0.707% | −7.192% | 2.629% |
| C: Cap 90 | div→cap | +0.903% | −7.192% | 2.936% |
| D: Cap 85 | cap→div | +0.775% | −4.822% | 2.724% |
| **E: Cap 85** | **div→cap** | **+1.119%** | −6.078% | 2.982% |

**Finding**: Y ordering (diversify-then-cap) outperforms X at both cap levels — +0.196pp better at cap 90, +0.344pp better at cap 85. Pre-diversifying before capping ensures replacement picks come from genuinely different sectors rather than clustering in the same sectors as the picks being replaced.

**Finding**: Cap 85 div→cap (E) is the clear winner on return (+1.119%) and profitable trades (54.1%). Its worst week (−6.078%) is worse than no-cap (−4.860%) but significantly better than cap 90 div→cap (−7.192%).

**Finding**: The ordering question is resolved — diversify-then-cap is the correct approach, and the 0.903% from the ranking method test was a genuine result not a methodological artefact.

**Decision**: Implement **cap 85, diversify-then-cap** in the live programme:
- Diversify full universe to pool of 15 (3x TOP_N)
- Remove any pick scoring 85+ from that pool
- Fill gaps from remaining pool picks below 85, least over-extended first
- Take top 5 from eligible remainder by score descending

**Outstanding question**: What drove the −6.078% worst week? Was it a market-wide event or concentrated in the replacement picks? See `analyse_worst_week.py`.

---

### 16. Worst Week Analysis — Strategy E
**Script**: `analyse_worst_week.py`  
**Purpose**: Investigate whether the −6.078% worst week in Strategy E was caused by the cap/replacement picks or by external market conditions.

**Worst week identified**: 2025-03-31 (the Trump tariff announcement week).

| | Strategy E | No-cap | FTSE 100 |
|--|-----------|--------|---------|
| Avg return | −6.078% | −3.712% | −7.833% |

**Strategy E picks that week:**

| Ticker | Sector | Score | Replacement? | Return | Notes |
|--------|--------|-------|-------------|--------|-------|
| ENOG | Energy | 80 | No | −10.68% | Stop triggered |
| CPG | Industrials | 70 | No | −8.52% | Stop triggered |
| CSN | Insurance | 70 | No | −5.04% | Stop triggered |
| RKT | Materials | 75 | No | −3.31% | Stop triggered |
| HLN | Pharma | 75 | No | −2.84% | Stop triggered |

**Finding**: No cap replacements were needed this week — all 5 picks already scored below 85. The cap played no role in this week's outcome.

**Finding**: The FTSE 100 fell −7.833% that week (Trump tariff announcement). Every pick across both strategies hit its stop loss. Strategy E actually **outperformed the market by +1.755pp**.

**Finding**: The only reason no-cap returned −3.712% vs E's −6.078% was one pick — BHMG (+3.21%) which happened to be resilient during the crash. Three of the five picks were identical across both strategies. This is luck rather than signal quality.

**Finding**: Z-score of −2.41 — a once-per-year statistical event, not a regular occurrence.

**Conclusion**: The worst week in Strategy E was entirely market-driven, not caused by the cap or replacement logic. The strategy outperformed the index during a severe market crash. This clears the main concern about Strategy E's downside risk.

**Note on live programme**: The news sentiment layer would likely have caught the tariff announcement, applying negative macro adjustments across most sectors that week. The live programme may have avoided some of this loss — the backtest cannot capture this because it runs on technicals only.

---

## Things Tested and Rejected

| Feature | Reason Rejected |
|---------|----------------|
| Upper limit prices (sell if stock hits target) | Systematically cuts winners short; destroyed returns in every test |
| Hard stop orders with broker | Unreliable execution; mental stops at triggering close outperform |
| Wider stops (1.5×–2.5× ATR) | Lower returns than 1.0× ATR at every width tested |
| Gap-down filter (skip gapped picks) | Gap-down picks perform as well as average; skipping reduces total return |
| Holding over weekend | Weekend gap-down effect costs ~0.4pp per occurrence |
| Selling at next open after stop triggers | Worse than same-day close execution |
| Gap-up recovery hold (sell next open only if still below stop) | Worse than method 2; gap recoveries don't add value |
| Relaxed sector limit (max 2/sector or no limit) | Increases stop-out rate and reduces avg return — correlated signals within sectors inflate apparent score quality |
| Minimum score threshold | Every threshold tested made returns worse — low-scoring picks outperform high-scoring ones; the problem is a ceiling not a floor |
| Ideal score ranking (proximity to 80) | Outperforms current but underperforms hard cap — unnecessarily penalises healthy 81–89 picks by treating distance from 80 as a negative |
| Reducing MACD crossover weight (+8 instead of +25) | Marginal return improvement (+0.005pp) offset by significantly worse worst-week (−7.192%) and higher stop-out rate; wrong lever |
| Context-aware MACD penalty (flips to −15 at 5+ signals) | Worse than reduced weight on all metrics; over-aggressive in practice |
| Score cap at 88 (compress score, keep same picks) | Selects the same picks as current — compressing scores does not change ranking order; selection cap is needed, not score cap |
| Cap at 90, cap-then-diversify | Underperforms no-cap on return (+0.707% vs +0.720%) while worsening worst week — replacement picks cluster in same sectors as removed picks |
| Cap at 90, diversify-then-cap | Better return (+0.903%) but worst week of −7.192% not justified when cap 85 div→cap achieves +1.119% with −6.078% worst week |
| Cap at 85, cap-then-diversify | Modest improvement over no cap but same ordering flaw as cap 90 — replaced by div→cap approach |

---

## Future Research

| Test | Hypothesis | Data Required |
|------|-----------|---------------|
| Sector concentration with strong sector news signal | When `sector_news_score` exceeds a threshold (e.g. >0.5), relaxing the 1-per-sector rule for that sector may outperform — a genuine macro catalyst (e.g. "oil prices rising dramatically") makes correlated picks legitimate rather than noise. The backtest only used technical signals, which cannot distinguish real catalysts from momentum double-counting. | ~50+ live weeks with `lse_news_log.csv` populated. Filter weeks by sector news score, compare concentrated vs diversified outcomes for those weeks only. |
| Sector concentration with strong macro news signal | The programme already scores each sector differently based on macro event type (e.g. rate cuts benefit Banking/FinServices, commodity shocks benefit Energy/Mining — see `SECTOR_SENSITIVITY` in `config.py`). When macro sentiment strongly favours a cluster of related sectors, the 1-per-sector rule may be unnecessarily restrictive across those sectors. Test whether weeks with high `macro_score` and a clear event type (geopolitical, inflation, etc.) would benefit from allowing 2 picks from macro-favoured sectors. Distinct from the sector news test above — macro affects multiple sectors simultaneously whereas sector news is sector-specific. | ~50+ live weeks with `lse_news_log.csv` populated, with sufficient macro events of each type to compare subgroups. |

---

## Known Limitations & Caveats

- **Backtests use closing prices for stop checks**, not intraday. A stock could breach the stop intraday and recover by close; the model would not trigger. In practice, monitor live prices if possible.
- **No transaction costs modelled.** Spread, commission, and stamp duty will reduce real returns.
- **Kelly sizing not used in backtests.** All picks treated as equal £1,000 positions for comparability. Live programme uses Kelly-weighted sizing.
- **News sentiment layer not in backtests.** The live calibration correction (+5.3pp) and the backtest correction (−9pp) differ by ~14pp for this reason.
- **51 weeks of data** (not 52) used in Window 2 onwards due to 14-day data cutoff for fair cross-window comparison.
- **Universe bias**: only picks that the screener actually selected are tested — no counterfactual for picks not made.
- **BACKTEST_END_DATE must be a Monday.** Currently set to `"2026-03-02"`. Update this date in `config.py` to re-run against a later dataset.
- **Directional accuracy in `backtest_gap_filter.py` is misleading.** That script computes `went_up` as `exit_price > entry_price`, where `exit_price` may be a stop-triggered close. This means every stop-out is counted as a wrong prediction by construction, producing an artificially low figure (50.2%). The true directional accuracy — whether the stock's end-of-window close was higher than its entry price, regardless of stops — was 55–60% in earlier backtests and is the correct measure of the model's forecasting ability. The v9.0 programme reports this correctly: P(rise) > 50% predicted up, Monday close > Tuesday open = actually went up.
