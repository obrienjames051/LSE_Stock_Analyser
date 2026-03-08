#!/usr/bin/env python3
"""
backtest_stop_execution.py
---------------------------
Tests three different methods of executing a mental stop loss to determine
which produces the best real-world returns.

All three methods use the same trigger: if a stock's daily close falls at
or below the stop price, the stop is activated. They differ only in WHEN
and HOW the position is exited after activation.

Method 1 -- Sell at close (4:15pm)
  If today's close <= stop price, sell at today's close.
  Requires manual monitoring and action just before close each day.
  This matches the current daily close simulation (+1.01% baseline).

Method 2 -- Sell at next open (no matter what)
  If today's close <= stop price, sell at tomorrow's open regardless.
  Simpler execution -- check closes each evening, place market sell
  order for the next morning. Gap risk in both directions.

Method 3 -- Sell at next open unless gap up over stop
  If today's close <= stop price:
    - If tomorrow's open > stop price: stock has recovered, hold on
      and continue monitoring for the rest of the week.
    - If tomorrow's open <= stop price: sell at tomorrow's open.
  Captures overnight recoveries while exiting genuine weakness.
  Most forgiving of the three methods.

All three methods:
  - Use 1x ATR stop (confirmed best stop width)
  - Fixed stop only (no trailing) -- isolates execution method effect
  - Monday-anchored sim_dates for reproducibility
  - 52-week backtest period (~260 picks)

Results saved to lse_se_method<n>.csv for each method.
Run stop_execution_diagnostic.py to compare results.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_stop_execution.py
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
        "label":       "Method 1",
        "display":     "Method 1  -- Sell at close if close <= stop",
        "method":      1,
        "output_file": "lse_se_method1.csv",
    },
    {
        "label":       "Method 2",
        "display":     "Method 2  -- Sell at next open (no matter what)",
        "method":      2,
        "output_file": "lse_se_method2.csv",
    },
    {
        "label":       "Method 3",
        "display":     "Method 3  -- Sell at next open unless gap up over stop",
        "method":      3,
        "output_file": "lse_se_method3.csv",
    },
]

OUTPUT_HEADERS = [
    "run_date", "ticker", "sector", "score",
    "entry_price", "stop_price", "atr",
    "exit_price", "exit_day", "exit_reason",
    "trigger_close", "trigger_day",
    "return_pct", "prob", "allocation_pct",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("=" * 62)
    print("  Stop Execution Method Comparison Backtest")
    print("=" * 62)
    print()
    print("  Tests three ways of executing a mental stop loss.")
    print()
    print("  All methods trigger when daily close <= stop price.")
    print("  They differ in how/when the position is actually exited.")
    print()
    print("  Method 1: sell at the triggering close (4:15pm manual)")
    print("  Method 2: sell at next morning's open (no exceptions)")
    print("  Method 3: sell at next open, but hold if stock gaps")
    print("            up back above the stop price overnight")
    print()
    print("  Uses 1x ATR stop, 52-week backtest, ~260 picks.")
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

    print("  Pre-scoring all weeks (shared across all methods)...")
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

        _apply_kelly(all_results)
        _save(all_results, variant["output_file"])
        _print_summary(all_results)
        print()

    print("  All methods complete.")
    print("  Run stop_execution_diagnostic.py to compare results.\n")


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
        print(f"    Scoring week of {sim_date.strftime('%Y-%m-%d')}...",
              end="\r", flush=True)

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
    Simulate a single pick using the given stop execution method.

    All methods share the same daily OHLC data. The open price of each
    bar is used as a proxy for next-day execution price in Methods 2 & 3.

    Note: yfinance daily bars include open, high, low, close for each day.
    We use:
      - bar["close"] to check the stop trigger condition
      - bar["open"] of the NEXT bar as the execution price for methods 2 & 3
    """
    ticker      = pick["ticker"]
    entry_price = pick["price"]
    stop_price  = pick["stop"]
    atr         = pick.get("atr", abs(entry_price - stop_price))
    method      = variant["method"]

    # Try .L suffix first (score_historical strips it, data stored with it)
    df = price_data.get(ticker + ".L")
    if df is None:
        df = price_data.get(ticker)
    if df is None:
        return None

    try:
        # Get TRADING_DAYS + 1 bars so we always have a "next open" available
        future_bars = df[df.index > sim_date_pd].head(TRADING_DAYS + 1)
        if future_bars.empty or len(future_bars) < 2:
            return None
    except Exception:
        return None

    outcome_date_pd = sim_date_pd + pd.Timedelta(days=7)

    # Check if open column exists (needed for methods 2 & 3)
    has_open = "open" in future_bars.columns

    exit_price    = None
    exit_day      = None
    exit_reason   = None
    trigger_close = None
    trigger_day   = None

    # We iterate over the first TRADING_DAYS bars for stop checking
    # but need access to bar i+1 for next-day open
    bars_list = list(future_bars.iterrows())

    for i, (date, bar) in enumerate(bars_list[:TRADING_DAYS]):
        day_close = float(bar["close"])
        day_num   = i + 1

        # Check stop trigger
        if day_close <= stop_price:
            trigger_close = day_close
            trigger_day   = day_num

            if method == 1:
                # Sell at the triggering close
                exit_price  = day_close
                exit_day    = day_num
                exit_reason = "stop_close"
                break

            elif method == 2:
                # Sell at next day's open, no matter what
                if i + 1 < len(bars_list):
                    next_bar   = bars_list[i + 1][1]
                    next_open  = float(next_bar["open"]) if has_open else float(next_bar["close"])
                    exit_price  = next_open
                    exit_day    = day_num + 1
                    exit_reason = "stop_next_open"
                else:
                    # No next bar available -- use trigger close as fallback
                    exit_price  = day_close
                    exit_day    = day_num
                    exit_reason = "stop_close_fallback"
                break

            elif method == 3:
                # Check next day's open:
                # - If next open > stop price: stock recovered, hold on
                # - If next open <= stop price: sell at next open
                if i + 1 < len(bars_list):
                    next_bar  = bars_list[i + 1][1]
                    next_open = float(next_bar["open"]) if has_open else float(next_bar["close"])

                    if next_open > stop_price:
                        # Gap up over stop -- stock has recovered, continue holding
                        # Reset trigger so we can trigger again on a later day
                        trigger_close = None
                        trigger_day   = None
                        continue
                    else:
                        # Gap down or flat -- sell at next open
                        exit_price  = next_open
                        exit_day    = day_num + 1
                        exit_reason = "stop_next_open"
                        break
                else:
                    # No next bar -- use trigger close as fallback
                    exit_price  = day_close
                    exit_day    = day_num
                    exit_reason = "stop_close_fallback"
                    break

    # No stop triggered -- sell at Friday close
    if exit_price is None:
        closest = future_bars[future_bars.index <= outcome_date_pd]
        if closest.empty:
            closest = future_bars.head(TRADING_DAYS)
        # Use last bar within the 5-day window
        week_bars = future_bars.head(TRADING_DAYS)
        exit_price  = float(week_bars["close"].iloc[-1])
        exit_day    = len(week_bars)
        exit_reason = "friday_close"

    return_pct = (exit_price - entry_price) / entry_price * 100

    return {
        "run_date":      sim_date_pd.strftime("%Y-%m-%d %H:%M"),
        "ticker":        ticker,
        "sector":        pick["sector"],
        "score":         pick["score"],
        "entry_price":   round(entry_price, 2),
        "stop_price":    round(stop_price, 2),
        "atr":           round(atr, 4),
        "exit_price":    round(exit_price, 2),
        "exit_day":      exit_day,
        "exit_reason":   exit_reason,
        "trigger_close": round(trigger_close, 2) if trigger_close else "",
        "trigger_day":   trigger_day if trigger_day else "",
        "return_pct":    round(return_pct, 2),
        "prob":          pick["prob"],
        "allocation_pct": 0.0,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_kelly(results):
    by_week = defaultdict(list)
    for r in results:
        by_week[r["run_date"]].append(r)

    for week_picks in by_week.values():
        mock = []
        for r in week_picks:
            atr      = float(r.get("atr", 0)) or abs(
                float(r["entry_price"]) - float(r["stop_price"])
            )
            target   = float(r["entry_price"]) + atr
            upside   = (target - float(r["entry_price"])) / float(r["entry_price"]) * 100
            downside = (float(r["entry_price"]) - float(r["stop_price"])) / float(r["entry_price"]) * 100
            mock.append({
                "prob":        r["prob"],
                "reward_risk": round(upside / downside, 2) if downside > 0 else 1.5,
                "price":       r["entry_price"],
            })
        calculate_allocations(mock, BACKTEST_CAPITAL)
        for i, r in enumerate(week_picks):
            r["allocation_pct"] = mock[i].get("allocation_pct", 0.0)


def _save(results, filepath):
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
    stops    = sum(1 for r in results if "stop" in r.get("exit_reason", ""))
    recoveries = sum(1 for r in results
                     if r.get("exit_reason") == "friday_close"
                     and r.get("trigger_day", "") != "")
    fridays  = sum(1 for r in results if r.get("exit_reason") == "friday_close")
    sign     = "+" if avg >= 0 else ""
    print(
        f"  {sign}{avg:.2f}% avg  |  {pos/n*100:.1f}% positive  |  "
        f"{stops} stop exits  |  {fridays} friday closes"
        + (f"  |  {recoveries} gap-up recoveries held" if recoveries > 0 else "")
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
