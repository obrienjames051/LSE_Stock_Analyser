# LSE Screener — Research & Strategy Documentation

This document records the backtesting research conducted to determine the optimal trading strategy for the LSE screener. It covers the confirmed strategy rules, key findings from each experiment, things tested and rejected, and the baseline performance figures used in the live programme.

---

## Confirmed Strategy (v8.0)

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

---

## Known Limitations & Caveats

- **Backtests use closing prices for stop checks**, not intraday. A stock could breach the stop intraday and recover by close; the model would not trigger. In practice, monitor live prices if possible.
- **No transaction costs modelled.** Spread, commission, and stamp duty will reduce real returns.
- **Kelly sizing not used in backtests.** All picks treated as equal £1,000 positions for comparability. Live programme uses Kelly-weighted sizing.
- **News sentiment layer not in backtests.** The live calibration correction (+5.3pp) and the backtest correction (−9pp) differ by ~14pp for this reason.
- **51 weeks of data** (not 52) used in Window 2 onwards due to 14-day data cutoff for fair cross-window comparison.
- **Universe bias**: only picks that the screener actually selected are tested — no counterfactual for picks not made.
- **BACKTEST_END_DATE must be a Monday.** Currently set to `"2026-03-02"`. Update this date in `config.py` to re-run against a later dataset.
- **Directional accuracy in `backtest_gap_filter.py` is misleading.** That script computes `went_up` as `exit_price > entry_price`, where `exit_price` may be a stop-triggered close. This means every stop-out is counted as a wrong prediction by construction, producing an artificially low figure (50.2%). The true directional accuracy — whether the stock's end-of-window close was higher than its entry price, regardless of stops — was 55–60% in earlier backtests and is the correct measure of the model's forecasting ability. The v8.0 programme will report this correctly: P(rise) > 50% predicted up, Monday close > Tuesday open = actually went up.
