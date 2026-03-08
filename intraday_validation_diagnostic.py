#!/usr/bin/env python3
"""
intraday_validation_diagnostic.py
-----------------------------------
Compares intraday validation results against the daily-close backtest
results for the same variants, over the same recent period.

The key question: how many extra stop hits appear when checking intraday
lows vs daily closes? If the number is large, the daily backtest was
flattering the strategy and real-world returns will be lower.

Run from LSE_Stock_Analyser/ with:
  python3 intraday_validation_diagnostic.py
"""

import csv
import os
from collections import defaultdict

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"

# Intraday results (from backtest_intraday_validation.py)
INTRADAY_FILES = [
    ("C-base",       "lse_iv_base.csv"),
    ("C-trail-cons", "lse_iv_cons.csv"),
    ("C-trail-mod",  "lse_iv_mod.csv"),
    ("C-trail-agg",  "lse_iv_agg.csv"),
]

# Daily close results (from backtest_dynamic_stop.py) -- for the same
# recent weeks. We filter these to match the intraday date range.
DAILY_FILES = [
    ("C-base",       "lse_ds_base.csv"),
    ("C-trail-cons", "lse_ds_cons.csv"),
    ("C-trail-mod",  "lse_ds_mod.csv"),
    ("C-trail-agg",  "lse_ds_agg.csv"),
]

BUCKETS = [
    ("< -5%",      lambda r: r < -5),
    ("-5% to -3%", lambda r: -5 <= r < -3),
    ("-3% to -1%", lambda r: -3 <= r < -1),
    ("-1% to 0%",  lambda r: -1 <= r < 0),
    ("0% to +1%",  lambda r: 0  <= r < 1),
    ("+1% to +3%", lambda r: 1  <= r < 3),
    ("+3% to +5%", lambda r: 3  <= r < 5),
    ("> +5%",      lambda r: r >= 5),
]


def load(filepath):
    if not os.path.isfile(filepath):
        return None
    with open(filepath, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_date_range(rows):
    dates = []
    for r in rows:
        try:
            dates.append(r["run_date"][:10])
        except (KeyError, IndexError):
            pass
    return min(dates) if dates else None, max(dates) if dates else None


def filter_to_dates(rows, start, end):
    """Filter daily rows to only include weeks within the intraday date range."""
    filtered = []
    for r in rows:
        d = r.get("run_date", "")[:10]
        if start <= d <= end:
            filtered.append(r)
    return filtered


def stats(rows, stop_reason_key="stop_hit"):
    returns = []
    for r in rows:
        try:
            returns.append(float(r["return_pct"]))
        except (ValueError, KeyError):
            continue
    if not returns:
        return {}

    n   = len(returns)
    pos = [r for r in returns if r > 0]
    neg = [r for r in returns if r <= 0]

    stops   = sum(1 for r in rows if stop_reason_key in r.get("exit_reason", ""))
    fridays = sum(1 for r in rows if r.get("exit_reason") == "friday_close")
    moved   = sum(1 for r in rows if str(r.get("stop_moved", "")).lower() == "true")

    by_week   = defaultdict(list)
    for r in rows:
        try:
            by_week[r["run_date"][:10]].append(float(r["return_pct"]))
        except (ValueError, KeyError):
            pass
    week_avgs = {w: sum(v)/len(v) for w, v in by_week.items()}

    return {
        "n":           n,
        "avg":         sum(returns) / n,
        "dir_acc":     len(pos) / n * 100,
        "avg_winner":  sum(pos) / len(pos) if pos else 0,
        "avg_loser":   sum(neg) / len(neg) if neg else 0,
        "worst_week":  min(week_avgs.values()) if week_avgs else 0,
        "stops":       stops,
        "fridays":     fridays,
        "moved":       moved,
        "stop_pct":    stops / n * 100,
        "_returns":    returns,
    }


def col(diff, higher_better=True):
    if abs(diff) < 0.01:
        return "  ~  "
    good = (diff > 0) == higher_better
    c    = GREEN if good else RED
    sign = "+" if diff > 0 else ""
    arr  = "▲" if diff > 0 else "▼"
    return f"  {c}{arr}{sign}{diff:.2f}{RESET}"


def print_comparison(label, daily_s, intra_s, daily_rows, intra_rows):
    n_d = daily_s["n"]
    n_i = intra_s["n"]

    print(f"\n{'='*66}")
    print(f"{BOLD}  {label}{RESET}")
    print(f"  Daily close: {n_d} picks   |   Intraday: {n_i} picks")
    print(f"{'='*66}")

    metrics = [
        ("Avg return",         "avg",        True),
        ("Directional acc.",   "dir_acc",    True),
        ("Avg winner",         "avg_winner", True),
        ("Avg loser",          "avg_loser",  False),
        ("Worst week",         "worst_week", False),
    ]
    print(f"  {'Metric':<24}  {'Daily close':>12}  {'Intraday':>12}  {'Diff':>10}")
    print(f"  {'-'*24}  {'-'*12}  {'-'*12}  {'-'*10}")
    for name, key, hb in metrics:
        dv = daily_s[key]
        iv = intra_s[key]
        print(f"  {name:<24}  {dv:>+11.2f}%  {iv:>+11.2f}%{col(iv-dv, hb)}")

    # Stop hit comparison -- the key metric
    print(f"\n  Stop activity:")
    print(f"  {'':24}  {'Daily close':>12}  {'Intraday':>12}  {'Extra':>10}")
    print(f"  {'-'*24}  {'-'*12}  {'-'*12}  {'-'*10}")

    d_stops  = daily_s["stops"]
    i_stops  = intra_s["stops"]
    d_fri    = daily_s["fridays"]
    i_fri    = intra_s["fridays"]
    d_moved  = daily_s["moved"]
    i_moved  = intra_s["moved"]

    extra_stops = i_stops - d_stops
    stop_col    = RED if extra_stops > 3 else (GREEN if extra_stops <= 0 else "")

    print(f"  {'Stop hits':<24}  {d_stops:>11}   {i_stops:>11}   "
          f"{stop_col}{'+' if extra_stops >= 0 else ''}{extra_stops}{RESET}")
    print(f"  {'Friday closes':<24}  {d_fri:>11}   {i_fri:>11}   "
          f"{d_fri - i_fri:>+10}")
    print(f"  {'Stops trailed':<24}  {d_moved:>11}   {i_moved:>11}")

    # Return distribution comparison
    d_ret = daily_s["_returns"]
    i_ret = intra_s["_returns"]
    print(f"\n  Return distribution (daily close vs intraday):")
    for blabel, fn in BUCKETS:
        dc  = sum(1 for r in d_ret if fn(r))
        ic  = sum(1 for r in i_ret if fn(r))
        diff = ic - dc
        c    = RED if diff < -2 else (GREEN if diff > 2 else "")
        sign = "+" if diff >= 0 else ""
        print(f"    {blabel:>14}  daily:{dc:>4}  intra:{ic:>4}  {c}({sign}{diff}){RESET}")


def print_summary(labels, daily_stats_list, intra_stats_list):
    print(f"\n{'='*66}")
    print(f"{BOLD}  SUMMARY: Daily Close vs Intraday Stop Hits{RESET}")
    print(f"{'='*66}")
    print(f"  {'Variant':<16}  {'Daily stops':>12}  {'Intra stops':>12}  "
          f"{'Extra hits':>11}  {'Daily avg':>10}  {'Intra avg':>10}")
    print(f"  {'-'*16}  {'-'*12}  {'-'*12}  {'-'*11}  {'-'*10}  {'-'*10}")

    for label, ds, is_ in zip(labels, daily_stats_list, intra_stats_list):
        extra = is_["stops"] - ds["stops"]
        c     = RED if extra > 3 else (GREEN if extra <= 0 else "")
        print(
            f"  {label:<16}  {ds['stops']:>12}   {is_['stops']:>12}   "
            f"{c}{'+' if extra >= 0 else ''}{extra:>10}{RESET}  "
            f"{ds['avg']:>+9.2f}%  {is_['avg']:>+9.2f}%"
        )

    print(f"\n  Interpretation:")
    print(f"  Extra stop hits = stops triggered by intraday low but NOT by daily close.")
    print(f"  If extra hits > 5% of picks, the daily backtest may be overoptimistic.")
    print(f"  If extra hits are small, the daily close simulation is a reliable proxy.")
    print()


def main():
    print(f"\n{BOLD}Intraday Validation Diagnostic{RESET}")
    print("Comparing intraday vs daily close trailing stop results\n")

    # Determine intraday date range to filter daily data accordingly
    sample_intra = load(INTRADAY_FILES[0][1])
    if sample_intra is None:
        print("Intraday files not found. Run backtest_intraday_validation.py first.")
        return

    iv_start, iv_end = get_date_range(sample_intra)
    print(f"  Intraday date range: {iv_start} to {iv_end}")
    print(f"  Filtering daily data to same date range for fair comparison.\n")

    labels      = []
    daily_stats = []
    intra_stats = []
    daily_rows_list = []
    intra_rows_list = []

    for (ilabel, ifile), (dlabel, dfile) in zip(INTRADAY_FILES, DAILY_FILES):
        irows = load(ifile)
        drows = load(dfile)

        if irows is None:
            print(f"  {ilabel}: intraday file not found, skipping.")
            continue
        if drows is None:
            print(f"  {dlabel}: daily file not found, skipping.")
            continue

        # Filter daily rows to same date range as intraday
        drows_filtered = filter_to_dates(drows, iv_start, iv_end)
        if not drows_filtered:
            print(f"  {dlabel}: no daily picks in intraday date range, skipping.")
            continue

        ds = stats(drows_filtered, stop_reason_key="stop_hit")
        is_ = stats(irows, stop_reason_key="stop_hit_intraday")

        if not ds or not is_:
            print(f"  {ilabel}: could not compute stats, skipping.")
            continue

        labels.append(ilabel)
        daily_stats.append(ds)
        intra_stats.append(is_)
        daily_rows_list.append(drows_filtered)
        intra_rows_list.append(irows)

        print_comparison(ilabel, ds, is_, drows_filtered, irows)

    if labels:
        print_summary(labels, daily_stats, intra_stats)


if __name__ == "__main__":
    main()
