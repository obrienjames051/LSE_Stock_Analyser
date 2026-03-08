#!/usr/bin/env python3
"""
exit_strategy_diagnostic.py
-----------------------------
Compares the four exit strategy variants against each other to determine
which approach produces the best real-world returns.

The four variants are:
  A -- No stops, no limits    (hold to Friday close)
  B -- No stops, limits only  (sell at limit if hit mid-week)
  C -- Stops only, no limits  (sell at stop if hit mid-week)
  D -- Stops and limits       (current programme logic)

Run from LSE_Stock_Analyser/ with:
  python3 exit_strategy_diagnostic.py
"""

import csv
import os
from collections import defaultdict

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"

VARIANTS = [
    ("A -- No stops, no limits",    "lse_exit_a.csv"),
    ("B -- No stops, limits only",  "lse_exit_b.csv"),
    ("C -- Stops only, no limits",  "lse_exit_c.csv"),
    ("D -- Stops and limits",       "lse_exit_d.csv"),
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


def load_variant(filepath):
    if not os.path.isfile(filepath):
        return None
    with open(filepath, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def compute_stats(rows):
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

    n          = len(returns)
    pos        = [r for r in returns if r > 0]
    neg        = [r for r in returns if r <= 0]
    by_week    = defaultdict(list)
    for r in rows:
        try:
            by_week[r["run_date"][:10]].append(float(r["return_pct"]))
        except (ValueError, KeyError):
            pass

    week_avgs    = {w: sum(v)/len(v) for w, v in by_week.items()}
    total_alloc  = sum(a for _, a in allocs)
    kelly        = (
        sum(ret * a for ret, a in allocs) / total_alloc
        if total_alloc > 0 else sum(returns) / n
    )
    neg_weeks    = sum(1 for v in week_avgs.values() if v < 0)

    # Weekly return distribution
    weekly_returns = list(week_avgs.values())

    return {
        "n":              n,
        "avg":            sum(returns) / n,
        "kelly":          kelly,
        "dir_acc":        len(pos) / n * 100,
        "avg_winner":     sum(pos) / len(pos) if pos else 0,
        "avg_loser":      sum(neg) / len(neg) if neg else 0,
        "best_week":      max(week_avgs.values()) if week_avgs else 0,
        "worst_week":     min(week_avgs.values()) if week_avgs else 0,
        "pct_neg_weeks":  neg_weeks / len(week_avgs) * 100 if week_avgs else 0,
        "n_weeks":        len(week_avgs),
        "_returns":       returns,
        "_weekly":        weekly_returns,
    }


def colour(val, higher_better=True):
    if val > 0:
        return GREEN if higher_better else RED
    elif val < 0:
        return RED if higher_better else GREEN
    return ""


def print_variant(label, stats, all_stats, rows):
    n = stats["n"]
    stops  = sum(1 for r in rows if r.get("exit_reason") == "stop_hit")
    limits = sum(1 for r in rows if r.get("exit_reason") == "limit_hit")
    fridays = sum(1 for r in rows if r.get("exit_reason") == "friday_close")

    print(f"\n{'='*62}")
    print(f"{BOLD}  {label}  ({n} picks){RESET}")
    print(f"{'='*62}")
    print(f"  Avg return (simple):    {stats['avg']:>+7.2f}%")
    print(f"  Avg return (Kelly-wtd): {stats['kelly']:>+7.2f}%")
    print(f"  Directional accuracy:   {stats['dir_acc']:>7.1f}%")
    print(f"  Avg winner return:      {stats['avg_winner']:>+7.2f}%")
    print(f"  Avg loser return:       {stats['avg_loser']:>+7.2f}%")
    print(f"  Best week:              {stats['best_week']:>+7.2f}%")
    print(f"  Worst week:             {stats['worst_week']:>+7.2f}%")
    print(f"  % weeks negative:       {stats['pct_neg_weeks']:>7.1f}%")
    print(f"\n  Exit breakdown:")
    print(f"    Stop hits:    {stops:>3}  ({stops/n*100:.1f}%)")
    print(f"    Limit hits:   {limits:>3}  ({limits/n*100:.1f}%)")
    print(f"    Friday close: {fridays:>3}  ({fridays/n*100:.1f}%)")

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
    print(f"\n  Return distribution:")
    for blabel, fn in BUCKETS:
        c   = sum(1 for r in stats["_returns"] if fn(r))
        bar = "█" * int(c / n * 30)
        print(f"    {blabel:>14}  {c:>3}  {bar}")


def print_comparison_table(all_stats):
    """Print a side-by-side summary table of all four variants."""
    labels = ["A", "B", "C", "D"]
    metrics = [
        ("Avg return",       "avg",           True),
        ("Kelly return",     "kelly",         True),
        ("Dir. accuracy",    "dir_acc",       True),
        ("Avg winner",       "avg_winner",    True),
        ("Avg loser",        "avg_loser",     False),
        ("Best week",        "best_week",     True),
        ("Worst week",       "worst_week",    False),
        ("% neg weeks",      "pct_neg_weeks", False),
    ]

    print(f"\n{'='*62}")
    print(f"{BOLD}  SIDE-BY-SIDE COMPARISON{RESET}")
    print(f"{'='*62}")
    print(f"  {'Metric':<22}  {'A':>8}  {'B':>8}  {'C':>8}  {'D':>8}")
    print(f"  {'-'*22}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")

    for metric_label, key, higher_better in metrics:
        vals    = [all_stats[lbl][key] for lbl in labels]
        best    = max(vals) if higher_better else min(vals)
        row     = f"  {metric_label:<22}"
        unit    = "%" if key != "n" else ""
        for v in vals:
            is_best = abs(v - best) < 0.005
            col     = GREEN if is_best else ""
            row    += f"  {col}{v:>+7.2f}{unit}{RESET if col else ''} "
        print(row)


def print_verdict(all_stats):
    print(f"\n{'='*62}")
    print(f"{BOLD}  VERDICT{RESET}")
    print(f"{'='*62}")

    # Find best variant for each key metric
    labels = ["A", "B", "C", "D"]
    avg_returns  = {lbl: all_stats[lbl]["avg"] for lbl in labels}
    worst_weeks  = {lbl: all_stats[lbl]["worst_week"] for lbl in labels}
    neg_weeks    = {lbl: all_stats[lbl]["pct_neg_weeks"] for lbl in labels}

    best_avg     = max(avg_returns, key=avg_returns.get)
    best_worst   = max(worst_weeks, key=worst_weeks.get)  # least negative
    best_neg_wks = min(neg_weeks, key=neg_weeks.get)      # fewest neg weeks

    print(f"  Best avg return:       Variant {best_avg}  "
          f"({avg_returns[best_avg]:+.2f}%)")
    print(f"  Best downside (worst week): Variant {best_worst}  "
          f"({worst_weeks[best_worst]:+.2f}%)")
    print(f"  Fewest negative weeks: Variant {best_neg_wks}  "
          f"({neg_weeks[best_neg_wks]:.1f}%)")
    print()

    # Recommendation logic
    a_avg = all_stats["A"]["avg"]
    b_avg = all_stats["B"]["avg"]
    c_avg = all_stats["C"]["avg"]
    d_avg = all_stats["D"]["avg"]

    print("  Interpretation:")
    if b_avg > a_avg:
        print(f"  {GREEN}► Upper limits add value (+{b_avg-a_avg:.2f}pp vs no limits){RESET}")
    else:
        print(f"  {RED}► Upper limits reduce returns ({b_avg-a_avg:.2f}pp vs no limits){RESET}")

    if c_avg > a_avg:
        print(f"  {GREEN}► Stop losses add value (+{c_avg-a_avg:.2f}pp vs no stops){RESET}")
    else:
        print(f"  {RED}► Stop losses reduce returns ({c_avg-a_avg:.2f}pp vs no stops){RESET}")

    if d_avg > a_avg:
        print(f"  {GREEN}► Both stops and limits add value (+{d_avg-a_avg:.2f}pp vs neither){RESET}")
    else:
        print(f"  {RED}► Current programme logic (stops+limits) underperforms "
              f"vs hold-to-Friday ({d_avg-a_avg:.2f}pp){RESET}")

    print()
    print("  Note: these results should guide how the programme places")
    print("  orders and sets stops/limits going forward.")
    print()


def main():
    print(f"\n{BOLD}Exit Strategy Diagnostic{RESET}")
    print("Comparing four exit strategies on identical weekly picks\n")

    all_rows  = {}
    all_stats = {}

    for label_full, filepath in VARIANTS:
        label = label_full[0]  # just "A", "B", "C", "D"
        rows  = load_variant(filepath)
        if rows is None:
            print(f"  {label}: file not found -- run backtest_exit_strategy.py first")
            continue
        stats = compute_stats(rows)
        if not stats:
            print(f"  {label}: could not compute stats")
            continue
        all_rows[label]  = rows
        all_stats[label] = stats

    if not all_stats:
        return

    # Print individual variant details
    for label_full, filepath in VARIANTS:
        label = label_full[0]
        if label in all_stats:
            print_variant(label_full, all_stats[label], all_stats, all_rows[label])

    # Print side-by-side comparison
    if len(all_stats) == 4:
        print_comparison_table(all_stats)
        print_verdict(all_stats)
    else:
        print("\nRun backtest_exit_strategy.py to generate all 4 variant files first.")


if __name__ == "__main__":
    main()
