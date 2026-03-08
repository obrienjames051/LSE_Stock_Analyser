#!/usr/bin/env python3
"""
backtest_a_vs_c.py
-------------------
Clean comparison of Variant A vs Variant C, both using Monday open
as entry price and Friday close as exit, over a full 52-week period.

Variant A -- No stops, no limits
  Buy at Monday open, hold to Friday close regardless.
  No daily monitoring required.

Variant C -- Stop at 1x ATR, no limits
  Buy at Monday open.
  Check each day's close against stop price.
  If close <= stop: exit at that close.
  If stop never triggered: sell at Friday close.

Both variants use identical picks each week so the only difference
is the stop logic. This gives a clean read on what the stop actually
adds or costs in return and risk terms.

Run from LSE_Stock_Analyser/ with:
  python3 backtest_a_vs_c.py
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    _header("Variant A vs Variant C  —  Monday open entry, Friday close exit")
    print("  Both variants use identical picks each week.")
    print("  Entry: Monday open (first bar after sim_date)")
    print("  Exit:  Friday close, or daily close that breaches stop (C only)")
    print("  Stop:  1x ATR below Monday open entry price")
    print()
    print("  52-week backtest.  Estimated time: 15-20 minutes.")
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

    now = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")

    print("  Pre-scoring all weeks...")
    weekly_picks = _prescore_weeks(tickers, price_data, now)
    total = sum(len(v) for v in weekly_picks.values())
    print(f"  {total} picks across {len(weekly_picks)} weeks.\n")

    # --- Variant A ---
    print("  Simulating Variant A (no stops, no limits)...")
    a_results = []
    for sim_date_str, picks in weekly_picks.items():
        sim_date_pd = pd.Timestamp(sim_date_str)
        for pick in picks:
            r = _sim_variant_a(pick, price_data, sim_date_pd)
            if r:
                a_results.append(r)
    _apply_kelly(a_results)
    print(f"  {len(a_results)} picks simulated.\n")

    # --- Variant C ---
    print("  Simulating Variant C (stop at daily close, no limits)...")
    c_results = []
    for sim_date_str, picks in weekly_picks.items():
        sim_date_pd = pd.Timestamp(sim_date_str)
        for pick in picks:
            r = _sim_variant_c(pick, price_data, sim_date_pd)
            if r:
                c_results.append(r)
    _apply_kelly(c_results)
    print(f"  {len(c_results)} picks simulated.\n")

    # --- Results ---
    _header("Variant A  —  No stops, no limits")
    _print_results(a_results)

    _header("Variant C  —  Stop at daily close, no limits")
    _print_results(c_results)

    _header("Comparison")
    _print_comparison(a_results, c_results)


# ---------------------------------------------------------------------------
# Pre-scoring
# ---------------------------------------------------------------------------

def _prescore_weeks(tickers, price_data, now):
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
# Entry price
# ---------------------------------------------------------------------------

def _get_monday_open(pick, df, sim_date_pd):
    """First daily bar open strictly after sim_date -- Monday's open."""
    try:
        future = df[df.index > sim_date_pd]
        if future.empty:
            return None
        open_price = float(future["open"].iloc[0])
        return open_price if open_price > 0 else None
    except Exception:
        return None


def _get_df(ticker, price_data):
    df = price_data.get(ticker + ".L")
    if df is None:
        df = price_data.get(ticker)
    return df


# ---------------------------------------------------------------------------
# Variant A simulation
# ---------------------------------------------------------------------------

def _sim_variant_a(pick, price_data, sim_date_pd):
    """Buy Monday open, sell Friday close. No stops, no limits."""
    ticker = pick["ticker"]
    df     = _get_df(ticker, price_data)
    if df is None:
        return None

    entry_price = _get_monday_open(pick, df, sim_date_pd)
    if entry_price is None:
        return None

    try:
        future_bars = df[df.index > sim_date_pd].head(TRADING_DAYS)
        if future_bars.empty:
            return None
        exit_price = float(future_bars["close"].iloc[-1])
        exit_day   = len(future_bars)
    except Exception:
        return None

    return _make_result(pick, sim_date_pd, entry_price, pick["stop"],
                        pick.get("atr", 0), exit_price, exit_day, "friday_close")


# ---------------------------------------------------------------------------
# Variant C simulation
# ---------------------------------------------------------------------------

def _sim_variant_c(pick, price_data, sim_date_pd):
    """Buy Monday open. Exit at close that breaches stop, else Friday close."""
    ticker = pick["ticker"]
    df     = _get_df(ticker, price_data)
    if df is None:
        return None

    entry_price = _get_monday_open(pick, df, sim_date_pd)
    if entry_price is None:
        return None

    # Recalculate stop relative to actual entry (Monday open)
    atr = pick.get("atr", 0)
    if atr > 0:
        stop_price = round(entry_price - STOP_MULTIPLIER * atr, 2)
    else:
        orig_dist  = pick["price"] - pick["stop"]
        stop_price = round(entry_price - orig_dist, 2)

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
        exit_price  = float(future_bars["close"].iloc[-1])
        exit_day    = len(future_bars)
        exit_reason = "friday_close"

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
    print(f"\n{'='*62}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{'='*62}")


def _c(val):
    c = GREEN if val >= 0 else RED
    return f"{c}{val:+.3f}%{RESET}"


def _print_results(results):
    if not results:
        print("  No results.")
        return

    returns = [float(r["return_pct"]) for r in results]
    n       = len(returns)
    pos     = [r for r in returns if r > 0]
    neg     = [r for r in returns if r <= 0]
    stops   = sum(1 for r in results if r.get("exit_reason") == "stop_close")
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

    print(f"\n  Picks:   {n}  across {len(week_avgs)} weeks")
    print(f"  Entry:   Monday open")
    print()
    print(f"  Avg return (simple):    {_c(sum(returns)/n)}")
    print(f"  Avg return (Kelly-wtd): {_c(kelly)}")
    print(f"  Directional accuracy:   {sum(r['went_up'] for r in results)/n*100:.1f}%")
    print(f"  Avg winner:             {_c(sum(pos)/len(pos) if pos else 0)}")
    print(f"  Avg loser:              {_c(sum(neg)/len(neg) if neg else 0)}")
    print(f"  Best week:              {_c(max(week_avgs.values()))}")
    print(f"  Worst week:             {_c(min(week_avgs.values()))}")
    print(f"  Negative weeks:         {neg_weeks} / {len(week_avgs)}  "
          f"({neg_weeks/len(week_avgs)*100:.1f}%)")
    if stops > 0:
        print(f"  Stop exits:             {stops}  ({stops/n*100:.1f}%)")
    print(f"  Friday exits:           {fridays}  ({fridays/n*100:.1f}%)")

    print(f"\n  Return distribution:")
    for label, fn in BUCKETS:
        count = sum(1 for r in returns if fn(r))
        bar   = "█" * int(count / n * 40)
        print(f"    {label:>14}  {count:>4}  {bar}")


def _print_comparison(a_results, c_results):
    def stats(results):
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
            "avg":       sum(returns) / n,
            "kelly":     kelly,
            "dir_acc":   sum(r["went_up"] for r in results) / n * 100,
            "avg_win":   sum(r for r in returns if r > 0) / max(1, sum(1 for r in returns if r > 0)),
            "avg_loss":  sum(r for r in returns if r <= 0) / max(1, sum(1 for r in returns if r <= 0)),
            "best":      max(week_avgs.values()),
            "worst":     min(week_avgs.values()),
            "neg_weeks": sum(1 for v in week_avgs.values() if v < 0),
            "n_weeks":   len(week_avgs),
            "stop_pct":  stops / n * 100,
        }

    a = stats(a_results)
    c = stats(c_results)

    def row(label, key, higher_better=True, fmt="{:>+8.3f}%"):
        av, cv  = a[key], c[key]
        a_best  = (av > cv) == higher_better if av != cv else False
        c_best  = (cv > av) == higher_better if av != cv else False
        a_str   = f"{GREEN if a_best else ''}{fmt.format(av)}{RESET if a_best else ''}"
        c_str   = f"{GREEN if c_best else ''}{fmt.format(cv)}{RESET if c_best else ''}"
        diff    = cv - av
        d_c     = GREEN if (diff > 0) == higher_better else RED
        sign    = "+" if diff >= 0 else ""
        d_str   = f"{d_c}{sign}{diff:.3f}{RESET}"
        print(f"  {label:<28}  {a_str}  {c_str}  {d_str}")

    print(f"\n  {'Metric':<28}  {'Variant A':>10}  {'Variant C':>10}  {'C minus A':>10}")
    print(f"  {'-'*28}  {'-'*10}  {'-'*10}  {'-'*10}")
    row("Avg return (simple)",     "avg")
    row("Avg return (Kelly-wtd)",  "kelly")
    row("Directional accuracy",    "dir_acc")
    row("Avg winner",              "avg_win")
    row("Avg loser",               "avg_loss",   higher_better=False)
    row("Best week",               "best")
    row("Worst week",              "worst",      higher_better=False)
    row("Negative weeks",          "neg_weeks",  higher_better=False, fmt="{:>8.0f}")
    row("Stop hit rate",           "stop_pct",   higher_better=False, fmt="{:>8.1f}%")

    print()
    avg_diff   = c["avg"] - a["avg"]
    worst_diff = c["worst"] - a["worst"]

    print(f"  Return difference (C vs A):    {_c(avg_diff)}")
    print(f"  Worst week improvement (C-A):  {_c(worst_diff)}")
    print()

    if avg_diff >= -0.05:
        print(f"  {GREEN}Verdict: Variant C is the better choice.{RESET}")
        if avg_diff >= 0:
            print(f"  Stops improve average return AND reduce downside risk.")
        else:
            print(f"  Stops cost {abs(avg_diff):.3f}pp in avg return but the worst week")
            print(f"  improves by {abs(worst_diff):.3f}pp -- insurance is essentially free.")
    else:
        print(f"  {RED}Verdict: stops cost {abs(avg_diff):.3f}pp in avg return.{RESET}")
        print(f"  Worst week improves by {abs(worst_diff):.3f}pp.")
        print(f"  Whether the protection is worth the cost is a judgement call.")
    print()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback; traceback.print_exc()
