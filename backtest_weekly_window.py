#!/usr/bin/env python3
"""
backtest_weekly_window.py
--------------------------
Tests Variant C across six different weekly trading windows to determine
whether the Monday-to-Friday window is optimal or whether a different
start/end day produces better returns.

Hypothesis: if investors systematically sell on Fridays to avoid weekend
gap risk, stocks may drift down late in the week and recover early in the
next week. This would make Monday open -> Friday close the worst window,
with Tuesday or Wednesday starts potentially outperforming.

All six windows use Variant C logic:
  - Entry at the open of the first bar of the window
  - Stop at 1x ATR below entry price
  - Check stop against each day's closing price
  - Exit at the close that breaches the stop, or at window-end close

Windows tested (all 5 trading days):
  Window 1:  Monday open    -> Friday close        (current baseline)
  Window 2:  Tuesday open   -> Monday close
  Window 3:  Wednesday open -> Tuesday close
  Window 4:  Thursday open  -> Wednesday close
  Window 5:  Friday open    -> Thursday close
  Window 6:  Friday close   -> following Friday open  (weekend gap test)

Note on Window 6: Friday close to Friday open spans a weekend.
  Entry:  Friday closing price (last price before weekend)
  Exit:   Following Friday opening price
  This tests whether holding over the weekend systematically helps or hurts.
  Stop is still checked against daily closes Mon-Thu.

All windows use the same scoring date (Monday of each week) so picks
are identical across windows -- only the entry/exit timing differs.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_weekly_window.py
"""

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
    import traceback; traceback.print_exc(); raise

TRADING_DAYS = 5

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"

BUCKETS = [
    ("< -5%",      lambda r: r < -5),
    ("-5% to -3%", lambda r: -5 <= r < -3),
    ("-3% to -1%", lambda r: -3 <= r < -1),
    ("-1% to  0%", lambda r: -1 <= r < 0),
    ("  0% to +1%",lambda r: 0  <= r < 1),
    ("+1% to +3%", lambda r: 1  <= r < 3),
    ("+3% to +5%", lambda r: 3  <= r < 5),
    (">  +5%",     lambda r: r >= 5),
]

WINDOWS = [
    {
        "label":       "Window 1",
        "description": "Monday open -> Friday close  (current baseline)",
        "entry_day":   0,   # days after sim_date (Monday) to find entry bar
        "entry_type":  "open",
        "exit_bars":   5,   # number of trading bars in window
        "exit_type":   "close",
        "window_6":    False,
    },
    {
        "label":       "Window 2",
        "description": "Tuesday open -> Monday close",
        "entry_day":   1,
        "entry_type":  "open",
        "exit_bars":   5,
        "exit_type":   "close",
        "window_6":    False,
    },
    {
        "label":       "Window 3",
        "description": "Wednesday open -> Tuesday close",
        "entry_day":   2,
        "entry_type":  "open",
        "exit_bars":   5,
        "exit_type":   "close",
        "window_6":    False,
    },
    {
        "label":       "Window 4",
        "description": "Thursday open -> Wednesday close",
        "entry_day":   3,
        "entry_type":  "open",
        "exit_bars":   5,
        "exit_type":   "close",
        "window_6":    False,
    },
    {
        "label":       "Window 5",
        "description": "Friday open -> Thursday close",
        "entry_day":   4,
        "entry_type":  "open",
        "exit_bars":   5,
        "exit_type":   "close",
        "window_6":    False,
    },
    {
        "label":       "Window 6",
        "description": "Friday close -> following Friday open  (weekend gap)",
        "entry_day":   4,   # Friday
        "entry_type":  "close",
        "exit_bars":   5,   # Mon-Fri of next week, exit at open of day 5
        "exit_type":   "open",
        "window_6":    True,
    },
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    _header("Weekly Window Comparison  —  Variant C across six entry/exit windows")
    print("  All windows use identical picks (scored on Monday of each week).")
    print("  Variant C: 1x ATR stop checked against daily close, no limits.")
    print()
    for w in WINDOWS:
        print(f"  {w['label']}:  {w['description']}")
    print()
    print("  52-week backtest.  Estimated time: 20-25 minutes.")
    print()

    confirm = input("  Press Enter to start, or 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        print("  Cancelled.")
        return

    tickers = get_tickers()
    print(f"\n  Universe: {len(tickers)} tickers")
    print("  Downloading price data...")
    price_data = _download_all_prices(tickers)
    print(f"  Downloaded: {len(price_data)} tickers\n")

    # Need extra bars for windows that start later in the week and extend
    # into the following week -- download with a larger period to ensure
    # we always have enough bars after sim_date
    now = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")

    print("  Pre-scoring all weeks (shared across all windows)...")
    weekly_picks = _prescore_weeks(tickers, price_data, now)
    total = sum(len(v) for v in weekly_picks.values())
    print(f"  {total} picks across {len(weekly_picks)} weeks.\n")

    all_results = {}

    for window in WINDOWS:
        print(f"  Simulating {window['label']}:  {window['description']}...")
        results = []
        for sim_date_str, picks in weekly_picks.items():
            sim_date_pd = pd.Timestamp(sim_date_str)
            for pick in picks:
                r = _simulate(pick, price_data, sim_date_pd, window)
                if r:
                    results.append(r)
        _apply_kelly(results)
        all_results[window["label"]] = results
        n = len(results)
        if n > 0:
            avg = sum(float(r["return_pct"]) for r in results) / n
            sign = "+" if avg >= 0 else ""
            print(f"    {n} picks  |  avg return: {sign}{avg:.3f}%")
        else:
            print(f"    No results.")

    print()

    # Print full results for each window
    for window in WINDOWS:
        _header(f"{window['label']}  —  {window['description']}")
        _print_results(all_results[window["label"]])

    # Print summary comparison
    _header("Summary Comparison")
    _print_summary(WINDOWS, all_results)


# ---------------------------------------------------------------------------
# Pre-scoring
# ---------------------------------------------------------------------------

def _prescore_weeks(tickers, price_data, now):
    weekly = {}
    for week_offset in range(BACKTEST_WEEKS_TECHNICAL, 0, -1):
        sim_date     = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=14)  # extra week for late windows
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
# Simulation
# ---------------------------------------------------------------------------

def _get_df(ticker, price_data):
    df = price_data.get(ticker + ".L")
    if df is None:
        df = price_data.get(ticker)
    return df


def _simulate(pick, price_data, sim_date_pd, window):
    """
    Simulate a single pick for the given window.

    For windows 1-5:
      - Find the Nth trading bar after sim_date (entry_day offset)
      - Use open or close of that bar as entry price
      - Check stop against the close of each subsequent bar
      - Exit at the close of bar entry_day + exit_bars, or stop close

    For window 6 (Friday close -> Friday open):
      - Entry: close of 4th bar after sim_date (Friday)
      - Stop recalculated from entry
      - Check stop against Mon-Thu closes of the following week
      - Exit: open of 5th bar of following week (Friday open)
    """
    ticker = pick["ticker"]
    df     = _get_df(ticker, price_data)
    if df is None:
        return None

    atr = pick.get("atr", 0)

    try:
        # Get enough bars to cover entry + 5 trading days
        # For later windows we need up to 10 bars after sim_date
        all_future = df[df.index > sim_date_pd].head(12)
        if len(all_future) < window["entry_day"] + 1:
            return None
    except Exception:
        return None

    # --- Entry ---
    entry_bar = all_future.iloc[window["entry_day"]]
    if window["entry_type"] == "open":
        entry_price = float(entry_bar["open"])
    else:
        entry_price = float(entry_bar["close"])

    if entry_price <= 0:
        return None

    # --- Stop price ---
    if atr > 0:
        stop_price = round(entry_price - STOP_MULTIPLIER * atr, 2)
    else:
        orig_dist  = pick["price"] - pick["stop"]
        stop_price = round(entry_price - orig_dist, 2)

    # --- Bars to check stop against ---
    # Include the entry bar's own close as the first check -- if the stock
    # closes below the stop on the entry day itself, exit that close.
    check_start = window["entry_day"]
    check_bars  = all_future.iloc[check_start:check_start + window["exit_bars"]]

    if check_bars.empty:
        return None

    exit_price  = None
    exit_day    = None
    exit_reason = None

    if window["window_6"]:
        # Window 6: check stop against closes Mon-Thu, exit at Friday open
        for day_idx, (_, bar) in enumerate(check_bars.iterrows(), 1):
            # Last bar: exit at open (Friday open of following week)
            if day_idx == window["exit_bars"]:
                exit_price  = float(bar["open"])
                exit_day    = day_idx
                exit_reason = "window_end_open"
                break
            day_close = float(bar["close"])
            if day_close <= stop_price:
                exit_price  = day_close
                exit_day    = day_idx
                exit_reason = "stop_close"
                break
    else:
        # Windows 1-5: check stop against closes, exit at final close
        for day_idx, (_, bar) in enumerate(check_bars.iterrows(), 1):
            day_close = float(bar["close"])
            if day_close <= stop_price:
                exit_price  = day_close
                exit_day    = day_idx
                exit_reason = "stop_close"
                break

        if exit_price is None:
            exit_price  = float(check_bars["close"].iloc[-1])
            exit_day    = len(check_bars)
            exit_reason = "window_end_close"

    if exit_price is None or exit_price <= 0:
        return None

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
            atr      = float(r["atr"]) or abs(
                float(r["entry_price"]) - float(r["stop_price"]))
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
# Display
# ---------------------------------------------------------------------------

def _header(title):
    print(f"\n{'='*64}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{'='*64}")


def _c(val):
    c = GREEN if val >= 0 else RED
    return f"{c}{val:+.3f}%{RESET}"


def _print_results(results):
    if not results:
        print("  No results.")
        return

    returns   = [float(r["return_pct"]) for r in results]
    n         = len(returns)
    pos       = [r for r in returns if r > 0]
    neg       = [r for r in returns if r <= 0]
    stops     = sum(1 for r in results if r.get("exit_reason") == "stop_close")
    win_ends  = n - stops

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

    print(f"\n  Picks:   {n}  across {len(week_avgs)} weeks")
    print(f"  Avg return (simple):    {_c(sum(returns)/n)}")
    print(f"  Avg return (Kelly-wtd): {_c(kelly)}")
    print(f"  Directional accuracy:   {sum(r['went_up'] for r in results)/n*100:.1f}%")
    print(f"  Avg winner:             {_c(sum(pos)/len(pos) if pos else 0)}")
    print(f"  Avg loser:              {_c(sum(neg)/len(neg) if neg else 0)}")
    print(f"  Best week:              {_c(max(week_avgs.values()))}")
    print(f"  Worst week:             {_c(min(week_avgs.values()))}")
    print(f"  Negative weeks:         {neg_weeks} / {len(week_avgs)}  "
          f"({neg_weeks/len(week_avgs)*100:.1f}%)")
    print(f"  Stop exits:             {stops}  ({stops/n*100:.1f}%)")
    print(f"  Window-end exits:       {win_ends}  ({win_ends/n*100:.1f}%)")

    print(f"\n  Return distribution:")
    for label, fn in BUCKETS:
        count = sum(1 for r in returns if fn(r))
        bar   = "█" * int(count / n * 40)
        print(f"    {label:>14}  {count:>4}  {bar}")


def _print_summary(windows, all_results):
    def stats(results):
        if not results:
            return {}
        returns   = [float(r["return_pct"]) for r in results]
        n         = len(returns)
        by_week   = defaultdict(list)
        for r in results:
            by_week[r["run_date"][:10]].append(float(r["return_pct"]))
        week_avgs = {w: sum(v)/len(v) for w, v in by_week.items()}
        allocs    = [(float(r["return_pct"]), float(r.get("allocation_pct") or 0))
                     for r in results]
        total_a   = sum(a for _, a in allocs)
        kelly     = sum(ret*a for ret, a in allocs)/total_a if total_a > 0 else sum(returns)/n
        stops     = sum(1 for r in results if r.get("exit_reason") == "stop_close")
        return {
            "n":        n,
            "avg":      sum(returns) / n,
            "kelly":    kelly,
            "dir_acc":  sum(r["went_up"] for r in results) / n * 100,
            "avg_win":  sum(r for r in returns if r > 0) / max(1, sum(1 for r in returns if r > 0)),
            "avg_loss": sum(r for r in returns if r <= 0) / max(1, sum(1 for r in returns if r <= 0)),
            "worst":    min(week_avgs.values()),
            "best":     max(week_avgs.values()),
            "neg_wks":  sum(1 for v in week_avgs.values() if v < 0),
            "stop_pct": stops / n * 100,
        }

    all_stats = {w["label"]: stats(all_results[w["label"]]) for w in windows}

    # Key metrics table
    metrics = [
        ("Avg return",     "avg",      True,  "{:>+7.3f}%"),
        ("Kelly return",   "kelly",    True,  "{:>+7.3f}%"),
        ("Dir. accuracy",  "dir_acc",  True,  "{:>7.1f}%"),
        ("Avg winner",     "avg_win",  True,  "{:>+7.3f}%"),
        ("Avg loser",      "avg_loss", False, "{:>+7.3f}%"),
        ("Best week",      "best",     True,  "{:>+7.3f}%"),
        ("Worst week",     "worst",    False, "{:>+7.3f}%"),
        ("Negative weeks", "neg_wks",  False, "{:>7.0f}"),
        ("Stop hit rate",  "stop_pct", False, "{:>7.1f}%"),
    ]

    labels = [w["label"] for w in windows]
    print(f"\n  {'Metric':<18}", end="")
    for label in labels:
        print(f"  {label:>10}", end="")
    print()
    print(f"  {'-'*18}", end="")
    for _ in labels:
        print(f"  {'-'*10}", end="")
    print()

    for metric_name, key, higher_better, fmt in metrics:
        vals = [all_stats[l].get(key, 0) for l in labels]
        best = max(vals) if higher_better else min(vals)
        print(f"  {metric_name:<18}", end="")
        for v in vals:
            is_best = abs(v - best) < 0.005
            c = GREEN if is_best else ""
            print(f"  {c}{fmt.format(v)}{RESET if c else ''}", end="")
        print()

    # Ranking by avg return
    ranked = sorted(
        [(w["label"], all_stats[w["label"]].get("avg", 0), w["description"])
         for w in windows],
        key=lambda x: x[1], reverse=True
    )

    print(f"\n  Ranking by average return:")
    for rank, (label, avg, desc) in enumerate(ranked, 1):
        c    = GREEN if rank == 1 else (DIM if rank == len(ranked) else "")
        sign = "+" if avg >= 0 else ""
        print(f"  {rank}.  {c}{label}  {sign}{avg:.3f}%  --  {desc}{RESET}")

    # Weekend gap analysis (Window 6)
    w6 = all_results.get("Window 6", [])
    w1 = all_results.get("Window 1", [])
    if w6 and w1:
        w6_avg = sum(float(r["return_pct"]) for r in w6) / len(w6)
        w1_avg = sum(float(r["return_pct"]) for r in w1) / len(w1)
        gap    = w6_avg - w1_avg
        print(f"\n  Weekend gap analysis (Window 6 vs Window 1):")
        print(f"  Friday close -> Friday open: {_c(w6_avg)}")
        print(f"  Monday open  -> Friday close: {_c(w1_avg)}")
        c = GREEN if gap > 0 else RED
        print(f"  Holding over weekend adds: {c}{gap:+.3f}pp{RESET}")
        if gap > 0.05:
            print(f"  {GREEN}Stocks tend to gap UP over the weekend.{RESET}")
            print(f"  Selling on Friday close and rebuying Monday open costs returns.")
        elif gap < -0.05:
            print(f"  {RED}Stocks tend to gap DOWN over the weekend.{RESET}")
            print(f"  Selling Friday close to avoid gap risk is justified.")
        else:
            print(f"  Weekend gaps are negligible -- no systematic direction.")

    # Best window verdict
    best_label, best_avg, best_desc = ranked[0]
    worst_label, worst_avg, worst_desc = ranked[-1]
    print(f"\n  {'='*60}")
    print(f"  {BOLD}Verdict{RESET}")
    print(f"  {'='*60}")
    print(f"  Best window:  {GREEN}{best_label}  ({best_avg:+.3f}%)  {best_desc}{RESET}")
    print(f"  Worst window: {RED}{worst_label}  ({worst_avg:+.3f}%)  {worst_desc}{RESET}")
    diff = best_avg - worst_avg
    print(f"  Spread between best and worst: {diff:.3f}pp")
    if diff < 0.1:
        print(f"  Spread is small -- day of week has minimal impact.")
        print(f"  Monday open -> Friday close is fine as the standard window.")
    else:
        print(f"  Meaningful spread -- consider shifting to {best_label}.")
    print()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback; traceback.print_exc()
