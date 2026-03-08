#!/usr/bin/env python3
"""
calibration_method_diagnostic.py
----------------------------------
Compares static vs moving calibration results and shows
the correction convergence trace.

Run from LSE_Stock_Analyser/ with:
  python3 calibration_method_diagnostic.py
"""

import csv
import os
from collections import defaultdict

GREEN = "\033[92m"
RED   = "\033[91m"
RESET = "\033[0m"
BOLD  = "\033[1m"

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
    returns = [float(r["return_pct"]) for r in rows]
    if not returns:
        return {}
    n   = len(returns)
    pos = [r for r in returns if r > 0]
    neg = [r for r in returns if r <= 0]

    allocs      = [(float(r["return_pct"]), float(r.get("allocation_pct") or 0)) for r in rows]
    total_alloc = sum(a for _, a in allocs)
    kelly       = sum(ret*a for ret, a in allocs) / total_alloc if total_alloc > 0 else sum(returns)/n

    by_week   = defaultdict(list)
    for r in rows:
        by_week[r["run_date"][:10]].append(float(r["return_pct"]))
    week_avgs = {w: sum(v)/len(v) for w, v in by_week.items()}

    corrections = [float(r.get("correction_at_time", 0)) for r in rows]
    probs_raw   = [float(r.get("raw_prob", 0)) for r in rows]
    probs_app   = [float(r.get("applied_prob", 0)) for r in rows]

    return {
        "n":             n,
        "avg":           sum(returns) / n,
        "kelly":         kelly,
        "dir_acc":       len(pos) / n * 100,
        "avg_winner":    sum(pos) / len(pos) if pos else 0,
        "avg_loser":     sum(neg) / len(neg) if neg else 0,
        "worst_week":    min(week_avgs.values()),
        "best_week":     max(week_avgs.values()),
        "pct_neg_weeks": sum(1 for v in week_avgs.values() if v < 0) / len(week_avgs) * 100,
        "avg_correction": sum(corrections) / len(corrections),
        "final_correction": corrections[-1],
        "avg_raw_prob":  sum(probs_raw) / len(probs_raw),   # already in %
        "avg_app_prob":  sum(probs_app) / len(probs_app),   # already in %
        "_returns":      returns,
        "_week_avgs":    week_avgs,
    }


def col(diff, higher_better=True):
    if abs(diff) < 0.005:
        return "~"
    good = (diff > 0) == higher_better
    c    = GREEN if good else RED
    sign = "+" if diff > 0 else ""
    arr  = "▲" if diff > 0 else "▼"
    return f"{c}{arr}{sign}{diff:.3f}{RESET}"


def print_comparison(s_stats, m_stats):
    print(f"\n{'='*64}")
    print(f"{BOLD}  SIDE-BY-SIDE COMPARISON{RESET}")
    print(f"{'='*64}")
    print(f"  {'Metric':<30}  {'Static':>10}  {'Moving':>10}  {'Diff':>10}")
    print(f"  {'-'*30}  {'-'*10}  {'-'*10}  {'-'*10}")

    metrics = [
        ("Avg return (simple)",    "avg",           True),
        ("Avg return (Kelly-wtd)", "kelly",         True),
        ("Directional accuracy",   "dir_acc",       True),
        ("Avg winner",             "avg_winner",    True),
        ("Avg loser",              "avg_loser",     False),
        ("Best week",              "best_week",     True),
        ("Worst week",             "worst_week",    False),
        ("% weeks negative",       "pct_neg_weeks", False),
    ]
    for name, key, hb in metrics:
        sv = s_stats[key]
        mv = m_stats[key]
        print(f"  {name:<30}  {sv:>+9.3f}%  {mv:>+9.3f}%  {col(mv-sv, hb):>10}")

    print(f"\n  Calibration:")
    print(f"  {'Avg raw prob':<30}  {s_stats['avg_raw_prob']:>9.1f}%  {m_stats['avg_raw_prob']:>9.1f}%")
    print(f"  {'Avg applied prob':<30}  {s_stats['avg_app_prob']:>9.1f}%  {m_stats['avg_app_prob']:>9.1f}%")
    print(f"  {'Avg correction applied':<30}  {s_stats['avg_correction']:>+9.2f}pp  {m_stats['avg_correction']:>+9.2f}pp")
    print(f"  {'Final correction':<30}  {s_stats['final_correction']:>+9.2f}pp  {m_stats['final_correction']:>+9.2f}pp")


def print_return_dist(s_stats, m_stats):
    print(f"\n{'='*64}")
    print(f"{BOLD}  RETURN DISTRIBUTION{RESET}")
    print(f"{'='*64}")
    print(f"  {'Bucket':>14}  {'Static':>8}  {'Moving':>8}  {'Diff':>6}")
    s_ret = s_stats["_returns"]
    m_ret = m_stats["_returns"]
    for label, fn in BUCKETS:
        sc   = sum(1 for r in s_ret if fn(r))
        mc   = sum(1 for r in m_ret if fn(r))
        diff = mc - sc
        c    = GREEN if diff > 2 else (RED if diff < -2 else "")
        sign = "+" if diff >= 0 else ""
        print(f"  {label:>14}  {sc:>8}  {mc:>8}  {c}{sign}{diff}{RESET}")


def print_trace(trace_rows):
    if not trace_rows:
        return
    print(f"\n{'='*64}")
    print(f"{BOLD}  MOVING CORRECTION CONVERGENCE TRACE{RESET}")
    print(f"{'='*64}")
    print(f"  {'Week':>5}  {'Date':<12}  {'Picks':>6}  "
          f"{'Model%':>7}  {'Actual%':>8}  {'Applied':>9}  {'Next':>8}")
    print(f"  {'-'*5}  {'-'*12}  {'-'*6}  "
          f"{'-'*7}  {'-'*8}  {'-'*9}  {'-'*8}")

    prev_corr = None
    for t in trace_rows:
        week     = int(t["after_week"])
        corr     = float(t["correction_pp"])
        next_c   = float(t["next_week_correction"])
        converge = abs(next_c - corr) < 0.5 if prev_corr is not None else False
        c        = GREEN if converge else ""

        print(
            f"  {week:>5}  {t['run_date']:<12}  {t['n_picks']:>6}  "
            f"{float(t['avg_raw_prob']):>6.1f}%  "
            f"{float(t['actual_win_rate']):>7.1f}%  "
            f"{corr:>+8.2f}pp  "
            f"{c}{next_c:>+7.2f}pp{RESET}"
        )
        prev_corr = corr

    # Convergence summary
    corrections = [float(t["next_week_correction"]) for t in trace_rows]
    if len(corrections) >= 4:
        last4_range = max(corrections[-4:]) - min(corrections[-4:])
        c = GREEN if last4_range < 1.0 else RED
        print(f"\n  Range of correction in final 4 weeks: "
              f"{c}{last4_range:.2f}pp{RESET}  "
              f"({'converged' if last4_range < 1.0 else 'still oscillating'})")


def print_verdict(s_stats, m_stats):
    print(f"\n{'='*64}")
    print(f"{BOLD}  VERDICT{RESET}")
    print(f"{'='*64}")

    avg_diff   = m_stats["avg"] - s_stats["avg"]
    kelly_diff = m_stats["kelly"] - s_stats["kelly"]
    corr_diff  = abs(m_stats["final_correction"] - s_stats["final_correction"])

    print(f"  Return difference (moving vs static):")
    print(f"    Simple avg:  {avg_diff:+.3f}pp")
    print(f"    Kelly-wtd:   {kelly_diff:+.3f}pp")
    print()
    print(f"  Correction difference: {corr_diff:.2f}pp")
    print()

    if abs(avg_diff) < 0.05:
        print(f"  Returns are essentially identical (< 0.05pp difference).")
        print(f"  The calibration method does not materially affect returns.")
        print()
        print(f"  {GREEN}Recommendation: use moving calibration in the backtest{RESET}")
        print(f"  for methodological consistency with the live system,")
        print(f"  but do not expect a meaningful return difference.")
    elif avg_diff > 0:
        print(f"  {GREEN}Moving calibration produces higher returns (+{avg_diff:.3f}pp).{RESET}")
        print(f"  Recommendation: switch backtest to moving calibration.")
    else:
        print(f"  {RED}Static calibration produces higher returns ({avg_diff:.3f}pp).{RESET}")
        print(f"  The walk-forward constraint hurts early weeks enough to")
        print(f"  offset the methodological benefit.")
        print(f"  Recommendation: keep static calibration but note the")
        print(f"  correction figure may be slightly optimistic.")
    print()


def main():
    print(f"\n{BOLD}Calibration Method Diagnostic{RESET}")
    print("Static vs moving walk-forward calibration\n")

    static_rows = load("lse_cal_static.csv")
    moving_rows = load("lse_cal_moving.csv")
    trace_rows  = load("lse_cal_moving_trace.csv")

    if static_rows is None or moving_rows is None:
        print("  CSV files not found. Run backtest_calibration_method.py first.")
        return

    s_stats = compute_stats(static_rows)
    m_stats = compute_stats(moving_rows)

    if not s_stats or not m_stats:
        print("  Could not compute stats.")
        return

    print_comparison(s_stats, m_stats)
    print_return_dist(s_stats, m_stats)
    if trace_rows:
        print_trace(trace_rows)
    print_verdict(s_stats, m_stats)


if __name__ == "__main__":
    main()
