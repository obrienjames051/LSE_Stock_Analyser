#!/usr/bin/env python3
"""
backtest_stop_width.py
-----------------------
Tests different stop loss distances to find the optimal level that
protects against genuine adverse moves while absorbing normal intraday
noise.

The intraday validation test revealed that at 1x ATR stop distance,
roughly 25% of picks are stopped out intraday that would not have been
stopped on a daily close basis. This suggests the stop is too tight.

Four stop multipliers are tested, each run twice:
  - Daily close simulation (as in backtest_dynamic_stop.py)
  - Intraday hourly simulation (as in backtest_intraday_validation.py)

Stop multipliers tested:
  1.0x ATR  -- current programme default
  1.5x ATR  -- 50% wider
  2.0x ATR  -- double the current distance
  2.5x ATR  -- 2.5x the current distance

All variants use:
  - Pure fixed stop (no trailing) -- isolates the stop width effect
  - Hold to Friday close if stop not triggered
  - Monday-anchored sim_dates for reproducibility

Daily simulation covers 52 weeks.
Intraday simulation covers last 8 weeks (yfinance hourly data limit).

Results saved to lse_sw_<multiplier>_daily.csv and lse_sw_<multiplier>_intra.csv.
Run stop_width_diagnostic.py to compare results.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_stop_width.py
"""

import csv
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from lse_analyser.backtest import _download_all_prices, _score_historical
    from lse_analyser.config import (
        ATR_MULTIPLIER, LIMIT_BUFFER,
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

TRADING_DAYS   = 5
INTRADAY_WEEKS = 8
INTERVAL       = "1h"

STOP_MULTIPLIERS = [1.0, 1.5, 2.0, 2.5]

DAILY_HEADERS = [
    "run_date", "ticker", "sector", "score",
    "entry_price", "stop_price", "atr", "stop_mult",
    "exit_price", "exit_day", "exit_reason",
    "return_pct", "prob", "allocation_pct",
]

INTRA_HEADERS = DAILY_HEADERS  # same fields


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("=" * 62)
    print("  Stop Width Comparison Backtest")
    print("=" * 62)
    print()
    print("  Tests four stop distances to find the optimal level.")
    print("  Each multiplier is run with daily close AND intraday data.")
    print()
    print("  Stop multipliers: 1.0x / 1.5x / 2.0x / 2.5x ATR")
    print()
    print("  Daily close:  52 weeks  (~260 picks per multiplier)")
    print("  Intraday:     last 8 weeks  (~40 picks per multiplier)")
    print()
    print("  Key metric: how many extra intraday stop hits remain")
    print("  as the stop widens?")
    print()
    print("  Estimated time: 25-35 minutes.")
    print()

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        print("  Cancelled.")
        return

    tickers = get_tickers()
    print(f"\n  Ticker universe: {len(tickers)} stocks")

    # Anchor to Monday
    now = datetime.now()
    now = now - timedelta(days=now.weekday())

    # Download daily data for scoring + 52-week simulation
    print("  Downloading daily price data (52 weeks)...")
    daily_data = _download_all_prices(tickers)
    print(f"  Daily data downloaded for {len(daily_data)} tickers.")

    # Download intraday data for 8-week simulation
    print("  Downloading hourly intraday data (last ~60 days)...")
    intraday_data = _download_intraday(tickers)
    print(f"  Intraday data downloaded for {len(intraday_data)} tickers.\n")

    # Pre-score all 52 weeks once -- shared across all multipliers
    # Scoring uses ATR_MULTIPLIER from config for the target price, but
    # we override the stop price per multiplier during simulation.
    print("  Pre-scoring 52 weeks (shared across all multipliers)...")
    weekly_picks_52 = _prescore_weeks(tickers, daily_data, now,
                                      BACKTEST_WEEKS_TECHNICAL)
    total_52 = sum(len(v) for v in weekly_picks_52.values())
    print(f"  {total_52} picks across {len(weekly_picks_52)} weeks.")

    print(f"  Pre-scoring last {INTRADAY_WEEKS} weeks for intraday test...")
    weekly_picks_intra = _prescore_weeks(tickers, daily_data, now,
                                         INTRADAY_WEEKS)
    total_intra = sum(len(v) for v in weekly_picks_intra.values())
    print(f"  {total_intra} picks across {len(weekly_picks_intra)} weeks.\n")

    for mult in STOP_MULTIPLIERS:
        mult_label = f"{mult:.1f}x"
        print(f"  ══ Stop multiplier: {mult_label} ATR ══")

        # Daily close simulation
        print(f"    Daily close simulation...")
        daily_results = []
        for sim_date_str, picks in weekly_picks_52.items():
            sim_date_pd = pd.Timestamp(sim_date_str)
            for pick in picks:
                r = _simulate_daily(pick, daily_data, sim_date_pd, mult)
                if r:
                    daily_results.append(r)
        _apply_kelly(daily_results)
        daily_file = f"lse_sw_{mult_label.replace('.','p')}_daily.csv"
        _save(daily_results, daily_file, DAILY_HEADERS)
        _print_summary(daily_results, "daily", stop_reason="stop_hit")

        # Intraday simulation
        print(f"    Intraday simulation...")
        intra_results = []
        for sim_date_str, picks in weekly_picks_intra.items():
            sim_date_pd = pd.Timestamp(sim_date_str)
            for pick in picks:
                r = _simulate_intraday(pick, intraday_data, sim_date_pd, mult)
                if r:
                    intra_results.append(r)
        if intra_results:
            _apply_kelly(intra_results)
            intra_file = f"lse_sw_{mult_label.replace('.','p')}_intra.csv"
            _save(intra_results, intra_file, INTRA_HEADERS)
            _print_summary(intra_results, "intraday", stop_reason="stop_hit_intraday")
        else:
            print("    No intraday results for this multiplier.")
        print()

    print("  All multipliers complete.")
    print("  Run stop_width_diagnostic.py to compare results.\n")


# ---------------------------------------------------------------------------
# Data downloads
# ---------------------------------------------------------------------------

def _download_intraday(tickers):
    intraday = {}
    ticker_list = list(tickers.keys())
    batch_size  = 50

    for i in range(0, len(ticker_list), batch_size):
        batch = ticker_list[i:i+batch_size]
        print(f"    Intraday batch {i//batch_size + 1} / "
              f"{-(-len(ticker_list)//batch_size)}...", end="\r", flush=True)
        try:
            raw = yf.download(
                batch, period="60d", interval=INTERVAL,
                progress=False, auto_adjust=True, group_by="ticker",
            )
            for ticker in batch:
                try:
                    df = raw.copy() if len(batch) == 1 else (
                        raw[ticker].copy()
                        if ticker in raw.columns.get_level_values(0) else None
                    )
                    if df is not None and len(df) >= 10:
                        df.columns = [
                            c[0].lower() if isinstance(c, tuple) else c.lower()
                            for c in df.columns
                        ]
                        df.dropna(inplace=True)
                        if df.index.tz is not None:
                            df.index = df.index.tz_convert("UTC").tz_localize(None)
                        intraday[ticker] = df
                except Exception:
                    continue
        except Exception:
            continue

    print()
    return intraday


# ---------------------------------------------------------------------------
# Pre-scoring
# ---------------------------------------------------------------------------

def _prescore_weeks(tickers, daily_data, now, n_weeks):
    weekly_picks = {}
    for week_offset in range(n_weeks, 0, -1):
        sim_date     = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=7)
        if outcome_date > now:
            continue

        sim_date_pd = pd.Timestamp(sim_date)
        print(f"    Scoring week of {sim_date.strftime('%Y-%m-%d')}...",
              end="\r", flush=True)

        candidates = []
        for ticker, sector in tickers.items():
            df = daily_data.get(ticker)
            if df is None or len(df) < 30:
                continue
            try:
                hist = df[df.index <= sim_date_pd].copy()
                if len(hist) < 30:
                    continue
                # Use default STOP_MULTIPLIER=1.0 for scoring -- we override
                # the stop price per-multiplier during simulation
                r = _score_historical(
                    ticker, sector, hist,
                    ATR_MULTIPLIER, 1.0, LIMIT_BUFFER
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
# Daily close simulation
# ---------------------------------------------------------------------------

def _simulate_daily(pick, daily_data, sim_date_pd, stop_mult):
    """
    Fixed stop simulation using daily closing prices.
    Stop price is recalculated using the given multiplier.
    """
    ticker      = pick["ticker"]
    entry_price = pick["price"]
    atr         = pick.get("atr", 0)
    if atr <= 0:
        return None

    stop_price = round(entry_price - stop_mult * atr, 2)

    # _score_historical strips .L from ticker -- try both forms
    df = daily_data.get(ticker + ".L")
    if df is None:
        df = daily_data.get(ticker)
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

    for day_idx, (date, bar) in enumerate(future_bars.iterrows(), 1):
        day_close = float(bar["close"])
        if day_close <= stop_price:
            exit_price  = stop_price
            exit_day    = day_idx
            exit_reason = "stop_hit"
            break

    if exit_price is None:
        closest = future_bars[future_bars.index <= outcome_date_pd]
        if closest.empty:
            closest = future_bars
        exit_price  = float(closest["close"].iloc[-1])
        exit_day    = len(closest)
        exit_reason = "friday_close"

    return {
        "run_date":     sim_date_pd.strftime("%Y-%m-%d %H:%M"),
        "ticker":       ticker,
        "sector":       pick["sector"],
        "score":        pick["score"],
        "entry_price":  round(entry_price, 2),
        "stop_price":   round(stop_price, 2),
        "atr":          round(atr, 4),
        "stop_mult":    stop_mult,
        "exit_price":   round(exit_price, 2),
        "exit_day":     exit_day,
        "exit_reason":  exit_reason,
        "return_pct":   round((exit_price - entry_price) / entry_price * 100, 2),
        "prob":         pick["prob"],
        "allocation_pct": 0.0,
    }


# ---------------------------------------------------------------------------
# Intraday simulation
# ---------------------------------------------------------------------------

def _simulate_intraday(pick, intraday_data, sim_date_pd, stop_mult):
    """
    Fixed stop simulation using hourly intraday bars.
    Checks intraday LOW of each bar against the stop price.
    Stop price recalculated using the given multiplier.
    """
    ticker      = pick["ticker"]
    entry_price = pick["price"]
    atr         = pick.get("atr", 0)
    if atr <= 0:
        return None

    stop_price = round(entry_price - stop_mult * atr, 2)

    # _score_historical strips .L from ticker -- try both forms
    df = intraday_data.get(ticker + ".L")
    if df is None:
        df = intraday_data.get(ticker)
    if df is None:
        return None

    week_end_pd = sim_date_pd + pd.Timedelta(days=7)
    try:
        week_bars = df[
            (df.index > sim_date_pd) &
            (df.index <= week_end_pd)
        ].copy()
        if week_bars.empty:
            return None
    except Exception:
        return None

    # Filter to LSE market hours (8:00-17:00 UTC)
    week_bars = week_bars[
        (week_bars.index.hour >= 8) &
        (week_bars.index.hour < 17)
    ]
    if week_bars.empty:
        return None

    week_bars["_date"] = week_bars.index.date
    trading_days = sorted(week_bars["_date"].unique())
    day_map      = {d: i+1 for i, d in enumerate(trading_days)}

    exit_price  = None
    exit_day    = None
    exit_reason = None

    for _, bar in week_bars.iterrows():
        bar_low  = float(bar["low"])  if "low"  in bar.index else float(bar["close"])
        bar_date = bar["_date"]
        day_num  = day_map.get(bar_date, len(trading_days))

        if bar_low <= stop_price:
            exit_price  = stop_price
            exit_day    = day_num
            exit_reason = "stop_hit_intraday"
            break

    if exit_price is None:
        try:
            exit_price  = float(week_bars["close"].iloc[-1])
            exit_day    = len(trading_days)
            exit_reason = "friday_close"
        except Exception:
            return None

    return {
        "run_date":     sim_date_pd.strftime("%Y-%m-%d %H:%M"),
        "ticker":       ticker,
        "sector":       pick["sector"],
        "score":        pick["score"],
        "entry_price":  round(entry_price, 2),
        "stop_price":   round(stop_price, 2),
        "atr":          round(atr, 4),
        "stop_mult":    stop_mult,
        "exit_price":   round(exit_price, 2),
        "exit_day":     exit_day,
        "exit_reason":  exit_reason,
        "return_pct":   round((exit_price - entry_price) / entry_price * 100, 2),
        "prob":         pick["prob"],
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


def _save(results, filepath, headers):
    if not results:
        print(f"    No results to save to {filepath}")
        return
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"    Saved {len(results)} picks to {filepath}")


def _print_summary(results, mode, stop_reason):
    if not results:
        return
    returns = [r["return_pct"] for r in results]
    n       = len(returns)
    avg     = sum(returns) / n
    pos     = sum(1 for r in returns if r > 0)
    stops   = sum(1 for r in results if r["exit_reason"] == stop_reason)
    fridays = sum(1 for r in results if r["exit_reason"] == "friday_close")
    sign    = "+" if avg >= 0 else ""
    print(
        f"    [{mode:>8}]  {sign}{avg:.2f}% avg  |  "
        f"{pos/n*100:.1f}% positive  |  "
        f"{stops} stop hits ({stops/n*100:.1f}%)  |  "
        f"{fridays} friday closes"
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
