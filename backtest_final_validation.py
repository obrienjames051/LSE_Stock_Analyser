#!/usr/bin/env python3
"""
backtest_final_validation.py
------------------------------
Final validation backtest confirming the settled strategy:
  - Stop at 1x ATR, no upper limits
  - Entry at Monday open (first bar after sim_date)
  - Stop checked against daily close only (mental stop)
  - Exit at the closing price that breaches the stop
  - Hold to Friday close if stop not triggered

Three tests are run:

  Test 1 -- Full year, daily close  (52 weeks)
    The definitive annual backtest using the correct entry price
    (Monday open) and daily close stop checking.
    Target: reproduce and confirm the +0.854% baseline.

  Test 2 -- Recent weeks, daily close  (last N weeks, excl. last week)
    Same logic as Test 1 but limited to weeks with hourly data available.
    The most recent week is excluded due to an anomalous market-wide
    sell-off (tariff war) that does not reflect normal conditions.
    Provides a recent-period baseline for comparison with Test 3.

  Test 3 -- Recent weeks, hard stop intraday  (same weeks as Test 2)
    Same picks and stop prices as Test 2, but stop is checked against
    the intraday LOW of each hourly bar rather than the daily close.
    Simulates placing a hard stop order with the broker.
    Expected to show worse returns due to intraday noise triggering
    stops prematurely.

Key correction vs previous backtests:
  All previous backtests used Friday's close as the entry price
  (the last close before sim_date). This script uses Monday's open
  as the entry price, which is what you actually pay in live trading.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_final_validation.py
"""

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
        BACKTEST_WEEKS_TECHNICAL, BACKTEST_CAPITAL,
        BACKTEST_END_DATE,
    )
    from lse_analyser.screener import diversify
    from lse_analyser.sizing import calculate_allocations
    from lse_analyser.tickers import get_tickers
except Exception as e:
    print(f"Import error: {e}")
    import traceback; traceback.print_exc(); raise

TRADING_DAYS   = 5
INTRADAY_WEEKS = 8   # max weeks yfinance hourly data covers
EXCLUDE_WEEKS  = 1   # exclude most recent N weeks (anomalous market)
INTERVAL       = "1h"

# ---------------------------------------------------------------------------
# Entry price mode
# ---------------------------------------------------------------------------
# MONDAY_OPEN  -- uses the open of the first trading bar after sim_date
#                 (what you actually pay in live trading)
# FRIDAY_CLOSE -- uses the last close before sim_date
#                 (what previous backtests used -- kept for reference)
ENTRY_MODE = "MONDAY_OPEN"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    _header("Final Validation Backtest")
    print("  Three tests to confirm the settled strategy:")
    print()
    print("  Test 1  Full year      Daily close stops    52 weeks")
    print("  Test 2  Recent weeks   Daily close stops    ~8 weeks excl. last")
    print("  Test 3  Recent weeks   Hard stop intraday   same weeks as Test 2")
    print()
    print("  Entry price: Monday open (first bar after sim_date)")
    print(f"  Stop:        1x ATR below entry, no upper limit")
    print(f"  Excludes:    most recent {EXCLUDE_WEEKS} week(s) from recent tests")
    print()
    print("  Estimated time: 20-25 minutes.")
    print()

    confirm = input("  Press Enter to start, or 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        print("  Cancelled.")
        return

    tickers = get_tickers()
    print(f"\n  Universe: {len(tickers)} tickers")

    print("  Downloading daily price data...")
    daily_data = _download_all_prices(tickers)
    print(f"  Daily data: {len(daily_data)} tickers")

    print("  Downloading hourly intraday data...")
    intraday_data = _download_intraday(tickers)
    print(f"  Intraday data: {len(intraday_data)} tickers\n")

    now = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")

    # ------------------------------------------------------------------
    # Determine the recent window (Tests 2 & 3)
    # ------------------------------------------------------------------
    # Go back INTRADAY_WEEKS from now, then add EXCLUDE_WEEKS buffer
    recent_start = now - timedelta(weeks=INTRADAY_WEEKS)
    recent_end   = now - timedelta(weeks=EXCLUDE_WEEKS)
    print(f"  Recent window: {recent_start.strftime('%Y-%m-%d')} "
          f"to {recent_end.strftime('%Y-%m-%d')} "
          f"(excl. last {EXCLUDE_WEEKS} week)")
    print()

    # ------------------------------------------------------------------
    # Pre-score all 52 weeks
    # ------------------------------------------------------------------
    print("  Pre-scoring 52 weeks...")
    all_weeks = _prescore_weeks(tickers, daily_data, now, BACKTEST_WEEKS_TECHNICAL)
    recent_weeks = {k: v for k, v in all_weeks.items()
                    if recent_start <= datetime.strptime(k[:10], "%Y-%m-%d") < recent_end}
    print(f"  Full year: {len(all_weeks)} weeks  "
          f"({sum(len(v) for v in all_weeks.values())} picks)")
    print(f"  Recent:    {len(recent_weeks)} weeks  "
          f"({sum(len(v) for v in recent_weeks.values())} picks)\n")

    # ------------------------------------------------------------------
    # Test 1: Full year, daily close
    # ------------------------------------------------------------------
    _header("Test 1 — Full year, daily close stops (52 weeks)")
    t1_results = []
    for sim_date_str, picks in all_weeks.items():
        sim_date_pd = pd.Timestamp(sim_date_str)
        for pick in picks:
            r = _sim_daily(pick, daily_data, sim_date_pd)
            if r:
                t1_results.append(r)
    _apply_kelly(t1_results)
    _print_results(t1_results, "Test 1")
    print()

    # ------------------------------------------------------------------
    # Test 2: Recent weeks, daily close
    # ------------------------------------------------------------------
    _header(f"Test 2 — Recent weeks, daily close stops ({len(recent_weeks)} weeks)")
    t2_results = []
    for sim_date_str, picks in recent_weeks.items():
        sim_date_pd = pd.Timestamp(sim_date_str)
        for pick in picks:
            r = _sim_daily(pick, daily_data, sim_date_pd)
            if r:
                t2_results.append(r)
    _apply_kelly(t2_results)
    _print_results(t2_results, "Test 2")
    print()

    # ------------------------------------------------------------------
    # Test 3: Recent weeks, hard stop intraday
    # ------------------------------------------------------------------
    _header(f"Test 3 — Recent weeks, hard stop intraday ({len(recent_weeks)} weeks)")
    t3_results = []
    for sim_date_str, picks in recent_weeks.items():
        sim_date_pd = pd.Timestamp(sim_date_str)
        for pick in picks:
            r = _sim_intraday(pick, intraday_data, daily_data, sim_date_pd)
            if r:
                t3_results.append(r)
    _apply_kelly(t3_results)
    _print_results(t3_results, "Test 3")
    print()

    # ------------------------------------------------------------------
    # Summary comparison
    # ------------------------------------------------------------------
    _header("Summary")
    _print_comparison(t1_results, t2_results, t3_results)


# ---------------------------------------------------------------------------
# Pre-scoring
# ---------------------------------------------------------------------------

def _prescore_weeks(tickers, daily_data, now, n_weeks):
    weekly = {}
    for week_offset in range(n_weeks, 0, -1):
        sim_date     = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=7)
        if outcome_date > now:
            continue
        sim_date_pd = pd.Timestamp(sim_date)
        print(f"    Scoring {sim_date.strftime('%Y-%m-%d')}...", end="\r", flush=True)

        candidates = []
        for ticker, sector in tickers.items():
            df = daily_data.get(ticker)
            if df is None or len(df) < 30:
                continue
            try:
                hist = df[df.index <= sim_date_pd].copy()
                if len(hist) < 30:
                    continue
                r = _score_historical(ticker, sector, hist,
                                      ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER)
                if r:
                    candidates.append(r)
            except Exception:
                continue

        if not candidates:
            continue
        candidates.sort(key=lambda x: x["score"], reverse=True)
        weekly[sim_date.strftime("%Y-%m-%d %H:%M")] = diversify(candidates, 5)

    print()
    return weekly


# ---------------------------------------------------------------------------
# Entry price helper
# ---------------------------------------------------------------------------

def _get_entry_price(pick, df, sim_date_pd):
    """
    Returns the entry price based on ENTRY_MODE.

    MONDAY_OPEN:  open of the first daily bar after sim_date_pd
    FRIDAY_CLOSE: close of the last bar on or before sim_date_pd
                  (i.e. pick["price"] -- kept for reference only)
    """
    if ENTRY_MODE == "FRIDAY_CLOSE":
        return pick["price"]

    # MONDAY_OPEN: first bar strictly after sim_date
    try:
        future = df[df.index > sim_date_pd]
        if future.empty:
            return None
        monday_open = float(future["open"].iloc[0])
        if monday_open <= 0:
            return None
        return monday_open
    except Exception:
        return None


def _adjust_stop(pick, entry_price):
    """
    Recalculate stop relative to actual entry price using ATR distance.
    The pick's stop was set relative to Friday close; we shift it by the
    same ATR distance from the actual Monday open entry price.
    """
    atr = pick.get("atr", 0)
    if atr <= 0:
        # Fall back to the percentage distance from original stop
        orig_dist = pick["price"] - pick["stop"]
        return round(entry_price - orig_dist, 2)
    return round(entry_price - STOP_MULTIPLIER * atr, 2)


# ---------------------------------------------------------------------------
# Test 1 & 2: Daily close simulation
# ---------------------------------------------------------------------------

def _sim_daily(pick, daily_data, sim_date_pd):
    ticker = pick["ticker"]
    df = daily_data.get(ticker + ".L"); df = df if df is not None else daily_data.get(ticker)
    if df is None:
        return None

    entry_price = _get_entry_price(pick, df, sim_date_pd)
    if not entry_price:
        return None

    stop_price = _adjust_stop(pick, entry_price)
    atr        = pick.get("atr", abs(entry_price - stop_price))

    try:
        future_bars = df[df.index > sim_date_pd].head(TRADING_DAYS)
        if future_bars.empty:
            return None
    except Exception:
        return None

    exit_price  = None
    exit_day    = None
    exit_reason = None

    for day_idx, (_, bar) in enumerate(future_bars.iterrows(), 1):
        day_close = float(bar["close"])
        if day_close <= stop_price:
            exit_price  = day_close
            exit_day    = day_idx
            exit_reason = "stop_close"
            break

    if exit_price is None:
        week_bars   = future_bars.head(TRADING_DAYS)
        exit_price  = float(week_bars["close"].iloc[-1])
        exit_day    = len(week_bars)
        exit_reason = "friday_close"

    return _make_result(pick, sim_date_pd, entry_price, stop_price,
                        atr, exit_price, exit_day, exit_reason)


# ---------------------------------------------------------------------------
# Test 3: Intraday hard stop simulation
# ---------------------------------------------------------------------------

def _sim_intraday(pick, intraday_data, daily_data, sim_date_pd):
    ticker = pick["ticker"]

    # Entry price from daily data (Monday open)
    df_daily = daily_data.get(ticker + ".L"); df_daily = df_daily if df_daily is not None else daily_data.get(ticker)
    if df_daily is None:
        return None

    entry_price = _get_entry_price(pick, df_daily, sim_date_pd)
    if not entry_price:
        return None

    stop_price = _adjust_stop(pick, entry_price)
    atr        = pick.get("atr", abs(entry_price - stop_price))

    # Intraday data for stop checking
    df_intra = intraday_data.get(ticker + ".L"); df_intra = df_intra if df_intra is not None else intraday_data.get(ticker)
    if df_intra is None:
        return None

    week_end_pd = sim_date_pd + pd.Timedelta(days=7)
    try:
        week_bars = df_intra[
            (df_intra.index > sim_date_pd) &
            (df_intra.index <= week_end_pd)
        ].copy()
        if week_bars.empty:
            return None
    except Exception:
        return None

    # LSE market hours only
    week_bars = week_bars[
        (week_bars.index.hour >= 8) &
        (week_bars.index.hour < 17)
    ]
    if week_bars.empty:
        return None

    week_bars["_date"] = week_bars.index.date
    trading_days = sorted(week_bars["_date"].unique())
    day_map      = {d: i + 1 for i, d in enumerate(trading_days)}

    exit_price  = None
    exit_day    = None
    exit_reason = None

    for _, bar in week_bars.iterrows():
        bar_low  = float(bar["low"]) if "low" in bar.index else float(bar["close"])
        bar_date = bar["_date"]
        day_num  = day_map.get(bar_date, len(trading_days))

        if bar_low <= stop_price:
            exit_price  = stop_price  # hard stop fills at stop price
            exit_day    = day_num
            exit_reason = "stop_intraday"
            break

    if exit_price is None:
        # No stop hit -- use Friday close from daily data
        try:
            friday_bars = df_daily[df_daily.index > sim_date_pd].head(TRADING_DAYS)
            exit_price  = float(friday_bars["close"].iloc[-1])
            exit_day    = len(friday_bars)
            exit_reason = "friday_close"
        except Exception:
            return None

    return _make_result(pick, sim_date_pd, entry_price, stop_price,
                        atr, exit_price, exit_day, exit_reason)


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------

def _make_result(pick, sim_date_pd, entry_price, stop_price,
                 atr, exit_price, exit_day, exit_reason):
    return_pct = (exit_price - entry_price) / entry_price * 100
    return {
        "run_date":      sim_date_pd.strftime("%Y-%m-%d %H:%M"),
        "ticker":        pick["ticker"],
        "sector":        pick["sector"],
        "score":         pick["score"],
        "entry_price":   round(entry_price, 4),
        "stop_price":    round(stop_price, 4),
        "atr":           round(atr, 4),
        "exit_price":    round(exit_price, 4),
        "exit_day":      exit_day,
        "exit_reason":   exit_reason,
        "return_pct":    round(return_pct, 4),
        "prob":          pick["prob"],
        "allocation_pct": 0.0,
        "went_up":       1 if exit_price > entry_price else 0,
    }


# ---------------------------------------------------------------------------
# Kelly sizing
# ---------------------------------------------------------------------------

def _apply_kelly(results):
    by_week = defaultdict(list)
    for r in results:
        by_week[r["run_date"]].append(r)
    for week_picks in by_week.values():
        mock = []
        for r in week_picks:
            atr      = float(r["atr"]) or abs(float(r["entry_price"]) - float(r["stop_price"]))
            target   = float(r["entry_price"]) + atr
            upside   = (target - float(r["entry_price"])) / float(r["entry_price"]) * 100
            downside = (float(r["entry_price"]) - float(r["stop_price"])) / float(r["entry_price"]) * 100
            mock.append({
                "prob":        float(r["prob"]) / 100,
                "reward_risk": round(upside / downside, 2) if downside > 0 else 1.5,
                "price":       r["entry_price"],
            })
        calculate_allocations(mock, BACKTEST_CAPITAL)
        for i, r in enumerate(week_picks):
            r["allocation_pct"] = mock[i].get("allocation_pct", 0.0)


# ---------------------------------------------------------------------------
# Intraday download
# ---------------------------------------------------------------------------

def _download_intraday(tickers):
    intraday = {}
    ticker_list = list(tickers.keys())
    batch_size  = 50
    for i in range(0, len(ticker_list), batch_size):
        batch = ticker_list[i:i + batch_size]
        print(f"    Intraday batch {i // batch_size + 1}/"
              f"{-(-len(ticker_list) // batch_size)}...", end="\r", flush=True)
        try:
            raw = yf.download(batch, period="60d", interval=INTERVAL,
                              progress=False, auto_adjust=True, group_by="ticker")
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
# Display helpers
# ---------------------------------------------------------------------------

BUCKETS = [
    ("< -5%",      lambda r: r < -5),
    ("-5% to -3%", lambda r: -5 <= r < -3),
    ("-3% to -1%", lambda r: -3 <= r < -1),
    ("-1% to  0%", lambda r: -1 <= r < 0),
    ("  0% to +1%", lambda r: 0  <= r < 1),
    ("+1% to +3%", lambda r: 1  <= r < 3),
    ("+3% to +5%", lambda r: 3  <= r < 5),
    (">  +5%",     lambda r: r >= 5),
]

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"


def _header(title):
    print(f"\n{'='*62}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{'='*62}")


def _print_results(results, label):
    if not results:
        print("  No results.")
        return

    returns = [float(r["return_pct"]) for r in results]
    n       = len(returns)
    pos     = [r for r in returns if r > 0]
    neg     = [r for r in returns if r <= 0]
    stops   = sum(1 for r in results if "stop" in r.get("exit_reason", ""))
    fridays = sum(1 for r in results if r.get("exit_reason") == "friday_close")

    allocs      = [(float(r["return_pct"]), float(r.get("allocation_pct") or 0))
                   for r in results]
    total_alloc = sum(a for _, a in allocs)
    kelly       = (sum(ret * a for ret, a in allocs) / total_alloc
                   if total_alloc > 0 else sum(returns) / n)

    by_week   = defaultdict(list)
    for r in results:
        by_week[r["run_date"][:10]].append(float(r["return_pct"]))
    week_avgs = {w: sum(v) / len(v) for w, v in by_week.items()}
    neg_weeks = sum(1 for v in week_avgs.values() if v < 0)

    avg_entry = sum(float(r["entry_price"]) for r in results) / n
    dir_acc   = sum(r["went_up"] for r in results) / n * 100
    avg_prob  = sum(float(r["prob"]) for r in results) / n

    print(f"\n  Picks:    {n}  across {len(week_avgs)} weeks  "
          f"({n/len(week_avgs):.1f} per week)")
    print(f"  Entry:    Monday open  (avg {avg_entry:.1f}p)")
    print()
    print(f"  Avg return (simple):     {_c(sum(returns)/n)}")
    print(f"  Avg return (Kelly-wtd):  {_c(kelly)}")
    print(f"  Directional accuracy:    {dir_acc:>8.1f}%  "
          f"(model avg {avg_prob:.1f}%)")
    print(f"  Avg winner:              {_c(sum(pos)/len(pos) if pos else 0)}")
    print(f"  Avg loser:               {_c(sum(neg)/len(neg) if neg else 0)}")
    print(f"  Best week:               {_c(max(week_avgs.values()))}")
    print(f"  Worst week:              {_c(min(week_avgs.values()))}")
    print(f"  Negative weeks:          {neg_weeks:>3} / {len(week_avgs)}  "
          f"({neg_weeks/len(week_avgs)*100:.1f}%)")
    print()
    print(f"  Stop exits:   {stops:>3}  ({stops/n*100:.1f}%)")
    print(f"  Friday exits: {fridays:>3}  ({fridays/n*100:.1f}%)")
    print()
    print(f"  Return distribution:")
    for blabel, fn in BUCKETS:
        count = sum(1 for r in returns if fn(r))
        bar   = "█" * int(count / n * 40)
        print(f"    {blabel:>14}  {count:>4}  {bar}")


def _print_comparison(t1, t2, t3):
    def stats(results):
        if not results:
            return {}
        returns = [float(r["return_pct"]) for r in results]
        n       = len(returns)
        by_week = defaultdict(list)
        for r in results:
            by_week[r["run_date"][:10]].append(float(r["return_pct"]))
        week_avgs = {w: sum(v)/len(v) for w, v in by_week.items()}
        allocs      = [(float(r["return_pct"]), float(r.get("allocation_pct") or 0))
                       for r in results]
        total_alloc = sum(a for _, a in allocs)
        kelly       = (sum(ret*a for ret, a in allocs) / total_alloc
                       if total_alloc > 0 else sum(returns)/n)
        stops   = sum(1 for r in results if "stop" in r.get("exit_reason", ""))
        return {
            "n":          n,
            "weeks":      len(week_avgs),
            "avg":        sum(returns) / n,
            "kelly":      kelly,
            "dir_acc":    sum(r["went_up"] for r in results) / n * 100,
            "worst":      min(week_avgs.values()),
            "neg_weeks":  sum(1 for v in week_avgs.values() if v < 0),
            "stop_pct":   stops / n * 100,
        }

    s1, s2, s3 = stats(t1), stats(t2), stats(t3)

    def row(label, key, fmt="{:>+8.3f}%", higher_better=True):
        v1, v2, v3 = s1.get(key, 0), s2.get(key, 0), s3.get(key, 0)
        best = max(v1, v2, v3) if higher_better else min(v1, v2, v3)
        def cell(v):
            c = GREEN if abs(v - best) < 0.005 else ""
            return f"  {c}{fmt.format(v)}{RESET if c else ''}"
        print(f"  {label:<28}{cell(v1)}{cell(v2)}{cell(v3)}")

    print(f"\n  {'Metric':<28}  {'Test 1':>10}  {'Test 2':>10}  {'Test 3':>10}")
    print(f"  {'':28}  {'(52wk)':>10}  {'recent':>10}  {'intraday':>10}")
    print(f"  {'-'*28}  {'-'*10}  {'-'*10}  {'-'*10}")
    row("Avg return (simple)",    "avg")
    row("Avg return (Kelly-wtd)", "kelly")
    row("Directional accuracy",   "dir_acc")
    row("Worst week",             "worst",    higher_better=False)
    row("Stop hit rate",          "stop_pct", fmt="{:>8.1f}%", higher_better=False)
    row("Negative weeks",         "neg_weeks",fmt="{:>8.0f}",  higher_better=False)
    print()
    print(f"  Test 2 vs Test 3 gap (daily close vs intraday hard stop):")
    gap = s2.get("avg", 0) - s3.get("avg", 0)
    c   = GREEN if gap > 0 else RED
    print(f"  Avg return difference: {c}{gap:+.3f}pp{RESET}")
    if gap > 0:
        print(f"  {GREEN}Daily close stops outperform hard stop orders.{RESET}")
        print(f"  Mental stop approach is confirmed as the better method.")
    else:
        print(f"  {RED}Hard stop orders match or outperform daily close.{RESET}")
        print(f"  Review intraday data -- unexpected result.")
    print()
    print(f"  Test 1 baseline (full year): {_c(s1['avg'])}  per pick avg")
    print(f"  This is the figure to use for programme calibration.")
    print()


def _c(val):
    c = GREEN if val >= 0 else RED
    return f"{c}{val:+.3f}%{RESET}"


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback; traceback.print_exc()
