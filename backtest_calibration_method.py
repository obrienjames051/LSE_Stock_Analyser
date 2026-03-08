#!/usr/bin/env python3
"""
backtest_calibration_method.py
--------------------------------
Compares two calibration approaches to determine which produces
better results when used as the basis for the programme's correction
parameter.

Both variants use the corrected Variant C exit logic (Method 1):
  - Stop at 1x ATR, exit at the triggering close price
  - No upper limits
  - Hold to Friday close if stop not triggered

Variant 1 -- Static calibration (current programme approach)
  Run the full 52-week backtest, compute a single correction factor
  at the end by comparing predicted vs actual directional accuracy.
  This is how the main programme's Phase 1 backtest currently works.
  Problem: correction is computed on the same data it will be applied
  to, so it's slightly circular.

Variant 2 -- Moving calibration (walk-forward)
  Use the first BURN_IN_WEEKS weeks to compute an initial correction.
  Apply that correction to week BURN_IN_WEEKS+1, observe the result,
  update the correction, apply to week BURN_IN_WEEKS+2, and so on.
  At each step, the correction only uses information available at that
  point in time -- no look-ahead bias.
  This matches how the live system works: it updates the correction as
  each new pick resolves.

The key output is:
  - The correction factor each variant would produce
  - Whether the moving correction converges or oscillates
  - The week-by-week correction trace for Variant 2
  - Whether applying the moving correction in real time changes the
    Kelly-weighted returns (since position sizing depends on probability)

Run from LSE_Stock_Analyser/ with:
  python3 backtest_calibration_method.py
  python3 calibration_method_diagnostic.py
"""

import csv
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from lse_analyser.backtest import _download_all_prices, _score_historical
    from lse_analyser.config import (
        ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER,
        BACKTEST_WEEKS_TECHNICAL, BACKTEST_CAPITAL,
        BACKTEST_END_DATE,
    )
    from lse_analyser.screener import diversify
    from lse_analyser.sizing import calculate_allocations
    from lse_analyser.tickers import get_tickers
except Exception as e:
    print(f"Import error: {e}")
    import traceback
    traceback.print_exc()
    raise

TRADING_DAYS  = 5
BURN_IN_WEEKS = 8   # minimum weeks before moving correction activates
PROB_MIN      = 0.01
PROB_MAX      = 0.99

STATIC_FILE  = "lse_cal_static.csv"
MOVING_FILE  = "lse_cal_moving.csv"
TRACE_FILE   = "lse_cal_moving_trace.csv"

OUTPUT_HEADERS = [
    "run_date", "ticker", "sector", "score",
    "entry_price", "stop_price", "atr",
    "exit_price", "exit_day", "exit_reason",
    "return_pct",
    "raw_prob",          # probability as scored, before any correction
    "applied_prob",      # probability actually used for Kelly sizing
    "correction_at_time", # correction applied when this pick was sized
    "allocation_pct",
    "went_up",           # 1 if exit_price > entry_price, 0 otherwise
]

TRACE_HEADERS = [
    "after_week", "run_date", "n_picks",
    "avg_raw_prob", "actual_win_rate",
    "correction_pp", "next_week_correction",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("=" * 62)
    print("  Calibration Method Comparison Backtest")
    print("=" * 62)
    print()
    print("  Variant 1 -- Static:  one correction at end of backtest")
    print("  Variant 2 -- Moving:  correction updates each week")
    print()
    print(f"  Exit logic: Variant C (stop at close, no limits)")
    print(f"  Burn-in period: {BURN_IN_WEEKS} weeks before moving correction")
    print(f"  activates (~{BURN_IN_WEEKS * 5} picks minimum sample)")
    print()
    print("  Estimated time: 15-20 minutes.")
    print()

    confirm = input("  Press Enter to start, or 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        print("  Cancelled.")
        return

    tickers = get_tickers()
    print(f"\n  Ticker universe: {len(tickers)} stocks")
    print("  Downloading price data...")
    price_data = _download_all_prices(tickers)
    print(f"  Downloaded data for {len(price_data)} tickers.\n")

    now = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")

    # Score all weeks upfront -- both variants use identical picks
    print("  Pre-scoring all weeks...")
    weekly_scored = _prescore_all_weeks(tickers, price_data, now)
    total = sum(len(v) for v in weekly_scored.values())
    print(f"  {total} picks across {len(weekly_scored)} weeks.\n")

    # Simulate all picks once -- outcome is the same for both variants
    # The only difference is the applied_prob and allocation used for sizing
    print("  Simulating outcomes (shared for both variants)...")
    weekly_outcomes = _simulate_all_weeks(weekly_scored, price_data)
    total_sim = sum(len(v) for v in weekly_outcomes.values())
    print(f"  {total_sim} picks simulated.\n")

    # --- Variant 1: Static calibration ---
    print("  --- Variant 1: Static calibration ---")
    static_results, static_correction = _apply_static_calibration(weekly_outcomes)
    _save(static_results, STATIC_FILE)
    _print_summary(static_results, static_correction, label="Static")
    print()

    # --- Variant 2: Moving calibration ---
    print("  --- Variant 2: Moving calibration ---")
    moving_results, trace = _apply_moving_calibration(weekly_outcomes)
    _save(moving_results, MOVING_FILE)
    _save_trace(trace, TRACE_FILE)
    final_correction = trace[-1]["next_week_correction"] if trace else 0.0
    _print_summary(moving_results, final_correction, label="Moving")
    _print_trace(trace)
    print()

    print("  Complete. Run calibration_method_diagnostic.py for full comparison.")


# ---------------------------------------------------------------------------
# Pre-scoring
# ---------------------------------------------------------------------------

def _prescore_all_weeks(tickers, price_data, now):
    """Score all weeks. Returns ordered dict of sim_date_str -> list of picks."""
    weekly = {}
    for week_offset in range(BACKTEST_WEEKS_TECHNICAL, 0, -1):
        sim_date     = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=7)
        if outcome_date > now:
            continue

        sim_date_pd = pd.Timestamp(sim_date)
        print(f"    Scoring {sim_date.strftime('%Y-%m-%d')}...", end="\r", flush=True)

        candidates = []
        for ticker, sector in tickers.items():
            df = price_data.get(ticker)
            if df is None or len(df) < 30:
                continue
            try:
                hist = df[df.index <= sim_date_pd].copy()
                if len(hist) < 30:
                    continue
                r = _score_historical(
                    ticker, sector, hist,
                    ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER
                )
                if r:
                    candidates.append(r)
            except Exception:
                continue

        if not candidates:
            continue

        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = diversify(candidates, 5)
        weekly[sim_date.strftime("%Y-%m-%d %H:%M")] = top

    print()
    return weekly


# ---------------------------------------------------------------------------
# Simulate outcomes (shared between both variants)
# ---------------------------------------------------------------------------

def _simulate_all_weeks(weekly_scored, price_data):
    """
    Simulate the outcome of each pick using Variant C exit logic.
    Returns the same structure but with outcome fields added.
    raw_prob is stored; applied_prob and allocation are set later
    by each calibration variant.
    """
    weekly_outcomes = {}
    for sim_date_str, picks in weekly_scored.items():
        sim_date_pd = pd.Timestamp(sim_date_str)
        week_results = []
        for pick in picks:
            r = _simulate_pick(pick, price_data, sim_date_pd)
            if r:
                week_results.append(r)
        if week_results:
            weekly_outcomes[sim_date_str] = week_results
    return weekly_outcomes


def _simulate_pick(pick, price_data, sim_date_pd):
    ticker      = pick["ticker"]
    entry_price = pick["price"]
    stop_price  = pick["stop"]
    atr         = pick.get("atr", abs(entry_price - stop_price))
    raw_prob    = pick["prob"]

    df = price_data.get(ticker + ".L")
    if df is None:
        df = price_data.get(ticker)
    if df is None:
        return None

    try:
        future_bars = df[df.index > sim_date_pd].head(TRADING_DAYS)
        if future_bars.empty:
            return None
    except Exception:
        return None

    exit_price  = None
    exit_day    = None
    exit_reason = None

    for day_idx, (date, bar) in enumerate(future_bars.iterrows(), 1):
        day_close = float(bar["close"])
        if day_close <= stop_price:
            exit_price  = day_close   # Method 1: exit at actual close
            exit_day    = day_idx
            exit_reason = "stop_close"
            break

    if exit_price is None:
        week_bars   = future_bars.head(TRADING_DAYS)
        exit_price  = float(week_bars["close"].iloc[-1])
        exit_day    = len(week_bars)
        exit_reason = "friday_close"

    return_pct = (exit_price - entry_price) / entry_price * 100
    went_up    = 1 if exit_price > entry_price else 0

    return {
        "run_date":     sim_date_pd.strftime("%Y-%m-%d %H:%M"),
        "ticker":       ticker,
        "sector":       pick["sector"],
        "score":        pick["score"],
        "entry_price":  round(entry_price, 2),
        "stop_price":   round(stop_price, 2),
        "atr":          round(atr, 4),
        "exit_price":   round(exit_price, 2),
        "exit_day":     exit_day,
        "exit_reason":  exit_reason,
        "return_pct":   round(return_pct, 2),
        "raw_prob":     raw_prob,
        "went_up":      went_up,
        # To be filled by calibration variant:
        "applied_prob":      raw_prob,
        "correction_at_time": 0.0,
        "allocation_pct":    0.0,
    }


# ---------------------------------------------------------------------------
# Variant 1: Static calibration
# ---------------------------------------------------------------------------

def _apply_static_calibration(weekly_outcomes):
    """
    Collect all picks, compute a single correction at the end,
    then apply it retroactively for Kelly sizing.
    This matches the current programme behaviour.
    """
    all_picks = []
    for picks in weekly_outcomes.values():
        all_picks.extend(picks)

    # Compute correction from all picks
    correction = _compute_correction(all_picks)

    # Apply correction to all picks for sizing
    results = []
    for week_picks in weekly_outcomes.values():
        week_copy = [dict(p) for p in week_picks]
        for p in week_copy:
            p["correction_at_time"] = correction
            p["applied_prob"] = _clamp(p["raw_prob"] + correction)
        _apply_kelly_to_week(week_copy)
        results.extend(week_copy)

    return results, correction


# ---------------------------------------------------------------------------
# Variant 2: Moving calibration
# ---------------------------------------------------------------------------

def _apply_moving_calibration(weekly_outcomes):
    """
    Walk forward week by week.
    After BURN_IN_WEEKS weeks, compute correction from all resolved picks so far.
    Apply that correction to the next week's sizing.
    Update after each week.
    """
    week_keys    = sorted(weekly_outcomes.keys())
    resolved     = []   # all picks resolved so far
    current_correction = 0.0  # no correction until burn-in complete
    results      = []
    trace        = []

    for week_idx, week_key in enumerate(week_keys):
        week_picks = [dict(p) for p in weekly_outcomes[week_key]]

        # Apply current correction to this week's sizing
        for p in week_picks:
            p["correction_at_time"] = current_correction
            p["applied_prob"] = _clamp(p["raw_prob"] + current_correction)
        _apply_kelly_to_week(week_picks)
        results.extend(week_picks)

        # Now that this week is resolved, add to history
        resolved.extend(week_picks)

        # Update correction if burn-in is complete
        if week_idx + 1 >= BURN_IN_WEEKS:
            new_correction = _compute_correction(resolved)

            trace.append({
                "after_week":           week_idx + 1,
                "run_date":             week_key[:10],
                "n_picks":              len(resolved),
                "avg_raw_prob":         round(sum(p["raw_prob"] for p in resolved) / len(resolved) * 100, 2),
                "actual_win_rate":      round(sum(p["went_up"] for p in resolved) / len(resolved) * 100, 2),
                "correction_pp":        round(current_correction, 2),
                "next_week_correction": round(new_correction, 2),
            })

            current_correction = new_correction

    return results, trace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_correction(picks):
    """
    Correction = actual win rate - avg predicted probability (in pp).
    Positive = model under-confident (reality better than predicted).
    Negative = model over-confident (reality worse than predicted).
    """
    if not picks:
        return 0.0
    # raw_prob is stored as a percentage (e.g. 63.0), convert to decimal
    avg_prob    = sum(p["raw_prob"] for p in picks) / len(picks) / 100
    actual_rate = sum(p["went_up"] for p in picks) / len(picks)
    return (actual_rate - avg_prob) * 100


def _clamp(prob_pct):
    """Clamp a probability expressed as percentage (e.g. 63.0) to [1, 99]."""
    return max(1.0, min(99.0, prob_pct))


def _apply_kelly_to_week(week_picks):
    mock = []
    for p in week_picks:
        atr      = float(p.get("atr", 0)) or abs(
            float(p["entry_price"]) - float(p["stop_price"])
        )
        target   = float(p["entry_price"]) + atr
        upside   = (target - float(p["entry_price"])) / float(p["entry_price"]) * 100
        downside = (float(p["entry_price"]) - float(p["stop_price"])) / float(p["entry_price"]) * 100
        mock.append({
            "prob":        p["applied_prob"] / 100,  # convert % to decimal for Kelly
            "reward_risk": round(upside / downside, 2) if downside > 0 else 1.5,
            "price":       p["entry_price"],
        })
    calculate_allocations(mock, BACKTEST_CAPITAL)
    for i, p in enumerate(week_picks):
        p["allocation_pct"] = mock[i].get("allocation_pct", 0.0)


def _save(results, filepath):
    if not results:
        print(f"  No results to save.")
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"  Saved {len(results)} picks to {filepath}")


def _save_trace(trace, filepath):
    if not trace:
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACE_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trace)
    print(f"  Saved correction trace ({len(trace)} entries) to {filepath}")


def _print_summary(results, correction, label):
    if not results:
        return
    returns = [float(r["return_pct"]) for r in results]
    n       = len(returns)
    avg     = sum(returns) / n
    pos     = sum(1 for r in returns if r > 0)

    allocs      = [(float(r["return_pct"]), float(r.get("allocation_pct") or 0)) for r in results]
    total_alloc = sum(a for _, a in allocs)
    kelly       = sum(ret * a for ret, a in allocs) / total_alloc if total_alloc > 0 else avg

    by_week   = defaultdict(list)
    for r in results:
        by_week[r["run_date"][:10]].append(float(r["return_pct"]))
    week_avgs = {w: sum(v)/len(v) for w, v in by_week.items()}

    print(f"  [{label}]")
    print(f"    Avg return (simple):   {avg:+.3f}%")
    print(f"    Avg return (Kelly):    {kelly:+.3f}%")
    print(f"    Directional accuracy:  {pos/n*100:.1f}%")
    print(f"    Worst week:            {min(week_avgs.values()):+.2f}%")
    print(f"    Correction produced:   {correction:+.2f}pp")


def _print_trace(trace):
    if not trace:
        return
    print(f"\n  Moving correction trace (every 4 weeks):")
    print(f"  {'Week':>5}  {'Picks':>6}  {'Avg prob':>9}  "
          f"{'Actual':>8}  {'Correction':>11}  {'Next':>8}")
    print(f"  {'-'*5}  {'-'*6}  {'-'*9}  {'-'*8}  {'-'*11}  {'-'*8}")
    for t in trace:
        if t["after_week"] % 4 == 0 or t["after_week"] == len(trace):
            print(
                f"  {t['after_week']:>5}  {t['n_picks']:>6}  "
                f"{t['avg_raw_prob']:>8.1f}%  "
                f"{t['actual_win_rate']:>7.1f}%  "
                f"{t['correction_pp']:>+10.2f}pp  "
                f"{t['next_week_correction']:>+7.2f}pp"
            )


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
