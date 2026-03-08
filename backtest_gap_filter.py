#!/usr/bin/env python3
"""
backtest_gap_filter.py
-----------------------
Tests whether filtering out picks that gap down past their stop price
at Tuesday open improves returns.

All three variants use:
  - Scoring on Monday close (last bar available Tuesday morning)
  - Window 2: Tuesday open -> Monday close
  - Variant C: 1x ATR stop checked against daily close
  - Stop recalculated from actual Tuesday open entry price

The three variants differ only in how they handle a pick where
Tuesday's open is already at or below the stop price:

Option A -- Buy everything (current backtest behaviour)
  Buy all 5 picks at Tuesday open regardless of where they open.
  If a pick opens below its stop, you buy it anyway and it
  immediately exits at the open price (which is at or below stop).
  This is what the backtests have been doing implicitly.

Option B -- Skip gapped-down picks, no replacement
  If Tuesday open <= stop price, skip that pick entirely.
  Trade fewer than 5 picks that week if necessary.
  Capital not deployed for skipped picks.

Option C -- Skip gapped-down picks, replace with next best
  If Tuesday open <= stop price, skip that pick and replace it
  with the next best pick from that week's scored list (6th, 7th
  pick etc.) that does NOT open below its stop.
  Always attempts to deploy full capital across 5 picks.
  Requires scoring more candidates per week.

Key question: does skipping gap-down picks improve returns enough
to justify the operational complexity, and does replacing them
add further value?

Run from LSE_Stock_Analyser/ with:
  python3 backtest_gap_filter.py
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

TRADING_DAYS   = 5
TOP_N          = 5    # picks per week
RESERVE_N      = 10   # extra candidates to score for Option C replacements

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
    _header("Gap Filter Comparison  —  Tuesday open, Monday close")
    print("  Tests whether skipping picks that gap down past their")
    print("  stop price at Tuesday open improves returns.")
    print()
    print("  Option A: Buy all picks regardless of gap  (current behaviour)")
    print("  Option B: Skip gapped-down picks, no replacement")
    print("  Option C: Skip gapped-down picks, replace with next best")
    print()
    print("  Window: Tuesday open -> Monday close  (Window 2, best performer)")
    print("  Stop:   1x ATR below Tuesday open entry price")
    print("  Scorer: uses data up to and including Monday close")
    print()
    print("  51-week backtest.  Estimated time: 15-20 minutes.")
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

    # Anchor to Monday -- scorer uses Monday close as last bar
    # Then entry is Tuesday open (first bar after Monday sim_date)
    now = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")

    print("  Pre-scoring all weeks (using Monday close as last bar)...")
    weekly_picks = _prescore_weeks(tickers, price_data, now)
    total = sum(len(v) for v in weekly_picks.values())
    print(f"  {total} top picks + reserves across {len(weekly_picks)} weeks.\n")

    # Simulate all three options
    a_results, a_stats = [], {"gaps_skipped": 0, "gaps_bought": 0}
    b_results, b_stats = [], {"gaps_skipped": 0, "gaps_bought": 0}
    c_results, c_stats = [], {"gaps_skipped": 0, "gaps_replaced": 0, "gaps_bought": 0}

    for sim_date_str, week_data in weekly_picks.items():
        sim_date_pd = pd.Timestamp(sim_date_str)
        top_picks   = week_data["top"]
        reserve     = week_data["reserve"]

        # Option A: buy everything
        for pick in top_picks:
            r = _simulate(pick, price_data, sim_date_pd)
            if r:
                a_results.append(r)
                if r["gapped_down"]:
                    a_stats["gaps_bought"] += 1

        # Option B: skip gapped-down picks
        for pick in top_picks:
            r = _simulate(pick, price_data, sim_date_pd)
            if r:
                if r["gapped_down"]:
                    b_stats["gaps_skipped"] += 1
                else:
                    b_results.append(r)

        # Option C: skip gapped-down picks, replace with next best
        week_c = []
        used_tickers = set()
        for pick in top_picks:
            r = _simulate(pick, price_data, sim_date_pd)
            if r:
                if r["gapped_down"]:
                    c_stats["gaps_skipped"] += 1
                    # Try to find a replacement from reserve
                    replaced = False
                    for res_pick in reserve:
                        if res_pick["ticker"] in used_tickers:
                            continue
                        res_r = _simulate(res_pick, price_data, sim_date_pd)
                        if res_r and not res_r["gapped_down"]:
                            week_c.append(res_r)
                            used_tickers.add(res_pick["ticker"])
                            c_stats["gaps_replaced"] += 1
                            replaced = True
                            break
                    if not replaced:
                        c_stats["gaps_bought"] += 1  # no replacement found
                else:
                    week_c.append(r)
                    used_tickers.add(pick["ticker"])
        c_results.extend(week_c)

    # Apply Kelly sizing
    _apply_kelly(a_results)
    _apply_kelly(b_results)
    _apply_kelly(c_results)

    # Print results
    _header("Option A  —  Buy all picks regardless of gap")
    _print_results(a_results, a_stats)

    _header("Option B  —  Skip gapped-down picks, no replacement")
    _print_results(b_results, b_stats)

    _header("Option C  —  Skip gapped-down picks, replace with next best")
    _print_results(c_results, c_stats)

    _header("Comparison")
    _print_comparison(a_results, b_results, c_results)


# ---------------------------------------------------------------------------
# Pre-scoring
# ---------------------------------------------------------------------------

def _prescore_weeks(tickers, price_data, now):
    """
    Score each week using Monday close as the last bar.
    Returns top TOP_N picks plus RESERVE_N extras for Option C replacements.
    Uses 14-day cutoff to ensure data exists for the full Tuesday->Monday window.
    """
    weekly = {}
    for week_offset in range(BACKTEST_WEEKS_TECHNICAL, 0, -1):
        sim_date     = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=14)
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
                # Use Monday close -- sim_date is Monday so this is correct
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
        top     = diversify(candidates, TOP_N)
        # Reserve: next best picks after top N, diversified from remaining
        top_tickers = {p["ticker"] for p in top}
        remaining   = [c for c in candidates if c["ticker"] not in top_tickers]
        reserve     = diversify(remaining, RESERVE_N) if remaining else []

        weekly[sim_date.strftime("%Y-%m-%d %H:%M")] = {
            "top":     top,
            "reserve": reserve,
        }

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


def _simulate(pick, price_data, sim_date_pd):
    """
    Simulate Window 2: Tuesday open -> Monday close.
    Entry: first bar open after sim_date (Tuesday open).
    Stop: recalculated from actual entry price.
    Stop checked against: Tue/Wed/Thu/Fri/Mon closes.
    Exit: stop close or Monday close.
    gapped_down: True if Tuesday open <= stop price.
    """
    ticker = pick["ticker"]
    df     = _get_df(ticker, price_data)
    if df is None:
        return None

    atr = pick.get("atr", 0)

    try:
        # Need Tuesday bar (index 0) + 5 check bars (indices 0-4)
        # + Monday close of following week (index 5)
        all_future = df[df.index > sim_date_pd].head(8)
        if len(all_future) < 2:
            return None
    except Exception:
        return None

    # Entry: Tuesday open (first bar after Monday sim_date)
    tuesday_bar = all_future.iloc[0]
    entry_price = float(tuesday_bar["open"])
    if entry_price <= 0:
        return None

    # Stop recalculated from actual Tuesday open entry
    if atr > 0:
        stop_price = round(entry_price - STOP_MULTIPLIER * atr, 2)
    else:
        orig_dist  = pick["price"] - pick["stop"]
        stop_price = round(entry_price - orig_dist, 2)

    # Gap-down check: did Tuesday open below the stop calculated from
    # Monday's close (the original scored stop price)?
    # This is what you'd know BEFORE placing the buy order.
    original_stop = pick["stop"]  # stop based on Monday close
    gapped_down   = entry_price <= original_stop

    # Check bars: Tuesday close through Monday close (5 bars)
    # iloc[0] = Tuesday bar, iloc[1] = Wednesday, ... iloc[5] = Monday
    check_bars = all_future.iloc[0:TRADING_DAYS + 1]

    exit_price  = None
    exit_day    = None
    exit_reason = None

    for day_idx, (_, bar) in enumerate(check_bars.iterrows(), 1):
        day_close = float(bar["close"])

        # Day 6 = Monday -- exit at close regardless
        if day_idx == TRADING_DAYS + 1:
            exit_price  = day_close
            exit_day    = day_idx
            exit_reason = "window_end"
            break

        if day_close <= stop_price:
            exit_price  = day_close
            exit_day    = day_idx
            exit_reason = "stop_close"
            break

    if exit_price is None:
        exit_price  = float(check_bars["close"].iloc[-1])
        exit_day    = len(check_bars)
        exit_reason = "window_end"

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
        "gapped_down":   gapped_down,
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
    return f"{c}{val:+.3f}{RESET}%"


def _print_results(results, stats):
    if not results:
        print("  No results.")
        return

    returns   = [float(r["return_pct"]) for r in results]
    n         = len(returns)
    pos       = [r for r in returns if r > 0]
    neg       = [r for r in returns if r <= 0]
    stops     = sum(1 for r in results if r.get("exit_reason") == "stop_close")
    win_ends  = sum(1 for r in results if r.get("exit_reason") == "window_end")
    gaps_bought = sum(1 for r in results if r.get("gapped_down"))

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

    # Gap stats
    total_gaps = stats.get("gaps_skipped", 0) + stats.get("gaps_bought", 0) + stats.get("gaps_replaced", 0)
    if total_gaps > 0 or gaps_bought > 0:
        print(f"\n  Gap-down activity:")
        if stats.get("gaps_bought", 0) > 0 or gaps_bought > 0:
            print(f"    Picks that opened below stop and were bought: {gaps_bought}")
        if stats.get("gaps_skipped", 0) > 0:
            print(f"    Picks skipped (opened below stop):            {stats['gaps_skipped']}")
        if stats.get("gaps_replaced", 0) > 0:
            print(f"    Skipped picks replaced with next best:        {stats['gaps_replaced']}")

    print(f"\n  Return distribution:")
    for label, fn in BUCKETS:
        count = sum(1 for r in returns if fn(r))
        bar   = "█" * int(count / n * 40)
        print(f"    {label:>14}  {count:>4}  {bar}")


def _print_comparison(a_results, b_results, c_results):
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
        gaps      = sum(1 for r in results if r.get("gapped_down"))
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
            "gap_pct":  gaps / n * 100,
        }

    sa, sb, sc = stats(a_results), stats(b_results), stats(c_results)

    metrics = [
        ("Avg return",     "avg",      True,  lambda v: f"{v:>+8.3f}%"),
        ("Kelly return",   "kelly",    True,  lambda v: f"{v:>+8.3f}%"),
        ("Dir. accuracy",  "dir_acc",  True,  lambda v: f"{v:>8.1f}%"),
        ("Avg winner",     "avg_win",  True,  lambda v: f"{v:>+8.3f}%"),
        ("Avg loser",      "avg_loss", False, lambda v: f"{v:>+8.3f}%"),
        ("Best week",      "best",     True,  lambda v: f"{v:>+8.3f}%"),
        ("Worst week",     "worst",    False, lambda v: f"{v:>+8.3f}%"),
        ("Negative weeks", "neg_wks",  False, lambda v: f"{v:>8.0f}"),
        ("Stop hit rate",  "stop_pct", False, lambda v: f"{v:>8.1f}%"),
        ("Gap-down rate",  "gap_pct",  False, lambda v: f"{v:>8.1f}%"),
        ("Total picks",    "n",        True,  lambda v: f"{v:>8.0f}"),
    ]

    print(f"\n  {'Metric':<20}  {'Option A':>10}  {'Option B':>10}  {'Option C':>10}")
    print(f"  {'':20}  {'(buy all)':>10}  {'(skip)':>10}  {'(replace)':>10}")
    print(f"  {'-'*20}  {'-'*10}  {'-'*10}  {'-'*10}")

    for name, key, hb, fmt_fn in metrics:
        vals = [sa.get(key, 0), sb.get(key, 0), sc.get(key, 0)]
        best = max(vals) if hb else min(vals)
        row  = f"  {name:<20}"
        for v in vals:
            is_best = abs(v - best) < 0.005
            c       = GREEN if is_best else ""
            row    += f"  {c}{fmt_fn(v)}{RESET if c else ''}"
        print(row)

    # Verdict
    avgs  = [sa.get("avg", 0), sb.get("avg", 0), sc.get("avg", 0)]
    best_idx = avgs.index(max(avgs))
    labels   = ["Option A (buy all)", "Option B (skip)", "Option C (replace)"]

    print(f"\n  {'='*58}")
    print(f"  {BOLD}Verdict{RESET}")
    print(f"  {'='*58}")
    print(f"  Best avg return: {GREEN}{labels[best_idx]}  ({avgs[best_idx]:+.3f}%){RESET}")
    print()

    gap_rate = sa.get("gap_pct", 0)
    print(f"  Gap-down picks (opened below stop): {gap_rate:.1f}% of all picks")

    if gap_rate < 2.0:
        print(f"  {GREEN}Gap-downs are very rare -- filtering adds minimal value.{RESET}")
        print(f"  Option A (buy everything) is fine operationally.")
    elif best_idx == 0:
        print(f"  Despite gap-downs occurring, buying them still produces")
        print(f"  the best overall return. The gap-down picks may recover.")
    elif best_idx == 1:
        print(f"  Skipping gap-down picks improves returns.")
        print(f"  Operational rule: if Tuesday open <= stop price, skip the pick.")
    else:
        print(f"  Replacing gap-down picks with next best improves returns further.")
        print(f"  Programme should output extra reserve picks for substitution.")
    print()

    # Show what gap-down picks actually returned in Option A
    gap_returns = [float(r["return_pct"]) for r in a_results if r.get("gapped_down")]
    if gap_returns:
        avg_gap = sum(gap_returns) / len(gap_returns)
        pos_gap = sum(1 for r in gap_returns if r > 0)
        print(f"  Gap-down pick returns (Option A):")
        print(f"    Count:             {len(gap_returns)}")
        print(f"    Avg return:        {_c(avg_gap)}")
        print(f"    Positive outcomes: {pos_gap} / {len(gap_returns)}  "
              f"({pos_gap/len(gap_returns)*100:.1f}%)")
        if avg_gap < -0.5:
            print(f"    {RED}Gap-down picks are reliably poor -- skip them.{RESET}")
        elif avg_gap > 0:
            print(f"    {GREEN}Gap-down picks still produce positive returns on average.{RESET}")
        else:
            print(f"    Gap-down picks are marginally negative -- borderline case.")
    print()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback; traceback.print_exc()
