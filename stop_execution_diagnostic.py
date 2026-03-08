#!/usr/bin/env python3
"""
stop_execution_diagnostic.py
------------------------------
Compares the three stop execution methods against each other.

Run from LSE_Stock_Analyser/ with:
  python3 stop_execution_diagnostic.py
"""

import csv
import os
from collections import defaultdict

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"

VARIANTS = [
    ("Method 1  (sell at triggering close)",      "lse_se_method1.csv"),
    ("Method 2  (sell at next open, no matter what)", "lse_se_method2.csv"),
    ("Method 3  (sell at next open, hold if gap up)", "lse_se_method3.csv"),
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


def compute_stats(rows):
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

    by_week = defaultdict(list)
    for r in rows:
        try:
            by_week[r["run_date"][:10]].append(float(r["return_pct"]))
        except (ValueError, KeyError):
            pass
    week_avgs = {w: sum(v)/len(v) for w, v in by_week.items()}

    # Exit breakdowns
    stop_exits   = sum(1 for r in rows if "stop" in r.get("exit_reason", ""))
    friday_exits = sum(1 for r in rows if r.get("exit_reason") == "friday_close")

    # Gap-up recoveries: triggered but held to Friday (method 3)
    recoveries = sum(1 for r in rows
                     if r.get("exit_reason") == "friday_close"
                     and r.get("trigger_day", "") not in ("", None))

    # Of those recoveries, how many ended profitably?
    recovery_returns = []
    for r in rows:
        if (r.get("exit_reason") == "friday_close"
                and r.get("trigger_day", "") not in ("", None)):
            try:
                recovery_returns.append(float(r["return_pct"]))
            except (ValueError, KeyError):
                pass

    # Gap analysis for method 2/3: compare trigger close vs exit price
    gap_ups   = 0
    gap_downs = 0
    gap_neutral = 0
    for r in rows:
        if "stop_next_open" in r.get("exit_reason", ""):
            try:
                trigger = float(r["trigger_close"])
                exit_p  = float(r["exit_price"])
                stop_p  = float(r["stop_price"])
                if exit_p > trigger:
                    gap_ups += 1
                elif exit_p < trigger:
                    gap_downs += 1
                else:
                    gap_neutral += 1
            except (ValueError, KeyError):
                pass

    allocs = []
    for r in rows:
        try:
            allocs.append((float(r["return_pct"]), float(r.get("allocation_pct", 0) or 0)))
        except (ValueError, KeyError):
            pass
    total_alloc = sum(a for _, a in allocs)
    kelly = (sum(ret*a for ret, a in allocs) / total_alloc
             if total_alloc > 0 else sum(returns)/n)

    return {
        "n":                  n,
        "avg":                sum(returns) / n,
        "kelly":              kelly,
        "dir_acc":            len(pos) / n * 100,
        "avg_winner":         sum(pos) / len(pos) if pos else 0,
        "avg_loser":          sum(neg) / len(neg) if neg else 0,
        "best_week":          max(week_avgs.values()) if week_avgs else 0,
        "worst_week":         min(week_avgs.values()) if week_avgs else 0,
        "pct_neg_weeks":      sum(1 for v in week_avgs.values() if v < 0) / len(week_avgs) * 100,
        "stop_exits":         stop_exits,
        "friday_exits":       friday_exits,
        "recoveries":         recoveries,
        "recovery_returns":   recovery_returns,
        "gap_ups":            gap_ups,
        "gap_downs":          gap_downs,
        "gap_neutral":        gap_neutral,
        "_returns":           returns,
    }


def col(diff, higher_better=True):
    if abs(diff) < 0.005:
        return "  ~  "
    good = (diff > 0) == higher_better
    c    = GREEN if good else RED
    sign = "+" if diff > 0 else ""
    arr  = "▲" if diff > 0 else "▼"
    return f"  {c}{arr}{sign}{diff:.2f}{RESET}"


def print_variant(label, base_s, s, rows):
    n = s["n"]
    print(f"\n{'='*62}")
    print(f"{BOLD}  {label}  ({n} picks){RESET}")
    print(f"{'='*62}")

    metrics = [
        ("Avg return (simple)",    base_s["avg"],           s["avg"],           True),
        ("Avg return (Kelly-wtd)", base_s["kelly"],         s["kelly"],         True),
        ("Directional accuracy",   base_s["dir_acc"],       s["dir_acc"],       True),
        ("Avg winner return",      base_s["avg_winner"],    s["avg_winner"],    True),
        ("Avg loser return",       base_s["avg_loser"],     s["avg_loser"],     False),
        ("Best week",              base_s["best_week"],     s["best_week"],     True),
        ("Worst week",             base_s["worst_week"],    s["worst_week"],    False),
        ("% weeks negative",       base_s["pct_neg_weeks"], s["pct_neg_weeks"], False),
    ]
    for name, bv, mv, hb in metrics:
        print(f"  {name:<28}  base:{bv:>+7.2f}%  this:{mv:>+7.2f}%{col(mv-bv, hb)}")

    # Exit breakdown
    print(f"\n  Exit breakdown:")
    print(f"    Stop exits:    {s['stop_exits']:>3}  ({s['stop_exits']/n*100:.1f}%)")
    print(f"    Friday closes: {s['friday_exits']:>3}  ({s['friday_exits']/n*100:.1f}%)")

    # Gap analysis (relevant for methods 2 & 3)
    total_gaps = s["gap_ups"] + s["gap_downs"] + s["gap_neutral"]
    if total_gaps > 0:
        print(f"\n  Of {total_gaps} next-open exits:")
        print(f"    Opened above trigger close (gap up):    {s['gap_ups']:>3}  "
              f"({s['gap_ups']/total_gaps*100:.1f}%)")
        print(f"    Opened below trigger close (gap down):  {s['gap_downs']:>3}  "
              f"({s['gap_downs']/total_gaps*100:.1f}%)")
        print(f"    Opened at trigger close (no gap):       {s['gap_neutral']:>3}  "
              f"({s['gap_neutral']/total_gaps*100:.1f}%)")

    # Gap-up recoveries held (method 3 only)
    if s["recoveries"] > 0:
        rec_ret = s["recovery_returns"]
        avg_rec = sum(rec_ret) / len(rec_ret) if rec_ret else 0
        pos_rec = sum(1 for r in rec_ret if r > 0)
        print(f"\n  Gap-up recoveries held to Friday: {s['recoveries']}")
        print(f"    Avg return of recovered picks:  {avg_rec:+.2f}%")
        print(f"    Ended positively: {pos_rec} / {len(rec_ret)} "
              f"({pos_rec/len(rec_ret)*100:.1f}%)")

    # Exit day distribution
    exit_days = {}
    for r in rows:
        try:
            d = int(r.get("exit_day", 5))
            exit_days[d] = exit_days.get(d, 0) + 1
        except (ValueError, TypeError):
            pass
    max_c = max(exit_days.values()) if exit_days else 1
    print(f"\n  Exit day distribution:")
    for day in sorted(exit_days):
        c   = exit_days[day]
        bar = "█" * int(c / max_c * 20)
        print(f"    Day {day}:  {c:>3}  {bar}")

    # Return distribution
    b_ret = base_s["_returns"]
    m_ret = s["_returns"]
    print(f"\n  Return distribution:")
    for blabel, fn in BUCKETS:
        bc   = sum(1 for r in b_ret if fn(r))
        mc   = sum(1 for r in m_ret if fn(r))
        diff = mc - bc
        c    = GREEN if diff > 0 else (RED if diff < 0 else "")
        sign = "+" if diff >= 0 else ""
        print(f"    {blabel:>14}  base:{bc:>4}  this:{mc:>4}  {c}({sign}{diff}){RESET}")


def print_summary_table(all_labels, all_stats):
    short = ["M1", "M2", "M3"]
    print(f"\n{'='*62}")
    print(f"{BOLD}  SIDE-BY-SIDE COMPARISON{RESET}")
    print(f"{'='*62}")
    print(f"  {'Metric':<28}  {'M1':>8}  {'M2':>8}  {'M3':>8}")
    print(f"  {'-'*28}  {'-'*8}  {'-'*8}  {'-'*8}")

    metrics = [
        ("Avg return",    "avg",           True),
        ("Kelly return",  "kelly",         True),
        ("Dir. accuracy", "dir_acc",       True),
        ("Avg winner",    "avg_winner",    True),
        ("Avg loser",     "avg_loser",     False),
        ("Best week",     "best_week",     True),
        ("Worst week",    "worst_week",    False),
        ("% neg weeks",   "pct_neg_weeks", False),
    ]
    for metric_label, key, higher_better in metrics:
        vals = [s[key] for s in all_stats]
        best = max(vals) if higher_better else min(vals)
        row  = f"  {metric_label:<28}"
        for v in vals:
            is_best = abs(v - best) < 0.005
            c       = GREEN if is_best else ""
            row    += f"  {c}{v:>+7.2f}%{RESET if c else ' '}"
        print(row)


def print_verdict(all_labels, all_stats):
    print(f"\n{'='*62}")
    print(f"{BOLD}  VERDICT{RESET}")
    print(f"{'='*62}")

    avgs = [s["avg"] for s in all_stats]
    best_idx = avgs.index(max(avgs))
    short = ["Method 1", "Method 2", "Method 3"]

    print(f"  Best avg return: {short[best_idx]}  ({avgs[best_idx]:+.2f}%)")
    print()

    m1, m2, m3 = avgs
    print(f"  Method 1 vs Method 2 (gap risk):      {col(m2-m1)}")
    print(f"  Method 1 vs Method 3 (gap recovery):  {col(m3-m1)}")
    print(f"  Method 2 vs Method 3 (recovery value):{col(m3-m2)}")
    print()

    # Practical recommendation
    print("  Practical considerations:")
    print("  Method 1: requires attention at 4:15pm every day")
    print("  Method 2: check each evening, place order before open")
    print("  Method 3: check each evening, cancel order if gap up")
    print()

    if best_idx == 0:
        print(f"  {GREEN}Method 1 wins -- the extra effort of a 4:15pm sell is{RESET}")
        print(f"  {GREEN}worth it vs waiting for next morning's open.{RESET}")
    elif best_idx == 1:
        print(f"  {GREEN}Method 2 wins -- simple next-open sell outperforms{RESET}")
        print(f"  {GREEN}both same-day and conditional approaches.{RESET}")
    else:
        print(f"  {GREEN}Method 3 wins -- holding gap-up recoveries adds value.{RESET}")
        print(f"  {GREEN}Check each evening and cancel the sell order if the{RESET}")
        print(f"  {GREEN}stock gaps up over the stop price at open.{RESET}")
    print()


def main():
    print(f"\n{BOLD}Stop Execution Method Diagnostic{RESET}")
    print("Comparing three ways to execute a mental stop loss\n")

    all_labels = []
    all_stats  = []
    all_rows   = []
    base_s     = None

    for label, filepath in VARIANTS:
        rows = load(filepath)
        if rows is None:
            print(f"  {label}: file not found -- run backtest_stop_execution.py first")
            continue
        s = compute_stats(rows)
        if not s:
            print(f"  {label}: could not compute stats")
            continue
        all_labels.append(label)
        all_stats.append(s)
        all_rows.append(rows)
        if base_s is None:
            base_s = s

    if not base_s:
        return

    for label, s, rows in zip(all_labels, all_stats, all_rows):
        print_variant(label, base_s, s, rows)

    if len(all_stats) == 3:
        print_summary_table(all_labels, all_stats)
        print_verdict(all_labels, all_stats)


if __name__ == "__main__":
    main()
