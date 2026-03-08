#!/usr/bin/env python3
"""
backtest_intraday_validation.py
--------------------------------
Validates the trailing stop strategy using intraday (hourly) price data
for the last ~60 days. This tests whether the daily-close simulation in
backtest_dynamic_stop.py is optimistic by checking true intraday lows
against the trailing stop level.

yfinance provides hourly bars for approximately the last 60 days, giving
us the high and low of each hourly bar. The intraday low of any bar is
used to check whether the stop was crossed during the session -- even if
the daily close was above it.

Four variants are tested (identical logic to backtest_dynamic_stop.py):

  C-base        -- Fixed stop only, hold to Friday close
  C-trail-cons  -- Trailing stop, trigger at 1.5x ATR above entry
  C-trail-mod   -- Trailing stop, trigger at 1.0x ATR above entry
  C-trail-agg   -- Trailing stop, trigger at 0.5x ATR above entry

Trailing stop mechanic (same as daily version):
  - Trigger: hourly close >= entry + N * ATR
  - On trigger: stop moves to breakeven (entry price)
  - Thereafter: stop = entry + 0.5 * (peak_close - entry)
  - Stop checked against intraday low of each hourly bar
  - If not stopped: exit at Friday close (last bar of the week)

Because the intraday dataset only covers ~60 days (~8-9 weeks), the
results will have higher variance than the 52-week daily test. The key
comparison is:

  Daily close version  vs  Intraday version
  -- How many more stop hits appear in the intraday version?
  -- Does the avg return hold up or deteriorate significantly?

Results saved to lse_iv_<variant>.csv.
Run intraday_validation_diagnostic.py to compare results.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_intraday_validation.py
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
        ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER,
        BACKTEST_CAPITAL,
    )
    from lse_analyser.screener import diversify
    from lse_analyser.sizing import calculate_allocations
    from lse_analyser.tickers import get_tickers
except Exception as e:
    print(f"Import error: {e}")
    import traceback
    traceback.print_exc()
    raise

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRADING_DAYS   = 5
INTRADAY_WEEKS = 8    # how many weeks to backtest (limited by yfinance ~60d)
INTERVAL       = "1h" # hourly bars -- best balance of granularity vs availability

VARIANTS = [
    {
        "label":         "C-base",
        "display":       "C-base        -- Fixed stop, hold to Friday close",
        "trail_trigger": None,
        "trail_pct":     0.5,
        "output_file":   "lse_iv_base.csv",
    },
    {
        "label":         "C-trail-cons",
        "display":       "C-trail-cons  -- Trailing stop, trigger at 1.5x ATR",
        "trail_trigger": 1.5,
        "trail_pct":     0.5,
        "output_file":   "lse_iv_cons.csv",
    },
    {
        "label":         "C-trail-mod",
        "display":       "C-trail-mod   -- Trailing stop, trigger at 1.0x ATR",
        "trail_trigger": 1.0,
        "trail_pct":     0.5,
        "output_file":   "lse_iv_mod.csv",
    },
    {
        "label":         "C-trail-agg",
        "display":       "C-trail-agg   -- Trailing stop, trigger at 0.5x ATR",
        "trail_trigger": 0.5,
        "trail_pct":     0.5,
        "output_file":   "lse_iv_agg.csv",
    },
]

OUTPUT_HEADERS = [
    "run_date", "ticker", "sector", "score",
    "entry_price", "original_stop", "atr",
    "exit_price", "exit_day", "exit_reason",
    "peak_price", "final_stop", "stop_moved",
    "return_pct", "prob", "allocation_pct",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    print("=" * 62)
    print("  Intraday Trailing Stop Validation")
    print("=" * 62)
    print()
    print("  Validates trailing stop using hourly intraday bars.")
    print("  Checks intraday LOWS against stop level each hour.")
    print("  Covers approximately the last 8 weeks (~60 trading days).")
    print()
    print("  Key question: how many extra stop hits appear vs daily closes?")
    print("  If many more stops are hit, the daily backtest was too optimistic.")
    print()
    print("  Variants:")
    for v in VARIANTS:
        print(f"    {v['display']}")
    print()

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        print("  Cancelled.")
        return

    tickers = get_tickers()
    print(f"\n  Ticker universe: {len(tickers)} stocks")

    # Anchor to Monday of current week for reproducibility
    now = datetime.now()
    now = now - timedelta(days=now.weekday())

    # Download daily data for scoring (needs ~6 months of history)
    print("  Downloading daily price data for scoring...")
    daily_data = _download_all_prices(tickers)
    print(f"  Daily data downloaded for {len(daily_data)} tickers.")

    # Download hourly intraday data for exit simulation
    print("  Downloading hourly intraday data (last ~60 days)...")
    intraday_data = _download_intraday(tickers)
    print(f"  Intraday data downloaded for {len(intraday_data)} tickers.\n")

    # Score the last INTRADAY_WEEKS weeks using daily data
    print(f"  Scoring last {INTRADAY_WEEKS} weeks...")
    weekly_picks = _prescore_weeks(tickers, daily_data, now, INTRADAY_WEEKS)
    total = sum(len(v) for v in weekly_picks.values())
    print(f"  {total} picks across {len(weekly_picks)} weeks.\n")

    if total == 0:
        print("  No picks found. The intraday window may be too recent.")
        print("  Try reducing INTRADAY_WEEKS or check ticker data availability.")
        return

    for variant in VARIANTS:
        print(f"  --- {variant['display']} ---")
        all_results = []

        for sim_date_str, picks in weekly_picks.items():
            sim_date_pd = pd.Timestamp(sim_date_str)
            for pick in picks:
                result = _simulate_intraday(
                    pick, intraday_data, sim_date_pd, variant
                )
                if result:
                    all_results.append(result)

        if not all_results:
            print("  No results -- intraday data may not cover this date range.")
            print()
            continue

        _apply_kelly_to_results(all_results)
        _save_results(all_results, variant["output_file"])
        _print_summary(all_results)
        print()

    print("  All variants complete.")
    print("  Run intraday_validation_diagnostic.py to compare results.\n")


# ---------------------------------------------------------------------------
# Intraday data download
# ---------------------------------------------------------------------------

def _download_intraday(tickers):
    """
    Download hourly bars for the last 60 days for all tickers.
    yfinance limit: interval='1h' available for last ~730 days but
    in practice reliable for ~60 days. We use period='60d'.
    """
    intraday = {}
    ticker_list = list(tickers.keys())
    batch_size  = 50

    for i in range(0, len(ticker_list), batch_size):
        batch = ticker_list[i:i+batch_size]
        print(f"    Intraday batch {i//batch_size + 1} / {len(ticker_list)//batch_size + 1}...",
              end="\r", flush=True)
        try:
            raw = yf.download(
                batch,
                period="60d",
                interval=INTERVAL,
                progress=False,
                auto_adjust=True,
                group_by="ticker",
            )
            for ticker in batch:
                try:
                    if len(batch) == 1:
                        df = raw.copy()
                    else:
                        df = raw[ticker].copy() if ticker in raw.columns.get_level_values(0) else None
                    if df is not None and len(df) >= 10:
                        df.columns = [
                            c[0].lower() if isinstance(c, tuple) else c.lower()
                            for c in df.columns
                        ]
                        df.dropna(inplace=True)
                        # Localise timezone to UTC then strip for consistent comparison
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
# Pre-scoring (uses daily data, same as main backtest)
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
# Core intraday simulation
# ---------------------------------------------------------------------------

def _simulate_intraday(pick, intraday_data, sim_date_pd, variant):
    """
    Simulate a single pick using hourly intraday bars.

    For each hourly bar in the week:
      - Check the bar's LOW against the current stop (intraday stop trigger)
      - Check the bar's CLOSE for trailing stop activation and peak tracking
      - Trailing stop activates when hourly CLOSE >= trigger price
      - Stop is checked against bar LOW (catches intraday dips below stop)

    Exit at Friday close (last bar of the week) if stop not triggered.

    Note: LSE hours are approximately 08:00-16:30 UTC.
    We filter to market hours to avoid pre/post-market noise.
    """
    ticker        = pick["ticker"]
    entry_price   = pick["price"]
    original_stop = pick["stop"]
    atr           = pick.get("atr", abs(entry_price - original_stop))

    trail_trigger = variant["trail_trigger"]
    trail_pct     = variant["trail_pct"]

    # Try .L suffix first, then raw ticker
    df = intraday_data.get(ticker)
    if df is None:
        df = intraday_data.get(ticker.replace(".L", "") if ticker.endswith(".L") else ticker + ".L")
    if df is None:
        return None

    # Get all hourly bars for the week after sim_date
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

    # Filter to approximate LSE market hours (8:00-17:00 UTC)
    week_bars = week_bars[
        (week_bars.index.hour >= 8) &
        (week_bars.index.hour < 17)
    ]
    if week_bars.empty:
        return None

    # Group bars by trading day for exit day tracking
    week_bars["_date"] = week_bars.index.date
    trading_days = sorted(week_bars["_date"].unique())
    day_map      = {d: i+1 for i, d in enumerate(trading_days)}

    current_stop = original_stop
    peak_price   = entry_price
    trailing     = False
    stop_moved   = False

    exit_price  = None
    exit_day    = None
    exit_reason = None

    for _, bar in week_bars.iterrows():
        bar_low   = float(bar["low"])   if "low"   in bar.index else float(bar["close"])
        bar_close = float(bar["close"])
        bar_date  = bar["_date"]
        day_num   = day_map.get(bar_date, len(trading_days))

        # Update peak using bar close
        if bar_close > peak_price:
            peak_price = bar_close

        # Check trailing stop activation (based on close, not low)
        if trail_trigger is not None and not trailing:
            trigger_price = entry_price + trail_trigger * atr
            if bar_close >= trigger_price:
                trailing   = True
                stop_moved = True

        # Update trailing stop level
        if trailing:
            trail_stop = entry_price + trail_pct * (peak_price - entry_price)
            if trail_stop > current_stop:
                current_stop = trail_stop

        # Check stop hit using intraday LOW -- this is the key difference
        # vs the daily close simulation. A bar whose low dips below the
        # stop price triggers the stop even if the close is above it.
        if bar_low <= current_stop:
            exit_price  = current_stop
            exit_day    = day_num
            exit_reason = "stop_hit_intraday"
            break

    # No stop triggered -- exit at last bar's close (Friday close)
    if exit_price is None:
        try:
            exit_price  = float(week_bars["close"].iloc[-1])
            exit_day    = len(trading_days)
            exit_reason = "friday_close"
        except Exception:
            return None

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
        "peak_price":    round(peak_price, 2),
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
            atr      = float(r.get("atr", 0)) or abs(
                float(r["entry_price"]) - float(r["original_stop"])
            )
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
    stops    = sum(1 for r in results if r["exit_reason"] == "stop_hit_intraday")
    fridays  = sum(1 for r in results if r["exit_reason"] == "friday_close")
    moved    = sum(1 for r in results if r["stop_moved"])
    sign     = "+" if avg >= 0 else ""
    print(
        f"  {sign}{avg:.2f}% avg  |  {pos/n*100:.1f}% positive  |  "
        f"{stops} intraday stop hits  |  {fridays} friday closes  |  "
        f"{moved} stops trailed ({moved/n*100:.1f}%)"
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
