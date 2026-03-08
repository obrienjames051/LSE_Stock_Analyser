#!/usr/bin/env python3
"""
dynamic_limit_diagnostic.py
-----------------------------
Compares all 6 dynamic limit variants against the Phase 1 static baseline.

Run from LSE_Stock_Analyser/ with:
  python3 dynamic_limit_diagnostic.py
"""

import csv
import os
from collections import defaultdict

BASELINE_FILE = "lse_backtest_technical.csv"

VARIANTS = [
    ("Trigger 2.5x  |  Limit only",            "lse_dl_2p5x_limit.csv"),
    ("Trigger 2.5x  |  Limit + breakeven stop", "lse_dl_2p5x_limit_stop.csv"),
    ("Trigger 3.0x  |  Limit only",             "lse_dl_3p0x_limit.csv"),
    ("Trigger 3.0x  |  Limit + breakeven stop", "lse_dl_3p0x_limit_stop.csv"),
    ("Trigger 4.0x  |  Limit only",             "lse_dl_4p0x_limit.csv"),
    ("Trigger 4.0x  |  Limit + breakeven stop", "lse_dl_4p0x_limit_stop.csv"),
]

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"

BUCKETS = [
    ("< -3%",      lambda r: r < -3),
    ("-3% to -2%", lambda r: -3 <= r < -2),
    ("-2% to -1%", lambda r: -2 <= r < -1),
    ("-1% to 0%",  lambda r: -1 <= r < 0),
    ("0% to +1%",  lambda r: 0 <= r < 1),
    ("+1% to +2%", lambda r: 1 <= r < 2),
    ("+2% to +3%", lambda r: 2 <= r < 3),
    ("> +3%",      lambda r: r >= 3),
]


def load_baseline(filepath):
    if not os.path.isfile(filepath):
        print(f"BASELINE NOT FOUND: {filepath}")
        return []
    rows = []
    with open(filepath, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("outcome_hit", "").strip() in ("YES", "NO"):
                rows.append(row)
    return rows


def load_variant(filepath):
    if not os.path.isfile(filepath):
        return None
    with open(filepath, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def compute_stats(rows, ret_field, alloc_field="allocation_pct"):
    returns, allocs = [], []
    for r in rows:
        try:
            ret = float(r[ret_field])
            returns.append(ret)
            allocs.append((ret, float(r.get(alloc_field, 0) or 0)))
        except (ValueError, KeyError):
            continue

    if not returns:
        return {}

    n         = len(returns)
    pos       = [r for r in returns if r > 0]
    neg       = [r for r in returns if r <= 0]
    by_week   = defaultdict(list)
    for r in rows:
        try:
            by_week[r["run_date"][:10]].append(float(r[ret_field]))
        except (ValueError, KeyError):
            pass

    week_avgs    = {w: sum(v)/len(v) for w, v in by_week.items()}
    total_alloc  = sum(a for _, a in allocs)
    kelly        = sum(ret*a for ret, a in allocs) / total_alloc if total_alloc > 0 else sum(returns)/n

    return {
        "n":             n,
        "avg":           sum(returns) / n,
        "kelly":         kelly,
        "dir_acc":       len(pos) / n * 100,
        "avg_winner":    sum(pos) / len(pos) if pos else 0,
        "avg_loser":     sum(neg) / len(neg) if neg else 0,
        "worst_week":    min(week_avgs.values()) if week_avgs else 0,
        "best_week":     max(week_avgs.values()) if week_avgs else 0,
        "pct_neg_weeks": sum(1 for v in week_avgs.values() if v < 0) / len(week_avgs) * 100 if week_avgs else 0,
        "_returns":      returns,
    }


def delta(mval, bval, higher_better=True):
    diff = mval - bval
    if abs(diff) < 0.005:
        return "  ~"
    good = (diff > 0) == higher_better
    col  = GREEN if good else RED
    sign = "+" if diff > 0 else ""
    arr  = "▲" if diff > 0 else "▼"
    return f"  {col}{arr}{sign}{diff:.2f}{RESET}"


def print_variant(label, baseline, managed, rows):
    n = len(rows)
    print(f"\n{'='*62}")
    print(f"{BOLD}  {label}  ({n} picks){RESET}")
    print(f"{'='*62}")

    metrics = [
        ("Avg return (simple)",    baseline["avg"],           managed["avg"],           True),
        ("Avg return (Kelly-wtd)", baseline["kelly"],         managed["kelly"],         True),
        ("Directional accuracy",   baseline["dir_acc"],       managed["dir_acc"],       True),
        ("Avg winner return",      baseline["avg_winner"],    managed["avg_winner"],    True),
        ("Avg loser return",       baseline["avg_loser"],     managed["avg_loser"],     False),
        ("Worst week",             baseline["worst_week"],    managed["worst_week"],    False),
        ("Best week",              baseline["best_week"],     managed["best_week"],     True),
        ("% weeks negative",       baseline["pct_neg_weeks"], managed["pct_neg_weeks"], False),
    ]
    for name, bv, mv, hb in metrics:
        print(f"  {name:<28}  base: {bv:>7.2f}%  managed: {mv:>7.2f}%{delta(mv, bv, hb)}")

    # Activity stats
    raised      = sum(1 for r in rows if str(r.get("limit_raised","")).lower() == "true")
    stops_moved = sum(1 for r in rows if str(r.get("stop_moved","")).lower() == "true")
    lh          = sum(1 for r in rows if r.get("exit_reason") == "limit_hit")
    sh          = sum(1 for r in rows if r.get("exit_reason") == "stop_hit")
    we          = sum(1 for r in rows if r.get("exit_reason") == "week_end")

    uplifts = []
    for r in rows:
        try:
            if str(r.get("limit_raised","")).lower() == "true":
                uplift = (float(r["final_limit"]) - float(r["original_target"])) \
                         / float(r["original_target"]) * 100
                uplifts.append(uplift)
        except (ValueError, KeyError):
            pass

    avg_uplift = sum(uplifts) / len(uplifts) if uplifts else 0

    print(f"\n  Exceptional movers:  {raised} picks ({raised/n*100:.1f}%)  "
          f"|  avg limit uplift: {avg_uplift:+.1f}%  "
          f"|  stops moved to breakeven: {stops_moved}")
    print(f"  Exit reasons:  {lh} limit hits  |  {sh} stop hits  |  {we} held to week end")

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

    # Return distribution
    b_ret = baseline["_returns"]
    m_ret = managed["_returns"]
    print(f"\n  Return distribution:")
    for blabel, fn in BUCKETS:
        bc   = sum(1 for r in b_ret if fn(r))
        mc   = sum(1 for r in m_ret if fn(r))
        diff = mc - bc
        col  = GREEN if diff > 0 else (RED if diff < 0 else "")
        sign = "+" if diff >= 0 else ""
        print(f"    {blabel:>14}  base:{bc:>4}  managed:{mc:>4}  {col}({sign}{diff}){RESET}")


def main():
    print(f"\n{BOLD}Dynamic Limit Diagnostic  (v2 -- 6 variants){RESET}")
    print("Comparing static baseline against all 6 dynamic limit variants\n")
    print("Note: 5-day trading window (Mon open to Fri close)\n")

    baseline_rows = load_baseline(BASELINE_FILE)
    if not baseline_rows:
        print(f"Could not load baseline from {BASELINE_FILE}.")
        print("Run the main backtest first to generate baseline data.")
        return

    baseline = compute_stats(baseline_rows, "outcome_return_pct")
    print(f"Baseline ({BASELINE_FILE}): {baseline['n']} picks")
    print(f"  Avg return:        {baseline['avg']:+.2f}%")
    print(f"  Kelly-wtd return:  {baseline['kelly']:+.2f}%")
    print(f"  Directional acc:   {baseline['dir_acc']:.1f}%")
    print(f"  Worst week:        {baseline['worst_week']:+.2f}%")
    print(f"  % weeks negative:  {baseline['pct_neg_weeks']:.1f}%")

    for label, filepath in VARIANTS:
        rows = load_variant(filepath)
        if rows is None:
            print(f"\n  {label}: file not found -- run backtest_dynamic_limit.py first")
            continue
        managed = compute_stats(rows, "return_pct")
        if not managed:
            print(f"\n  {label}: could not compute stats")
            continue
        print_variant(label, baseline, managed, rows)

    print(f"\n{'='*62}")
    print(f"{BOLD}  WHAT TO LOOK FOR{RESET}")
    print(f"{'='*62}")
    print("  Ideal result:  avg return UP  |  avg winner UP  |  worst week ~same")
    print("  The > +3% bucket should grow -- exceptional movers captured better")
    print("  The < -3% bucket should be ~same -- stop never moved for most picks")
    print("  Stop + limit variant should show same or better avg return vs limit only")
    print("  If limit only beats limit+stop: stop is cutting exceptional movers short")
    print("  If limit+stop beats limit only: breakeven protection is adding value")
    print()


if __name__ == "__main__":
    main()
