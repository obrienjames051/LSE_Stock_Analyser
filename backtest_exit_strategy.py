#!/usr/bin/env python3
"""
backtest_exit_strategy.py
--------------------------
Tests four exit strategies to determine which produces the best real-world
returns. This addresses a fundamental issue with the existing backtest which
uses only Friday close prices, ignoring whether stops or limits were hit
during the week.

Four variants:

  A -- No stops, no limits
       Hold from Monday open to Friday close regardless.
       Sell at Friday close price.
       This is the purest measure of stock selection quality.

  B -- No stops, upper limits only
       Hold from Monday open, exit at limit price if hit mid-week.
       Sell at Friday close if limit not hit.
       Tests whether taking profit at the limit improves returns.

  C -- Stops only, no upper limits
       Hold from Monday open, exit at stop price if hit mid-week.
       Sell at Friday close if stop not hit.
       Tests whether downside protection improves returns.

  D -- Stops and upper limits
       Hold from Monday open, exit at stop OR limit if hit mid-week.
       Sell at Friday close if neither triggered.
       This is the current programme's intended real-world behaviour.

All four use the same weekly picks (same scoring, same tickers, same weeks)
so the only variable is the exit strategy.

Mid-week prices are checked using daily closing prices (not intraday highs/lows)
to keep the simulation conservative and realistic for limit orders.

Results saved to lse_exit_<variant>.csv for each variant.
Run exit_strategy_diagnostic.py to compare results.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_exit_strategy.py
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
        "label":       "A",
        "display":     "A -- No stops, no limits    (hold to Friday close)",
        "use_stop":    False,
        "use_limit":   False,
        "output_file": "lse_exit_a.csv",
    },
    {
        "label":       "B",
        "display":     "B -- No stops, limits only  (sell at limit if hit)",
        "use_stop":    False,
        "use_limit":   True,
        "output_file": "lse_exit_b.csv",
    },
    {
        "label":       "C",
        "display":     "C -- Stops only, no limits  (sell at stop if hit)",
        "use_stop":    True,
        "use_limit":   False,
        "output_file": "lse_exit_c.csv",
    },
    {
        "label":       "D",
        "display":     "D -- Stops and limits       (current programme logic)",
        "use_stop":    True,
        "use_limit":   True,
        "output_file": "lse_exit_d.csv",
    },
]

OUTPUT_HEADERS = [
    "run_date", "ticker", "sector", "score", "entry_price",
    "target_price", "stop_price", "limit_price",
    "exit_price", "exit_day", "exit_reason",
    "return_pct", "prob", "allocation_pct",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("=" * 62)
    print("  Exit Strategy Comparison Backtest")
    print("=" * 62)
    print()
    print("  Tests 4 exit strategies on the same weekly picks:")
    print()
    print("  A  No stops, no limits    -- hold to Friday close")
    print("  B  No stops, limits only  -- sell at limit if hit mid-week")
    print("  C  Stops only, no limits  -- sell at stop if hit mid-week")
    print("  D  Stops and limits       -- current programme logic")
    print()
    print("  Mid-week checks use daily closing prices.")
    print("  All four variants use identical weekly picks.")
    print()
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
    now = now - timedelta(days=now.weekday())  # anchor to Monday of current week

    # Score all weeks once -- shared across all 4 variants
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

    print("  All 4 variants complete.")
    print("  Run exit_strategy_diagnostic.py to compare results.\n")


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
    Simulate a single pick using the given exit strategy.

    All variants:
      - Entry at Monday open (approximated by first available close after sim_date)
      - Default exit at Friday close (closest close to 7 calendar days later)

    Variant-specific mid-week checks using daily closing prices:
      - use_stop:  exit at stop_price if any daily close falls to or below it
      - use_limit: exit at limit_price if any daily close rises to or above it
    """
    ticker       = pick["ticker"]
    entry_price  = pick["price"]
    stop_price   = pick["stop"]
    target_price = pick["target"]
    limit_price  = pick["limit"]

    use_stop  = variant["use_stop"]
    use_limit = variant["use_limit"]

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

    exit_price  = None
    exit_day    = None
    exit_reason = None

    # Check each day's close in order
    for day_idx, (date, bar) in enumerate(future_bars.iterrows(), 1):
        day_close = float(bar["close"])

        # Stop check -- exit if close at or below stop price
        if use_stop and day_close <= stop_price:
            exit_price  = stop_price
            exit_day    = day_idx
            exit_reason = "stop_hit"
            break

        # Limit check -- exit if close at or above limit price
        if use_limit and day_close >= limit_price:
            exit_price  = limit_price
            exit_day    = day_idx
            exit_reason = "limit_hit"
            break

    # If no mid-week exit triggered, sell at Friday close
    if exit_price is None:
        closest = future_bars[future_bars.index <= outcome_date_pd]
        if closest.empty:
            closest = future_bars
        exit_price  = float(closest["close"].iloc[-1])
        exit_day    = len(closest)
        exit_reason = "friday_close"

    return_pct = (exit_price - entry_price) / entry_price * 100

    return {
        "run_date":     sim_date_pd.strftime("%Y-%m-%d %H:%M"),
        "ticker":       ticker,
        "sector":       pick["sector"],
        "score":        pick["score"],
        "entry_price":  round(entry_price, 2),
        "target_price": round(target_price, 2),
        "stop_price":   round(stop_price, 2),
        "limit_price":  round(limit_price, 2),
        "exit_price":   round(exit_price, 2),
        "exit_day":     exit_day,
        "exit_reason":  exit_reason,
        "return_pct":   round(return_pct, 2),
        "prob":         pick["prob"],
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
            upside   = (r["target_price"] - r["entry_price"]) / r["entry_price"] * 100
            downside = (r["entry_price"] - r["stop_price"]) / r["entry_price"] * 100
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
    returns   = [r["return_pct"] for r in results]
    n         = len(returns)
    avg       = sum(returns) / n
    pos       = sum(1 for r in returns if r > 0)
    stops     = sum(1 for r in results if r["exit_reason"] == "stop_hit")
    limits    = sum(1 for r in results if r["exit_reason"] == "limit_hit")
    fridays   = sum(1 for r in results if r["exit_reason"] == "friday_close")
    sign      = "+" if avg >= 0 else ""
    print(
        f"  {sign}{avg:.2f}% avg  |  {pos/n*100:.1f}% positive  |  "
        f"exits: {stops} stops / {limits} limits / {fridays} friday close"
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
