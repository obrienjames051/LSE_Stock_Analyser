"""
Quick diagnostic to check backtest CSV contents.
Run from LSE_Stock_Analyser/ folder with: python3 backtest_diagnostic.py
"""
import csv
from collections import Counter

for filepath, label in [
    ("lse_backtest_technical.csv", "Phase 1 - Technical"),
    ("lse_backtest_news.csv",      "Phase 2 - News-enhanced"),
]:
    print(f"\n{'='*60}")
    print(f"{label}: {filepath}")
    print('='*60)

    try:
        rows = []
        with open(filepath, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print("  FILE NOT FOUND")
        continue

    print(f"  Total rows:      {len(rows)}")

    resolved = [r for r in rows if r.get("outcome_hit", "").strip() in ("YES", "NO")]
    print(f"  Resolved picks:  {len(resolved)}")

    # Unique tickers
    tickers  = [r["ticker"] for r in rows]
    unique   = set(tickers)
    print(f"  Unique tickers:  {len(unique)}")

    # Unique run dates
    dates    = sorted(set(r["run_date"] for r in rows if r.get("run_date")))
    print(f"  Unique run dates: {len(dates)}")
    for d in dates:
        picks_this_week = [r for r in rows if r["run_date"] == d]
        hits = sum(1 for r in picks_this_week if r.get("outcome_hit") == "YES")
        misses = sum(1 for r in picks_this_week if r.get("outcome_hit") == "NO")
        print(f"    {d}  --  {len(picks_this_week)} picks  "
              f"({hits} hits, {misses} misses)")

    # Sector distribution
    sectors = Counter(r["sector"] for r in rows)
    print(f"\n  Sector distribution:")
    for sector, count in sectors.most_common():
        print(f"    {count:>4}  {sector}")

    # Most frequently picked tickers
    ticker_counts = Counter(tickers)
    print(f"\n  Most frequently picked tickers (top 10):")
    for ticker, count in ticker_counts.most_common(10):
        print(f"    {count:>3}x  {ticker}")

