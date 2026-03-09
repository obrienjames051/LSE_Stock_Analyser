"""
backtest_cap_ordering.py
------------------------
Tests two orderings of cap and diversification at cap values 85 and 90,
to determine which approach is methodologically correct and produces
genuinely better returns.

Approach X — Cap then Diversify (backtest_score_cap.py method):
  1. Remove all stocks scoring >= cap from full universe
  2. Diversify from remaining stocks
  Replacements drawn from full universe across all sectors.

Approach Y — Diversify then Cap (backtest_ranking_method.py method):
  1. Diversify full universe to a pool of 3x TOP_N (15 stocks)
  2. Remove stocks scoring >= cap from that pool
  3. Fill any gaps from next best in pool below cap
  Replacements constrained to already-diversified pool.

Four strategies tested:
  A) No cap baseline
  B) Cap 90, cap-then-diversify  (X ordering)
  C) Cap 90, diversify-then-cap  (Y ordering)
  D) Cap 85, cap-then-diversify  (X ordering)
  E) Cap 85, diversify-then-cap  (Y ordering)

Run from LSE_Stock_Analyser folder:
    python backtest_cap_ordering.py
"""

import sys
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lse_analyser.config import (
    TOP_N, BACKTEST_WEEKS_TECHNICAL, BACKTEST_CAPITAL,
    ATR_MULTIPLIER, STOP_MULTIPLIER, BACKTEST_END_DATE,
)
from lse_analyser.tickers import get_tickers
from lse_analyser.sizing import calculate_allocations
from lse_analyser.utils import console
from lse_analyser.backtest import (
    _download_all_prices,
    _score_historical,
    _simulate_trade,
)
from lse_analyser.screener import diversify as sector_diversify

from rich.table import Table
from rich.panel import Panel
from rich import box


# ── Selection functions ───────────────────────────────────────────────────────

def select_no_cap(week_scores):
    """Baseline: sort descending, diversify, take top 5."""
    sorted_scores = sorted(week_scores, key=lambda x: x["score"], reverse=True)
    return sector_diversify(sorted_scores, TOP_N)


def select_cap_then_diversify(week_scores, cap):
    """
    X ordering: remove cap+ stocks first, then diversify from remainder.
    Replacements drawn from full universe.
    """
    eligible = [r for r in week_scores if r["score"] < cap]
    if len(eligible) < TOP_N:
        # Fallback: fill from least over-extended cap+ stocks
        overflow = sorted(
            [r for r in week_scores if r["score"] >= cap],
            key=lambda x: x["score"]
        )
        eligible = eligible + overflow[:(TOP_N - len(eligible))]
    eligible_sorted = sorted(eligible, key=lambda x: x["score"], reverse=True)
    return sector_diversify(eligible_sorted, TOP_N)


def select_diversify_then_cap(week_scores, cap, pool_size=15):
    """
    Y ordering: diversify to pool first, then remove cap+ stocks from pool.
    Replacements constrained to the pre-diversified pool.
    """
    sorted_scores = sorted(week_scores, key=lambda x: x["score"], reverse=True)
    diverse_pool  = sector_diversify(sorted_scores, min(pool_size, len(sorted_scores)))

    eligible  = [r for r in diverse_pool if r["score"] < cap]
    shortfall = TOP_N - len(eligible)

    if shortfall > 0:
        overflow = sorted(
            [r for r in diverse_pool if r["score"] >= cap],
            key=lambda x: x["score"]
        )
        eligible = eligible + overflow[:shortfall]

    return sorted(eligible, key=lambda x: x["score"], reverse=True)[:TOP_N]


STRATEGIES = [
    ("A: No cap",              lambda ws: select_no_cap(ws)),
    ("B: Cap90 cap→div",       lambda ws: select_cap_then_diversify(ws, 90)),
    ("C: Cap90 div→cap",       lambda ws: select_diversify_then_cap(ws, 90)),
    ("D: Cap85 cap→div",       lambda ws: select_cap_then_diversify(ws, 85)),
    ("E: Cap85 div→cap",       lambda ws: select_diversify_then_cap(ws, 85)),
]


# ── Core simulation ───────────────────────────────────────────────────────────

def run_strategy(tickers, price_data, select_fn, n_weeks):
    all_results = []
    end_monday  = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")

    for week_offset in range(n_weeks, 0, -1):
        sim_monday    = end_monday - timedelta(weeks=week_offset)
        entry_tuesday = sim_monday + timedelta(days=1)
        exit_monday   = sim_monday + timedelta(days=8)

        if exit_monday > end_monday:
            continue

        sim_monday_pd    = pd.Timestamp(sim_monday)
        entry_tuesday_pd = pd.Timestamp(entry_tuesday)
        exit_monday_pd   = pd.Timestamp(exit_monday)

        week_scores = []
        for ticker, sector in tickers.items():
            df = price_data.get(ticker)
            if df is None or len(df) < 30:
                continue
            try:
                hist = df[df.index <= sim_monday_pd].copy()
                if len(hist) < 30:
                    continue
                r = _score_historical(ticker, sector, hist)
                if r:
                    week_scores.append(r)
            except Exception:
                continue

        if not week_scores:
            continue

        top = select_fn(week_scores)

        week_resolved = []
        for r in top:
            df = price_data.get(r["ticker"] + ".L")
            if df is None:
                df = price_data.get(r["ticker"])
            if df is None:
                continue
            try:
                outcome = _simulate_trade(df, r, entry_tuesday_pd, exit_monday_pd)
                if outcome:
                    r.update(outcome)
                    r["run_date"]   = sim_monday.strftime("%Y-%m-%d")
                    r["week_label"] = sim_monday.strftime("%Y-%m-%d")
                    week_resolved.append(r)
            except Exception:
                continue

        if week_resolved:
            calculate_allocations(week_resolved, BACKTEST_CAPITAL)
            all_results.extend(week_resolved)

    return all_results


# ── Statistics ────────────────────────────────────────────────────────────────

def compute_stats(picks):
    if not picks:
        return {}

    resolved    = [p for p in picks if p.get("outcome_return_pct") != ""]
    returns     = [float(p["outcome_return_pct"]) for p in resolved]
    went_up     = [p for p in resolved if str(p.get("went_up",    "")) == "1"]
    profitable  = [p for p in resolved if str(p.get("profitable", "")) == "1"]
    target_hits = [p for p in resolved if p.get("outcome_hit") == "YES"]
    stops       = [p for p in resolved if "Stop" in str(p.get("outcome_notes", ""))]

    weeks = defaultdict(list)
    for p in resolved:
        weeks[p["week_label"]].append(float(p["outcome_return_pct"]))
    weekly_avgs = [sum(v) / len(v) for v in weeks.values()]

    score_dist = defaultdict(int)
    for p in picks:
        band = (p["score"] // 5) * 5
        score_dist[f"{band}–{band+5}"] += 1

    return {
        "n_picks":        len(resolved),
        "n_weeks":        len(weeks),
        "avg_return":     round(sum(returns) / len(returns), 3) if returns else 0,
        "std_return":     round(statistics.stdev(returns), 3) if len(returns) > 1 else 0,
        "profitable_pct": round(len(profitable) / len(resolved) * 100, 1) if resolved else 0,
        "dir_acc":        round(len(went_up) / len(resolved) * 100, 1) if resolved else 0,
        "target_hit_pct": round(len(target_hits) / len(resolved) * 100, 1) if resolved else 0,
        "stop_pct":       round(len(stops) / len(resolved) * 100, 1) if resolved else 0,
        "best_week":      round(max(weekly_avgs), 3) if weekly_avgs else 0,
        "worst_week":     round(min(weekly_avgs), 3) if weekly_avgs else 0,
        "std_weekly":     round(statistics.stdev(weekly_avgs), 3) if len(weekly_avgs) > 1 else 0,
        "avg_score":      round(sum(p["score"] for p in picks) / len(picks), 1),
        "score_dist":     dict(sorted(score_dist.items())),
    }


def colour_best(vals, higher_is_better=True):
    best = max(vals) if higher_is_better else min(vals)
    return [
        f"[bold bright_green]{v}[/bold bright_green]" if v == best else f"[dim]{v}[/dim]"
        for v in vals
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold cyan]Cap Ordering Comparison[/bold cyan]")
    console.print(
        "\n[dim]Tests two orderings of cap and diversification at cap 85 and 90.\n\n"
        "  X (cap→div): Remove cap+ stocks first, diversify from remainder\n"
        "  Y (div→cap): Diversify to pool of 15 first, cap within that pool\n\n"
        "  A) No cap baseline\n"
        "  B) Cap 90, X ordering  (cap then diversify)\n"
        "  C) Cap 90, Y ordering  (diversify then cap)\n"
        "  D) Cap 85, X ordering  (cap then diversify)\n"
        "  E) Cap 85, Y ordering  (diversify then cap)\n\n"
        "  ~10-15 minutes.[/dim]\n"
    )

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        console.print("[yellow]Cancelled.[/yellow]\n")
        return

    tickers = get_tickers()
    console.print(f"[dim]Ticker universe: {len(tickers)} stocks[/dim]\n")

    console.print("[dim]Downloading price data...[/dim]")
    price_data = _download_all_prices(tickers)
    console.print(f"[dim]Price data ready for {len(price_data)} tickers.[/dim]\n")

    all_stats  = {}
    all_labels = []

    for name, select_fn in STRATEGIES:
        console.print(f"[bold]Running {name}...[/bold]")
        picks = run_strategy(tickers, price_data, select_fn, BACKTEST_WEEKS_TECHNICAL)
        stats = compute_stats(picks)
        all_stats[name]  = stats
        all_labels.append(name)
        console.print(
            f"  [green]Done.[/green] {stats['n_picks']} picks, "
            f"avg score {stats['avg_score']:.1f}, "
            f"avg return [cyan]{stats['avg_return']:+.3f}%[/cyan]\n"
        )

    # ── Individual panels ──────────────────────────────────────────────────────
    for label in all_labels:
        stats = all_stats[label]
        rc    = "bright_green" if stats["avg_return"] >= 0 else "red"
        wc    = "bright_green" if stats["worst_week"] >= -5.0 else "red"
        dist  = "  ".join(
            f"{band}: {count}" for band, count in stats["score_dist"].items()
        )
        console.print(Panel(
            f"  Picks:             {stats['n_picks']} across {stats['n_weeks']} weeks\n"
            f"  Avg score:         {stats['avg_score']:.1f}\n"
            f"  Avg return:        [{rc}]{stats['avg_return']:+.3f}%[/{rc}] per pick\n"
            f"  Std dev (picks):   {stats['std_return']:.3f}%\n"
            f"  Profitable:        {stats['profitable_pct']:.1f}%\n"
            f"  Directional acc:   {stats['dir_acc']:.1f}%\n"
            f"  Target hit rate:   {stats['target_hit_pct']:.1f}%\n"
            f"  Stop-out rate:     {stats['stop_pct']:.1f}%\n"
            f"  Best week:         [bright_green]{stats['best_week']:+.3f}%[/bright_green]\n"
            f"  Worst week:        [{wc}]{stats['worst_week']:+.3f}%[/{wc}]\n"
            f"  Std dev (weekly):  {stats['std_weekly']:.3f}%\n\n"
            f"  Score distribution:\n  {dist}",
            title=f"[bold]{label}[/bold]",
            box=box.ROUNDED,
        ))

    # ── Comparison table ───────────────────────────────────────────────────────
    console.rule("[bold]Final Comparison Table[/bold]")

    s_list = [all_stats[l] for l in all_labels]

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    table.add_column("Metric",          style="bold", width=22)
    for label in all_labels:
        table.add_column(label, justify="right", width=16)
    table.add_column("Better", justify="left", width=8, style="dim")

    def row(label, key, fmt, higher_is_better=True, suffix=""):
        raw_vals = [s[key] for s in s_list]
        coloured = colour_best(raw_vals, higher_is_better)
        coloured = [c + suffix for c in coloured]
        table.add_row(label, *coloured, "↑" if higher_is_better else "↓")

    row("Avg score",           "avg_score",      "{:.1f}",  higher_is_better=False)
    row("Avg return / pick",   "avg_return",      "{:+.3f}", suffix="%")
    row("Profitable trades",   "profitable_pct",  "{:.1f}",  suffix="%")
    row("Directional acc",     "dir_acc",         "{:.1f}",  suffix="%")
    row("Target hit rate",     "target_hit_pct",  "{:.1f}",  suffix="%")
    row("Stop-out rate",       "stop_pct",        "{:.1f}",  higher_is_better=False, suffix="%")
    row("Best week",           "best_week",       "{:+.3f}", suffix="%")
    row("Worst week",          "worst_week",      "{:+.3f}", suffix="%")
    row("Std dev (picks)",     "std_return",      "{:.3f}",  higher_is_better=False, suffix="%")
    row("Std dev (weekly)",    "std_weekly",      "{:.3f}",  higher_is_better=False, suffix="%")

    console.print(table)

    # ── Interpretation ─────────────────────────────────────────────────────────
    best_return      = max(all_labels, key=lambda l: all_stats[l]["avg_return"])
    best_worst_week  = max(all_labels, key=lambda l: all_stats[l]["worst_week"])
    best_consistency = min(all_labels, key=lambda l: all_stats[l]["std_weekly"])

    # X vs Y direct comparison at each cap
    c90_x = all_stats.get("B: Cap90 cap→div", {}).get("avg_return", 0)
    c90_y = all_stats.get("C: Cap90 div→cap", {}).get("avg_return", 0)
    c85_x = all_stats.get("D: Cap85 cap→div", {}).get("avg_return", 0)
    c85_y = all_stats.get("E: Cap85 div→cap", {}).get("avg_return", 0)

    console.print(Panel(
        f"  Best avg return:    [bold cyan]{best_return}[/bold cyan]\n"
        f"  Most consistent:    [bold cyan]{best_consistency}[/bold cyan]\n"
        f"  Best worst-week:    [bold cyan]{best_worst_week}[/bold cyan]\n\n"
        f"  Cap 90 ordering comparison:\n"
        f"    cap→div (X): {c90_x:+.3f}%    div→cap (Y): {c90_y:+.3f}%\n"
        f"    Winner: {'X (cap first)' if c90_x >= c90_y else 'Y (diversify first)'}\n\n"
        f"  Cap 85 ordering comparison:\n"
        f"    cap→div (X): {c85_x:+.3f}%    div→cap (Y): {c85_y:+.3f}%\n"
        f"    Winner: {'X (cap first)' if c85_x >= c85_y else 'Y (diversify first)'}\n\n"
        f"  [dim]If X wins at both caps: cap-then-diversify is the correct approach\n"
        f"  and the earlier 0.903% result was a methodological artefact.\n"
        f"  If Y wins: diversify-then-cap genuinely produces better picks\n"
        f"  and the pool constraint is doing useful work.[/dim]",
        title="[bold]Interpretation[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
