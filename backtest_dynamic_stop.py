#!/usr/bin/env python3
"""
backtest_dynamic_stop.py
-------------------------
Tests whether a trailing stop improves on pure Variant C (stops only,
hold to Friday). Variant C is the confirmed best exit strategy: stops
protect the downside while letting winners run freely to Friday close.

The risk with pure C is a stock that rises strongly mid-week then falls
back to near entry by Friday -- the gain is lost. A trailing stop that
locks in a portion of mid-week gains would capture this.

Four variants are tested:

  C-base        -- Pure Variant C. Stop fixed at original level.
                   Baseline for this test. Should match exit strategy results.

  C-trail-cons  -- Conservative trailing stop.
                   Trigger: stock closes >= entry + 1.5 * ATR above entry.
                   On trigger: stop moves to breakeven (entry price).
                   Thereafter: stop trails at entry + 0.5 * (peak - entry).
                   Gives the stock plenty of room before locking in any gain.

  C-trail-mod   -- Moderate trailing stop.
                   Trigger: stock closes >= entry + 1.0 * ATR above entry.
                   On trigger: stop moves to breakeven.
                   Thereafter: stop trails at entry + 0.5 * (peak - entry).

  C-trail-agg   -- Aggressive trailing stop.
                   Trigger: stock closes >= entry + 0.5 * ATR above entry.
                   On trigger: stop moves to breakeven.
                   Thereafter: stop trails at entry + 0.5 * (peak - entry).
                   Responds quickly to any meaningful upward move.

All variants:
  - Use Monday-anchored sim_dates (reproducible picks across runs)
  - Use the same pre-scored weekly picks
  - Check daily closing prices only (conservative, realistic)
  - Never move the stop downward
  - Sell at Friday close if stop not triggered

Results saved to lse_ds_<variant>.csv.
Run dynamic_stop_diagnostic.py to compare results.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_dynamic_stop.py
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
    )
    from lse_analyser.screener import diversify
    from lse_analyser.sizing import calculate_allocations
    from lse_analyser.tickers import get_tickers
except Exception as e:
    print(f"Import error: {e}")
    import traceback
    traceback.print_exc()
    raise

TRADING_DAYS = 5

VARIANTS = [
    {
        "label":         "C-base",
        "display":       "C-base        -- Pure Variant C (fixed stop, hold to Friday)",
        "trail_trigger": None,   # No trailing -- pure C
        "trail_pct":     0.5,
        "output_file":   "lse_ds_base.csv",
    },
    {
        "label":         "C-trail-cons",
        "display":       "C-trail-cons  -- Trailing stop, trigger at 1.5x ATR above entry",
        "trail_trigger": 1.5,
        "trail_pct":     0.5,
        "output_file":   "lse_ds_cons.csv",
    },
    {
        "label":         "C-trail-mod",
        "display":       "C-trail-mod   -- Trailing stop, trigger at 1.0x ATR above entry",
        "trail_trigger": 1.0,
        "trail_pct":     0.5,
        "output_file":   "lse_ds_mod.csv",
    },
    {
        "label":         "C-trail-agg",
        "display":       "C-trail-agg   -- Trailing stop, trigger at 0.5x ATR above entry",
        "trail_trigger": 0.5,
        "trail_pct":     0.5,
        "output_file":   "lse_ds_agg.csv",
    },
]

OUTPUT_HEADERS = [
    "run_date", "ticker", "sector", "score",
    "entry_price", "original_stop", "atr",
    "exit_price", "exit_day", "exit_reason",
    "peak_close", "final_stop", "stop_moved",
    "return_pct", "prob", "allocation_pct",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("=" * 62)
    print("  Dynamic Stop Backtest")
    print("=" * 62)
    print()
    print("  Tests trailing stop variants against pure Variant C.")
    print("  Trailing stop logic:")
    print("    1. Trigger: stock closes X * ATR above entry")
    print("    2. On trigger: stop moves to breakeven (entry)")
    print("    3. Thereafter: stop = entry + 0.5 * (peak_close - entry)")
    print("    4. Stop never moves down, only up")
    print("    5. Sell at Friday close if stop never triggered")
    print()
    print("  Variants:")
    for v in VARIANTS:
        print(f"    {v['display']}")
    print()
    print("  Uses Monday-anchored sim_dates for reproducibility.")
    print("  Estimated time: 15-20 minutes.")
    print()

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        print("  Cancelled.")
        return

    tickers = get_tickers()
    print(f"\n  Ticker universe: {len(tickers)} stocks")
    print("  Downloading historical price data...")
    price_data = _download_all_prices(tickers)
    print(f"  Downloaded data for {len(price_data)} tickers.\n")

    now = datetime.now()
    now = now - timedelta(days=now.weekday())  # anchor to Monday

    print("  Pre-scoring all weeks (shared across all variants)...")
    weekly_picks = _prescore_all_weeks(tickers, price_data, now)
    total = sum(len(v) for v in weekly_picks.values())
    print(f"  {total} picks across {len(weekly_picks)} weeks.\n")

    for variant in VARIANTS:
        print(f"  --- {variant['display']} ---")
        all_results = []

        for sim_date_str, picks in weekly_picks.items():
            sim_date_pd = pd.Timestamp(sim_date_str)
            for pick in picks:
                result = _simulate(pick, price_data, sim_date_pd, variant)
                if result:
                    all_results.append(result)

        _apply_kelly_to_results(all_results)
        _save_results(all_results, variant["output_file"])
        _print_summary(all_results)
        print()

    print("  All variants complete.")
    print("  Run dynamic_stop_diagnostic.py to compare results.\n")


# ---------------------------------------------------------------------------
# Pre-scoring
# ---------------------------------------------------------------------------

def _prescore_all_weeks(tickers, price_data, now):
    weekly_picks = {}
    for week_offset in range(BACKTEST_WEEKS_TECHNICAL, 0, -1):
        sim_date     = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=7)
        if outcome_date > now:
            continue

        sim_date_pd = pd.Timestamp(sim_date)
        print(f"    Scoring week of {sim_date.strftime('%Y-%m-%d')}...", end="\r", flush=True)

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
        weekly_picks[sim_date.strftime("%Y-%m-%d %H:%M")] = top

    print()
    return weekly_picks


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def _simulate(pick, price_data, sim_date_pd, variant):
    """
    Simulate a single pick with the given trailing stop variant.

    Stop logic:
      - Starts at original stop price (fixed, as scored)
      - Once daily close exceeds entry + (trail_trigger * ATR):
          stop moves to entry (breakeven) -- locks in no-loss
          then trails: stop = entry + 0.5 * (peak_close - entry)
      - Each subsequent day: stop = max(current_stop,
                                        entry + trail_pct * (peak_close - entry))
      - Stop never moves down
      - If stop hit: exit at stop price
      - If week ends without stop hit: exit at Friday close
    """
    ticker         = pick["ticker"]
    entry_price    = pick["price"]
    original_stop  = pick["stop"]
    atr            = pick.get("atr", abs(entry_price - original_stop))

    trail_trigger  = variant["trail_trigger"]
    trail_pct      = variant["trail_pct"]

    ticker_key = ticker + ".L"
    df = price_data.get(ticker_key)
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

    outcome_date_pd = sim_date_pd + pd.Timedelta(days=7)

    current_stop  = original_stop
    peak_close    = entry_price   # track highest close seen
    trailing      = False         # has trailing been activated?
    stop_moved    = False

    exit_price  = None
    exit_day    = None
    exit_reason = None

    for day_idx, (date, bar) in enumerate(future_bars.iterrows(), 1):
        day_close = float(bar["close"])

        # Update peak
        if day_close > peak_close:
            peak_close = day_close

        # Check whether trailing stop should activate (only for trail variants)
        if trail_trigger is not None and not trailing:
            trigger_price = entry_price + trail_trigger * atr
            if day_close >= trigger_price:
                trailing   = True
                stop_moved = True

        # Update trailing stop if active
        if trailing:
            trail_stop = entry_price + trail_pct * (peak_close - entry_price)
            # Stop only ever moves up
            if trail_stop > current_stop:
                current_stop = trail_stop

        # Check stop hit (using today's close)
        if day_close <= current_stop:
            exit_price  = current_stop
            exit_day    = day_idx
            exit_reason = "stop_hit"
            break

    # No stop triggered -- exit at Friday close
    if exit_price is None:
        closest = future_bars[future_bars.index <= outcome_date_pd]
        if closest.empty:
            closest = future_bars
        exit_price  = float(closest["close"].iloc[-1])
        exit_day    = len(closest)
        exit_reason = "friday_close"

    return_pct = (exit_price - entry_price) / entry_price * 100

    return {
        "run_date":      sim_date_pd.strftime("%Y-%m-%d %H:%M"),
        "ticker":        ticker,
        "sector":        pick["sector"],
        "score":         pick["score"],
        "entry_price":   round(entry_price, 2),
        "original_stop": round(original_stop, 2),
        "atr":           round(atr, 4),
        "exit_price":    round(exit_price, 2),
        "exit_day":      exit_day,
        "exit_reason":   exit_reason,
        "peak_close":    round(peak_close, 2),
        "final_stop":    round(current_stop, 2),
        "stop_moved":    stop_moved,
        "return_pct":    round(return_pct, 2),
        "prob":          pick["prob"],
        "allocation_pct": 0.0,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_kelly_to_results(results):
    by_week = defaultdict(list)
    for r in results:
        by_week[r["run_date"]].append(r)

    for week_picks in by_week.values():
        mock = []
        for r in week_picks:
            # Use original_stop for downside and ATR-based target for upside.
            # target = entry + ATR (same multiplier used in scoring).
            # This mirrors what the live programme uses for Kelly sizing.
            atr      = float(r.get("atr", 0)) or abs(float(r["entry_price"]) - float(r["original_stop"]))
            target   = float(r["entry_price"]) + atr
            upside   = (target - float(r["entry_price"])) / float(r["entry_price"]) * 100
            downside = (float(r["entry_price"]) - float(r["original_stop"])) / float(r["entry_price"]) * 100
            mock.append({
                "prob":        r["prob"],
                "reward_risk": round(upside / downside, 2) if downside > 0 else 1.5,
                "price":       r["entry_price"],
            })
        calculate_allocations(mock, BACKTEST_CAPITAL)
        for i, r in enumerate(week_picks):
            r["allocation_pct"] = mock[i].get("allocation_pct", 0.0)


def _save_results(results, filepath):
    if not results:
        print(f"  No results to save to {filepath}")
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"  Saved {len(results)} picks to {filepath}")


def _print_summary(results):
    if not results:
        return
    returns  = [r["return_pct"] for r in results]
    n        = len(returns)
    avg      = sum(returns) / n
    pos      = sum(1 for r in returns if r > 0)
    stops    = sum(1 for r in results if r["exit_reason"] == "stop_hit")
    fridays  = sum(1 for r in results if r["exit_reason"] == "friday_close")
    moved    = sum(1 for r in results if r["stop_moved"])
    sign     = "+" if avg >= 0 else ""
    print(
        f"  {sign}{avg:.2f}% avg  |  {pos/n*100:.1f}% positive  |  "
        f"{stops} stop hits  |  {fridays} friday closes  |  "
        f"{moved} stops moved ({moved/n*100:.1f}%)"
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
