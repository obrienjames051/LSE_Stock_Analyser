#!/usr/bin/env python3
"""
stop_width_diagnostic.py
-------------------------
Compares the four stop width variants, showing daily close vs intraday
results side by side. The key output is how many extra intraday stop hits
remain as the stop widens, and what happens to the return at each level.

Run from LSE_Stock_Analyser/ with:
  python3 stop_width_diagnostic.py
"""

import csv
import os
from collections import defaultdict

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"

MULTIPLIERS = [1.0, 1.5, 2.0, 2.5]

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
    dates = [r["run_date"][:10] for r in rows if "run_date" in r]
    return (min(dates), max(dates)) if dates else (None, None)


def filter_to_dates(rows, start, end):
    return [r for r in rows if start <= r.get("run_date", "")[:10] <= end]


def compute_stats(rows, stop_key="stop_hit"):
    returns = []
    for r in rows:
        try:
            returns.append(float(r["return_pct"]))
        except (ValueError, KeyError):
            continue
    if not returns:
        return {}

    n       = len(returns)
    pos     = [r for r in returns if r > 0]
    neg     = [r for r in returns if r <= 0]
    stops   = sum(1 for r in rows if stop_key in r.get("exit_reason", ""))
    fridays = sum(1 for r in rows if r.get("exit_reason") == "friday_close")

    by_week = defaultdict(list)
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
        "best_week":   max(week_avgs.values()) if week_avgs else 0,
        "stops":       stops,
        "stop_pct":    stops / n * 100,
        "fridays":     fridays,
        "_returns":    returns,
    }


def col_diff(diff, higher_better=True):
    if abs(diff) < 0.01:
        return f"  ~  "
    good = (diff > 0) == higher_better
    c    = GREEN if good else RED
    sign = "+" if diff > 0 else ""
    arr  = "▲" if diff > 0 else "▼"
    return f"  {c}{arr}{sign}{diff:.2f}{RESET}"


def print_multiplier(mult, daily_s, intra_s):
    label    = f"{mult:.1f}x ATR"
    extra    = intra_s["stops"] - daily_s["stops"]
    extra_c  = RED if extra > 4 else (GREEN if extra <= 1 else "")
    n_d      = daily_s["n"]
    n_i      = intra_s["n"]

    print(f"\n{'='*66}")
    print(f"{BOLD}  Stop at {label}  "
          f"(daily: {n_d} picks  |  intraday: {n_i} picks){RESET}")
    print(f"{'='*66}")

    print(f"  {'Metric':<24}  {'Daily close':>12}  {'Intraday':>12}  {'Diff':>10}")
    print(f"  {'-'*24}  {'-'*12}  {'-'*12}  {'-'*10}")

    metrics = [
        ("Avg return",       "avg",        True),
        ("Directional acc.", "dir_acc",    True),
        ("Avg winner",       "avg_winner", True),
        ("Avg loser",        "avg_loser",  False),
        ("Worst week",       "worst_week", False),
    ]
    for name, key, hb in metrics:
        dv = daily_s[key]
        iv = intra_s[key]
        print(f"  {name:<24}  {dv:>+11.2f}%  {iv:>+11.2f}%{col_diff(iv-dv, hb)}")

    print(f"\n  Stop activity:")
    print(f"  {'':24}  {'Daily close':>12}  {'Intraday':>12}  {'Extra':>10}")
    print(f"  {'-'*24}  {'-'*12}  {'-'*12}  {'-'*10}")
    print(f"  {'Stop hits':<24}  {daily_s['stops']:>11}   {intra_s['stops']:>11}   "
          f"{extra_c}{'+' if extra >= 0 else ''}{extra}{RESET}")
    print(f"  {'Stop hit rate':<24}  {daily_s['stop_pct']:>10.1f}%  "
          f"{intra_s['stop_pct']:>10.1f}%")
    print(f"  {'Friday closes':<24}  {daily_s['fridays']:>11}   {intra_s['fridays']:>11}")

    # Return distributions
    d_ret = daily_s["_returns"]
    i_ret = intra_s["_returns"]
    print(f"\n  Return distribution (daily vs intraday):")
    for blabel, fn in BUCKETS:
        dc   = sum(1 for r in d_ret if fn(r))
        ic   = sum(1 for r in i_ret if fn(r))
        diff = ic - dc
        c    = RED if diff < -2 else (GREEN if diff > 2 else "")
        sign = "+" if diff >= 0 else ""
        print(f"    {blabel:>14}  daily:{dc:>4}  intra:{ic:>4}  {c}({sign}{diff}){RESET}")


def print_main_comparison(all_mults, all_daily, all_intra, all_daily_52):
    """
    The main summary table: shows daily (52-week) return vs intraday extra
    stop hits for each multiplier, so the trade-off is clear.
    """
    print(f"\n{'='*66}")
    print(f"{BOLD}  MAIN COMPARISON TABLE{RESET}")
    print(f"{'='*66}")
    print()
    print(f"  52-week daily results (full backtest):")
    print(f"  {'Stop mult':<12}  {'Avg return':>11}  {'Stop hits':>10}  "
          f"{'Stop hit %':>11}  {'Worst week':>11}")
    print(f"  {'-'*12}  {'-'*11}  {'-'*10}  {'-'*11}  {'-'*11}")

    avgs_52 = [s["avg"] for s in all_daily_52]
    best_avg = max(avgs_52)
    for mult, s in zip(all_mults, all_daily_52):
        is_best = abs(s["avg"] - best_avg) < 0.005
        c       = GREEN if is_best else ""
        print(
            f"  {mult:.1f}x ATR    {c}{s['avg']:>+10.2f}%{RESET}  "
            f"{s['stops']:>10}   {s['stop_pct']:>10.1f}%  "
            f"{s['worst_week']:>+10.2f}%"
        )

    print()
    print(f"  Intraday validation (last 8 weeks -- extra stop hits vs daily):")
    print(f"  {'Stop mult':<12}  {'Daily avg':>10}  {'Intra avg':>10}  "
          f"{'Extra stops':>12}  {'Extra %':>8}  {'Converging?':>12}")
    print(f"  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*12}  {'-'*8}  {'-'*12}")

    for mult, ds, is_ in zip(all_mults, all_daily, all_intra):
        extra     = is_["stops"] - ds["stops"]
        extra_pct = extra / ds["n"] * 100 if ds["n"] > 0 else 0
        converge  = extra_pct <= 5.0
        c_extra   = GREEN if converge else RED
        c_conv    = GREEN if converge else RED
        conv_str  = "YES ✓" if converge else "NO  ✗"
        print(
            f"  {mult:.1f}x ATR    {ds['avg']:>+9.2f}%  {is_['avg']:>+9.2f}%  "
            f"{c_extra}{extra:>+11}   {extra_pct:>7.1f}%{RESET}  "
            f"{c_conv}{conv_str:>12}{RESET}"
        )

    print()
    print(f"  'Converging?' = extra intraday stop hits <= 5% of picks.")
    print(f"  At this level the daily close simulation is a reliable proxy")
    print(f"  for real-world trading with hard stop orders.")


def print_verdict(all_mults, all_daily_52, all_daily, all_intra):
    print(f"\n{'='*66}")
    print(f"{BOLD}  VERDICT{RESET}")
    print(f"{'='*66}")

    # Find the widest stop that still converges (extra hits <= 5%)
    # AND has the best daily return among converging options
    converging = []
    for mult, ds, is_, ds52 in zip(all_mults, all_daily, all_intra, all_daily_52):
        extra_pct = (is_["stops"] - ds["stops"]) / ds["n"] * 100 if ds["n"] > 0 else 100
        if extra_pct <= 5.0:
            converging.append((mult, ds52["avg"], ds52["worst_week"], extra_pct))

    non_converging = []
    for mult, ds, is_, ds52 in zip(all_mults, all_daily, all_intra, all_daily_52):
        extra_pct = (is_["stops"] - ds["stops"]) / ds["n"] * 100 if ds["n"] > 0 else 100
        if extra_pct > 5.0:
            non_converging.append((mult, ds52["avg"], ds52["worst_week"], extra_pct))

    if converging:
        # Among converging options, pick the one with best avg return
        best = max(converging, key=lambda x: x[1])
        print(f"  {GREEN}Recommended stop multiplier: {best[0]:.1f}x ATR{RESET}")
        print(f"  Daily avg return:   {best[1]:+.2f}%")
        print(f"  Worst week:         {best[2]:+.2f}%")
        print(f"  Extra intraday hits: {best[3]:.1f}% of picks")
        print(f"  --> Daily close simulation is reliable at this level.")
        print(f"  --> Hard stop orders at this price should perform as backtested.")
    else:
        print(f"  {RED}No multiplier converges to <= 5% extra intraday hits.{RESET}")
        print(f"  Consider using mental stops (daily close checks) rather than")
        print(f"  hard stop orders placed with the broker.")

    if non_converging:
        print(f"\n  Multipliers with too many intraday hits (unreliable for hard stops):")
        for mult, avg, worst, extra_pct in non_converging:
            print(f"  {RED}  {mult:.1f}x ATR -- {extra_pct:.1f}% extra intraday stops{RESET}")

    print()


def main():
    print(f"\n{BOLD}Stop Width Diagnostic{RESET}")
    print("Comparing stop distances: daily close vs intraday\n")

    # Load intraday files to determine date range
    sample_intra = load(
        f"lse_sw_{f'{MULTIPLIERS[0]:.1f}x'.replace('.','p')}_intra.csv"
    )
    if sample_intra is None:
        print("Intraday files not found. Run backtest_stop_width.py first.")
        return

    iv_start, iv_end = get_date_range(sample_intra)
    print(f"  Intraday date range: {iv_start} to {iv_end}")
    print(f"  Daily (intraday window): filtered to same {iv_start} - {iv_end}")
    print(f"  Daily (52-week):         full backtest period\n")

    all_mults     = []
    all_daily_52  = []  # full 52-week daily results
    all_daily     = []  # daily filtered to intraday date range
    all_intra     = []  # intraday results

    for mult in MULTIPLIERS:
        label = f"{mult:.1f}x".replace(".", "p")
        daily_file = f"lse_sw_{label}_daily.csv"
        intra_file = f"lse_sw_{label}_intra.csv"

        daily_rows = load(daily_file)
        intra_rows = load(intra_file)

        if daily_rows is None:
            print(f"  {mult:.1f}x: daily file not found, skipping.")
            continue
        if intra_rows is None:
            print(f"  {mult:.1f}x: intraday file not found, skipping.")
            continue

        daily_rows_filtered = filter_to_dates(daily_rows, iv_start, iv_end)

        ds_52      = compute_stats(daily_rows, "stop_hit")
        ds_filtered = compute_stats(daily_rows_filtered, "stop_hit")
        is_        = compute_stats(intra_rows, "stop_hit_intraday")

        if not ds_52 or not ds_filtered or not is_:
            print(f"  {mult:.1f}x: could not compute stats, skipping.")
            continue

        all_mults.append(mult)
        all_daily_52.append(ds_52)
        all_daily.append(ds_filtered)
        all_intra.append(is_)

        print_multiplier(mult, ds_filtered, is_)

    if all_mults:
        print_main_comparison(all_mults, all_daily, all_intra, all_daily_52)
        print_verdict(all_mults, all_daily_52, all_daily, all_intra)


if __name__ == "__main__":
    main()
