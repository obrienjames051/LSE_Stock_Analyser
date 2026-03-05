"""
directional_diagnostic.py
--------------------------
Analyses backtest results to separate directional accuracy from target accuracy.

Key questions:
  1. What % of picks ended the week ABOVE entry price (directional accuracy)?
  2. Of the picks that missed the target, how many still went up?
  3. What is the return distribution across all picks?
  4. What would the hit rate be at lower target thresholds (0.5%, 1.0% etc)?

Run from LSE_Stock_Analyser/ with: python3 directional_diagnostic.py
"""

import csv
from collections import defaultdict

def load_csv(filepath):
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"  FILE NOT FOUND: {filepath}")
        return []

def analyse(rows, label):
    resolved = [
        r for r in rows
        if r.get("outcome_hit", "").strip() in ("YES", "NO")
        and r.get("outcome_return_pct", "").strip()
    ]

    if not resolved:
        print(f"\n{label}: no resolved picks found")
        return

    returns = [float(r["outcome_return_pct"]) for r in resolved]
    n       = len(returns)

    # Directional accuracy
    up      = [r for r in returns if r > 0]
    down    = [r for r in returns if r <= 0]
    dir_acc = len(up) / n * 100

    # Target hit rate
    hits    = [r for r in resolved if r["outcome_hit"] == "YES"]
    hit_rate = len(hits) / n * 100

    # Of the misses, how many still went up?
    misses       = [r for r in resolved if r["outcome_hit"] == "NO"]
    misses_up    = [r for r in misses if float(r["outcome_return_pct"]) > 0]
    misses_up_pct = len(misses_up) / len(misses) * 100 if misses else 0

    # Average returns
    avg_return      = sum(returns) / n
    avg_return_up   = sum(up) / len(up) if up else 0
    avg_return_down = sum(down) / len(down) if down else 0

    # Return distribution buckets
    buckets = {
        "< -3%":       [r for r in returns if r < -3],
        "-3% to -2%":  [r for r in returns if -3 <= r < -2],
        "-2% to -1%":  [r for r in returns if -2 <= r < -1],
        "-1% to 0%":   [r for r in returns if -1 <= r < 0],
        "0% to +1%":   [r for r in returns if 0 <= r < 1],
        "+1% to +2%":  [r for r in returns if 1 <= r < 2],
        "+2% to +3%":  [r for r in returns if 2 <= r < 3],
        "> +3%":       [r for r in returns if r >= 3],
    }

    # Alternative hit rates at different thresholds
    thresholds = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]

    print(f"\n{'='*60}")
    print(f"{label}  ({n} resolved picks)")
    print('='*60)

    print(f"\n  TARGET HIT RATE:      {hit_rate:.1f}%  ({len(hits)}/{n} hit target price)")
    print(f"  DIRECTIONAL ACCURACY: {dir_acc:.1f}%  ({len(up)}/{n} ended week above entry)")
    print(f"\n  Of {len(misses)} misses: {len(misses_up)} ({misses_up_pct:.1f}%) still ended above entry price")

    print(f"\n  Average return (all picks):   {avg_return:+.2f}%")
    print(f"  Average return (picks up):    {avg_return_up:+.2f}%")
    print(f"  Average return (picks down):  {avg_return_down:+.2f}%")

    print(f"\n  Return distribution:")
    for bucket, vals in buckets.items():
        bar = "█" * len(vals)
        print(f"    {bucket:>14}  {len(vals):>3} picks  {bar}")

    print(f"\n  Hit rate at alternative target thresholds:")
    for threshold in thresholds:
        above = sum(1 for r in returns if r >= threshold)
        pct   = above / n * 100
        label_str = f">= +{threshold:.1f}%" if threshold >= 0 else f">= {threshold:.1f}%"
        print(f"    {label_str:>10}  {pct:>5.1f}%  ({above}/{n})")

    # Best and worst weeks
    by_week = defaultdict(list)
    for r in resolved:
        by_week[r["run_date"][:10]].append(float(r["outcome_return_pct"]))

    week_avgs = [(date, sum(v)/len(v), len(v)) for date, v in by_week.items()]
    week_avgs.sort(key=lambda x: x[1], reverse=True)

    print(f"\n  Best 5 weeks:")
    for date, avg, n_picks in week_avgs[:5]:
        print(f"    {date}  avg {avg:+.2f}%  ({n_picks} picks)")

    print(f"\n  Worst 5 weeks:")
    for date, avg, n_picks in week_avgs[-5:]:
        print(f"    {date}  avg {avg:+.2f}%  ({n_picks} picks)")


# Run analysis on both files
for filepath, label in [
    ("lse_backtest_technical.csv", "Phase 1 - Technical Backtest"),
    ("lse_backtest_news.csv",      "Phase 2 - News-Enhanced Backtest"),
]:
    rows = load_csv(filepath)
    if rows:
        analyse(rows, label)

print()
