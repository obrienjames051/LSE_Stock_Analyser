#!/usr/bin/env python3
"""
dynamic_stop_diagnostic.py
---------------------------
Compares dynamic trailing stop variants against pure Variant C baseline.

Run from LSE_Stock_Analyser/ with:
  python3 dynamic_stop_diagnostic.py
"""

import csv
import os
from collections import defaultdict

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"

VARIANTS = [
    ("C-base        (fixed stop)",          "lse_ds_base.csv"),
    ("C-trail-cons  (trigger 1.5x ATR)",    "lse_ds_cons.csv"),
    ("C-trail-mod   (trigger 1.0x ATR)",    "lse_ds_mod.csv"),
    ("C-trail-agg   (trigger 0.5x ATR)",    "lse_ds_agg.csv"),
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


def stats(rows):
    returns, allocs = [], []
    for r in rows:
        try:
            ret = float(r["return_pct"])
            returns.append(ret)
            allocs.append((ret, float(r.get("allocation_pct", 0) or 0)))
        except (ValueError, KeyError):
            continue

    if not returns:
        return {}

    n       = len(returns)
    pos     = [r for r in returns if r > 0]
    neg     = [r for r in returns if r <= 0]
    by_week = defaultdict(list)
    for r in rows:
        try:
            by_week[r["run_date"][:10]].append(float(r["return_pct"]))
        except (ValueError, KeyError):
            pass

    week_avgs   = {w: sum(v)/len(v) for w, v in by_week.items()}
    total_alloc = sum(a for _, a in allocs)
    kelly       = (sum(ret*a for ret, a in allocs) / total_alloc
                   if total_alloc > 0 else sum(returns)/n)

    return {
        "n":             n,
        "avg":           sum(returns) / n,
        "kelly":         kelly,
        "dir_acc":       len(pos) / n * 100,
        "avg_winner":    sum(pos) / len(pos) if pos else 0,
        "avg_loser":     sum(neg) / len(neg) if neg else 0,
        "best_week":     max(week_avgs.values()) if week_avgs else 0,
        "worst_week":    min(week_avgs.values()) if week_avgs else 0,
        "pct_neg_weeks": sum(1 for v in week_avgs.values() if v < 0) / len(week_avgs) * 100,
        "_returns":      returns,
    }


def col(diff, higher_better=True):
    if abs(diff) < 0.005:
        return "  ~  "
    good = (diff > 0) == higher_better
    c    = GREEN if good else RED
    sign = "+" if diff > 0 else ""
    arr  = "▲" if diff > 0 else "▼"
    return f"  {c}{arr}{sign}{diff:.2f}{RESET}"


def print_variant(label, base_s, var_s, rows):
    n       = var_s["n"]
    stops   = sum(1 for r in rows if r.get("exit_reason") == "stop_hit")
    fridays = sum(1 for r in rows if r.get("exit_reason") == "friday_close")
    moved   = sum(1 for r in rows if str(r.get("stop_moved","")).lower() == "true")

    print(f"\n{'='*62}")
    print(f"{BOLD}  {label}  ({n} picks){RESET}")
    print(f"{'='*62}")

    metrics = [
        ("Avg return (simple)",    base_s["avg"],           var_s["avg"],           True),
        ("Avg return (Kelly-wtd)", base_s["kelly"],         var_s["kelly"],         True),
        ("Directional accuracy",   base_s["dir_acc"],       var_s["dir_acc"],       True),
        ("Avg winner return",      base_s["avg_winner"],    var_s["avg_winner"],    True),
        ("Avg loser return",       base_s["avg_loser"],     var_s["avg_loser"],     False),
        ("Best week",              base_s["best_week"],     var_s["best_week"],     True),
        ("Worst week",             base_s["worst_week"],    var_s["worst_week"],    False),
        ("% weeks negative",       base_s["pct_neg_weeks"], var_s["pct_neg_weeks"], False),
    ]
    for name, bv, mv, hb in metrics:
        print(f"  {name:<28}  base:{bv:>+7.2f}%  this:{mv:>+7.2f}%{col(mv-bv, hb)}")

    print(f"\n  Stop activity:")
    print(f"    Stops moved to trailing: {moved} ({moved/n*100:.1f}%)")
    print(f"    Stop hits:               {stops} ({stops/n*100:.1f}%)")
    print(f"    Friday closes:           {fridays} ({fridays/n*100:.1f}%)")

    # Exit day distribution
    exit_days = defaultdict(int)
    for r in rows:
        try:
            exit_days[int(r.get("exit_day", 5))] += 1
        except ValueError:
            pass
    max_c = max(exit_days.values()) if exit_days else 1
    print(f"\n  Exit day distribution:")
    for day in sorted(exit_days):
        c   = exit_days[day]
        bar = "█" * int(c / max_c * 20)
        print(f"    Day {day}:  {c:>3}  {bar}")

    # Return distribution vs base
    b_ret = base_s["_returns"]
    m_ret = var_s["_returns"]
    print(f"\n  Return distribution:")
    for blabel, fn in BUCKETS:
        bc   = sum(1 for r in b_ret if fn(r))
        mc   = sum(1 for r in m_ret if fn(r))
        diff = mc - bc
        c    = GREEN if diff > 0 else (RED if diff < 0 else "")
        sign = "+" if diff >= 0 else ""
        print(f"    {blabel:>14}  base:{bc:>4}  this:{mc:>4}  {c}({sign}{diff}){RESET}")


def print_summary_table(all_labels, all_stats):
    print(f"\n{'='*62}")
    print(f"{BOLD}  SUMMARY TABLE{RESET}")
    print(f"{'='*62}")

    short = ["Base", "Cons", "Mod", "Agg"]
    print(f"  {'Metric':<28}  {'Base':>8}  {'Cons':>8}  {'Mod':>8}  {'Agg':>8}")
    print(f"  {'-'*28}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")

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

    short = ["C-base", "C-trail-cons", "C-trail-mod", "C-trail-agg"]
    avgs  = [s["avg"] for s in all_stats]
    base_avg = avgs[0]

    best_idx = avgs.index(max(avgs))
    print(f"  Best avg return: {short[best_idx]}  ({avgs[best_idx]:+.2f}%)")
    print()

    for i in range(1, len(all_stats)):
        diff = avgs[i] - base_avg
        c    = GREEN if diff > 0 else RED
        sign = "+" if diff >= 0 else ""
        arr  = "▲" if diff >= 0 else "▼"
        print(f"  {short[i]:<16}  {c}{arr}{sign}{diff:.2f}pp vs C-base{RESET}  "
              f"| worst week: {all_stats[i]['worst_week']:+.2f}%  "
              f"| neg weeks: {all_stats[i]['pct_neg_weeks']:.1f}%")

    print()
    print("  Key question: does trailing the stop capture mid-week gains")
    print("  without cutting off stocks that dip before recovering?")
    print()
    print("  If trailing variants show:")
    print("    avg return UP + avg loser better  --> trailing adds value")
    print("    avg return DOWN + avg winner worse --> trailing exits too early")
    print()


def main():
    print(f"\n{BOLD}Dynamic Stop Diagnostic{RESET}")
    print("Comparing trailing stop variants against pure Variant C\n")

    all_labels = []
    all_stats  = []
    all_rows   = []

    base_rows = None
    base_s    = None

    for label, filepath in VARIANTS:
        rows = load(filepath)
        if rows is None:
            print(f"  {label}: file not found -- run backtest_dynamic_stop.py first")
            continue
        s = stats(rows)
        if not s:
            print(f"  {label}: could not compute stats")
            continue
        all_labels.append(label)
        all_stats.append(s)
        all_rows.append(rows)
        if base_s is None:
            base_s    = s
            base_rows = rows

    if base_s is None:
        print("Could not load base variant. Run backtest_dynamic_stop.py first.")
        return

    # Print each variant compared to base
    for i, (label, s, rows) in enumerate(zip(all_labels, all_stats, all_rows)):
        print_variant(label, base_s, s, rows)

    if len(all_stats) == 4:
        print_summary_table(all_labels, all_stats)
        print_verdict(all_labels, all_stats)


if __name__ == "__main__":
    main()
