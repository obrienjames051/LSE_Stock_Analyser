#!/usr/bin/env python3
"""
backtest_dynamic_limit.py
--------------------------
Tests whether dynamically raising the limit price (and optionally moving
the stop to breakeven) on exceptional movers improves outcomes vs baseline.

Six variants are tested across three trigger thresholds:

  Trigger 2.5x -- stock must move 2.5x expected daily rate to qualify
  Trigger 3.0x -- stock must move 3.0x expected daily rate to qualify
  Trigger 4.0x -- stock must move 4.0x expected daily rate to qualify

  For each trigger, two sub-variants:
    [limit only]         -- raise limit using momentum projection, stop unchanged
    [limit + breakeven]  -- raise limit AND move stop to entry price (breakeven)

The stop only ever moves to breakeven -- never higher -- so exceptional movers
can still run freely to the new limit without being stopped out prematurely.

Non-qualifying picks (the majority) are completely unchanged from baseline.

Uses Option A (momentum projection) as the limit calculation method:
  new_limit = entry + (original_target - entry) * (5 / days_elapsed)

Uses a 5-day trading window (Monday open to Friday close).

Results saved to lse_dl_<variant>.csv for each of the 6 variants.
Compare against baseline using dynamic_limit_diagnostic.py.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_dynamic_limit.py
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
        "label":       "2.5x_limit_only",
        "trigger_mult": 2.5,
        "move_stop":    False,
        "output_file":  "lse_dl_2p5x_limit.csv",
        "display":      "Trigger 2.5x  |  Limit only",
    },
    {
        "label":       "2.5x_limit_and_stop",
        "trigger_mult": 2.5,
        "move_stop":    True,
        "output_file":  "lse_dl_2p5x_limit_stop.csv",
        "display":      "Trigger 2.5x  |  Limit + breakeven stop",
    },
    {
        "label":       "3.0x_limit_only",
        "trigger_mult": 3.0,
        "move_stop":    False,
        "output_file":  "lse_dl_3p0x_limit.csv",
        "display":      "Trigger 3.0x  |  Limit only",
    },
    {
        "label":       "3.0x_limit_and_stop",
        "trigger_mult": 3.0,
        "move_stop":    True,
        "output_file":  "lse_dl_3p0x_limit_stop.csv",
        "display":      "Trigger 3.0x  |  Limit + breakeven stop",
    },
    {
        "label":       "4.0x_limit_only",
        "trigger_mult": 4.0,
        "move_stop":    False,
        "output_file":  "lse_dl_4p0x_limit.csv",
        "display":      "Trigger 4.0x  |  Limit only",
    },
    {
        "label":       "4.0x_limit_and_stop",
        "trigger_mult": 4.0,
        "move_stop":    True,
        "output_file":  "lse_dl_4p0x_limit_stop.csv",
        "display":      "Trigger 4.0x  |  Limit + breakeven stop",
    },
]

OUTPUT_HEADERS = [
    "run_date", "ticker", "sector", "score", "entry_price",
    "original_target", "original_stop", "final_limit", "final_stop",
    "exit_price", "exit_day", "exit_reason",
    "return_pct", "limit_raised", "stop_moved",
    "prob", "allocation_pct",
]


def run():
    print("=" * 62)
    print("  Dynamic Limit Backtest  (v2 -- 6 variants)")
    print("=" * 62)
    print()
    print("  Tests dynamic limit raising on exceptional movers only.")
    print("  Three trigger thresholds x two stop variants = 6 runs.")
    print("  Non-qualifying picks are completely unchanged from baseline.")
    print()
    print("  Trigger thresholds: 2.5x / 3.0x / 4.0x expected daily rate")
    print("  Stop variants: limit only / limit + move stop to breakeven")
    print()
    print("  Pre-scores all weeks once then runs 6 simulations on cached data.")
    print("  Estimated time: 15-20 minutes.")
    print()

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        print("  Cancelled.")
        return

    tickers = get_tickers()
    print(f"\n  Ticker universe: {len(tickers)} stocks")
    print("  Downloading historical price data (a few minutes)...")
    price_data = _download_all_prices(tickers)
    print(f"  Downloaded data for {len(price_data)} tickers.\n")

    now = datetime.now()
    now = now - timedelta(days=now.weekday())  # anchor to Monday of current week

    # Score all weeks once -- shared across all 6 variants
    print("  Pre-scoring all weeks...")
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

    print("  All 6 variants complete.")
    print("  Run dynamic_limit_diagnostic.py to compare against baseline.\n")


def _prescore_all_weeks(tickers, price_data, now):
    weekly_picks = {}
    for week_offset in range(BACKTEST_WEEKS_TECHNICAL, 0, -1):
        sim_date     = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=7)
        if outcome_date > now:
            continue

        sim_date_pd = pd.Timestamp(sim_date)
        print(f"    Week of {sim_date.strftime('%Y-%m-%d')}...", end="\r", flush=True)

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


def _simulate(pick, price_data, sim_date_pd, variant):
    """
    Simulate a single pick over its 5-day window.

    Non-qualifying picks use the EXACT baseline methodology:
    single outcome price at the closest close to 7 calendar days after
    entry. No daily monitoring. This ensures unmodified picks produce
    identical results to the baseline.

    Qualifying picks (hit trigger threshold) get daily monitoring from
    the trigger day onward with the raised limit and optional stop move.
    """
    ticker          = pick["ticker"]
    entry_price     = pick["price"]
    original_stop   = pick["stop"]
    original_target = pick["target"]
    atr_distance    = original_target - entry_price
    expected_daily  = atr_distance / TRADING_DAYS

    trigger_mult = variant["trigger_mult"]
    move_stop    = variant["move_stop"]

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

    # Phase 1: scan daily closes to detect exceptional mover
    trigger_day = None
    for day_idx, (date, bar) in enumerate(future_bars.iterrows(), 1):
        day_close = float(bar["close"])
        if expected_daily > 0:
            expected_so_far = expected_daily * day_idx
            move_so_far     = day_close - entry_price
            ratio           = move_so_far / expected_so_far if expected_so_far > 0 else 0
            if ratio >= trigger_mult:
                trigger_day = day_idx
                break

    limit_raised  = False
    stop_moved    = False
    current_limit = original_target
    current_stop  = original_stop

    if trigger_day is None:
        # NOT an exceptional mover -- exact baseline methodology
        outcome_date_pd = sim_date_pd + pd.Timedelta(days=7)
        closest = future_bars[future_bars.index <= outcome_date_pd]
        if closest.empty:
            closest = future_bars
        exit_price  = float(closest["close"].iloc[-1])
        exit_day    = len(closest)
        exit_reason = "week_end_baseline"

    else:
        # IS an exceptional mover -- raise limit from trigger day onward
        projected_move = atr_distance * (TRADING_DAYS / trigger_day)
        new_limit = round(entry_price + projected_move, 2)
        if new_limit > current_limit:
            current_limit = new_limit
            limit_raised  = True

        if move_stop and entry_price > current_stop:
            current_stop = entry_price
            stop_moved   = True

        # Monitor daily closes from day after trigger
        exit_price  = None
        exit_day    = TRADING_DAYS
        exit_reason = "week_end"

        post_trigger = future_bars.iloc[trigger_day:]

        for day_idx, (date, bar) in enumerate(post_trigger.iterrows(), trigger_day + 1):
            day_close = float(bar["close"])
            if day_close <= current_stop:
                exit_price  = current_stop
                exit_day    = day_idx
                exit_reason = "stop_hit"
                break
            if day_close >= current_limit:
                exit_price  = current_limit
                exit_day    = day_idx
                exit_reason = "limit_hit"
                break

        if exit_price is None:
            exit_price = float(future_bars["close"].iloc[-1])
            exit_day   = len(future_bars)
            exit_reason = "week_end"

    return {
        "run_date":         sim_date_pd.strftime("%Y-%m-%d %H:%M"),
        "ticker":           ticker,
        "sector":           pick["sector"],
        "score":            pick["score"],
        "entry_price":      round(entry_price, 2),
        "original_target":  round(original_target, 2),
        "original_stop":    round(original_stop, 2),
        "final_limit":      round(current_limit, 2),
        "final_stop":       round(current_stop, 2),
        "exit_price":       round(exit_price, 2),
        "exit_day":         exit_day,
        "exit_reason":      exit_reason,
        "return_pct":       round((exit_price - entry_price) / entry_price * 100, 2),
        "limit_raised":     limit_raised,
        "stop_moved":       stop_moved,
        "prob":             pick["prob"],
        "allocation_pct":   0.0,
    }


def _apply_kelly_to_results(results):
    by_week = defaultdict(list)
    for r in results:
        by_week[r["run_date"]].append(r)

    for week_picks in by_week.values():
        mock = []
        for r in week_picks:
            upside   = (r["original_target"] - r["entry_price"]) / r["entry_price"] * 100
            downside = (r["entry_price"] - r["original_stop"]) / r["entry_price"] * 100
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
    returns    = [r["return_pct"] for r in results]
    n          = len(returns)
    avg        = sum(returns) / n
    pos        = sum(1 for r in returns if r > 0)
    raised     = sum(1 for r in results if r["limit_raised"])
    stops_moved = sum(1 for r in results if r["stop_moved"])
    lh         = sum(1 for r in results if r["exit_reason"] == "limit_hit")
    sh         = sum(1 for r in results if r["exit_reason"] == "stop_hit")
    we         = sum(1 for r in results if r["exit_reason"] == "week_end")
    sign       = "+" if avg >= 0 else ""
    print(
        f"  {sign}{avg:.2f}% avg  |  {pos/n*100:.1f}% positive  |  "
        f"{raised} limits raised ({raised/n*100:.1f}%)  |  "
        f"{stops_moved} stops moved  |  "
        f"exits: {lh}L / {sh}S / {we}W"
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
