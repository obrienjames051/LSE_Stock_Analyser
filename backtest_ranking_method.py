"""
backtest_ranking_method.py
--------------------------
Test 3: Compares four pick-ranking strategies over the same 52-week
backtest window to find whether an ideal score target outperforms the
current highest-score-wins approach.

Strategies:
  A) Current    — rank by score descending, take top 5
  B) Capped     — rank descending, discard score >= 90, replace from below
  C) Ideal      — rank by proximity to score 80 (closest = best), no cap
  D) Ideal+Cap  — rank by proximity to score 80, discard score >= 90 first

IDEAL_SCORE = 80  (midpoint of consistent 75–88 sweet spot from Test 1/2)
SCORE_CAP   = 90  (crossover point where returns turn negative in both halves)

Run from LSE_Stock_Analyser folder:
    python backtest_ranking_method.py
"""

import sys
import os
import csv
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
from rich.table import Table
from rich.panel import Panel
from rich import box


# ── Constants ─────────────────────────────────────────────────────────────────

IDEAL_SCORE = 80
SCORE_CAP   = 90


# ── Ranking strategies ────────────────────────────────────────────────────────

def rank_current(candidates: list, top_n: int) -> list:
    """A: Rank by score descending. Take top N."""
    return sorted(candidates, key=lambda x: x["score"], reverse=True)[:top_n]


def rank_capped(candidates: list, top_n: int) -> list:
    """B: Discard score >= SCORE_CAP. Rank remainder by score descending."""
    eligible = [c for c in candidates if c["score"] < SCORE_CAP]
    if len(eligible) < top_n:
        # Fall back to including capped picks if not enough eligible
        eligible = sorted(candidates, key=lambda x: x["score"], reverse=True)
    return sorted(eligible, key=lambda x: x["score"], reverse=True)[:top_n]


def rank_ideal(candidates: list, top_n: int) -> list:
    """C: Rank by proximity to IDEAL_SCORE. Closest = best. No cap."""
    return sorted(candidates, key=lambda x: abs(x["score"] - IDEAL_SCORE))[:top_n]


def rank_ideal_capped(candidates: list, top_n: int) -> list:
    """D: Discard score >= SCORE_CAP first. Then rank by proximity to IDEAL_SCORE."""
    eligible = [c for c in candidates if c["score"] < SCORE_CAP]
    if len(eligible) < top_n:
        eligible = candidates  # fallback
    return sorted(eligible, key=lambda x: abs(x["score"] - IDEAL_SCORE))[:top_n]


STRATEGIES = [
    ("A: Current",     rank_current),
    ("B: Capped <90",  rank_capped),
    ("C: Ideal ~80",   rank_ideal),
    ("D: Ideal+Cap",   rank_ideal_capped),
]


# ── Core simulation ───────────────────────────────────────────────────────────

def run_strategy(tickers, price_data, name, rank_fn, n_weeks):
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

        # Score all tickers
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

        # Apply sector diversification first (same as live programme)
        # then apply the ranking strategy on top
        from lse_analyser.screener import diversify as sector_diversify

        # Get a larger diverse pool first, then apply ranking strategy
        # Use 3x TOP_N as the pool to give ranking strategies enough to work with
        diverse_pool = sector_diversify(
            sorted(week_scores, key=lambda x: x["score"], reverse=True),
            min(TOP_N * 3, len(week_scores))
        )

        top = rank_fn(diverse_pool, TOP_N)

        # Simulate trades
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

def compute_stats(picks: list) -> dict:
    if not picks:
        return {}

    resolved = [p for p in picks if p.get("outcome_return_pct") != ""]
    returns  = [float(p["outcome_return_pct"]) for p in resolved]

    went_up    = [p for p in resolved if str(p.get("went_up", "")) == "1"]
    profitable = [p for p in resolved if str(p.get("profitable", "")) == "1"]
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


# ── Display ───────────────────────────────────────────────────────────────────

def colour_best(vals, higher_is_better=True):
    """Return rich colour tags — best value green, others dim."""
    best = max(vals) if higher_is_better else min(vals)
    return [
        f"[bold bright_green]{v}[/bold bright_green]" if v == best else f"[dim]{v}[/dim]"
        for v in vals
    ]


def print_strategy_panel(name, stats):
    rc = "bright_green" if stats["avg_return"] >= 0 else "red"
    wc = "bright_green" if stats["worst_week"] >= -1.5 else "red"

    dist_str = "  ".join(
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
        f"  Score distribution:\n  {dist_str}",
        title=f"[bold]Strategy {name}[/bold]",
        box=box.ROUNDED,
    ))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold cyan]Ranking Method Backtest[/bold cyan]")
    console.print(
        f"\n[dim]Comparing four ranking strategies over {BACKTEST_WEEKS_TECHNICAL} weeks.\n\n"
        f"  A) Current:    rank by score descending\n"
        f"  B) Capped:     rank descending, discard score >= {SCORE_CAP}\n"
        f"  C) Ideal:      rank by proximity to score {IDEAL_SCORE}\n"
        f"  D) Ideal+Cap:  rank by proximity to {IDEAL_SCORE}, discard score >= {SCORE_CAP}\n\n"
        f"  Sector diversification (1 per sector) applied before ranking in all strategies.\n"
        f"  Shared price data download — should take ~10-15 minutes.[/dim]\n"
    )

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        console.print("[yellow]Cancelled.[/yellow]\n")
        return

    tickers = get_tickers()
    console.print(f"[dim]Ticker universe: {len(tickers)} stocks[/dim]\n")

    console.print("[dim]Downloading price data (shared across all strategies)...[/dim]")
    price_data = _download_all_prices(tickers)
    console.print(f"[dim]Price data ready for {len(price_data)} tickers.[/dim]\n")

    all_stats = {}

    for name, rank_fn in STRATEGIES:
        console.print(f"[bold]Running strategy {name}...[/bold]")
        picks = run_strategy(tickers, price_data, name, rank_fn, BACKTEST_WEEKS_TECHNICAL)
        stats = compute_stats(picks)
        all_stats[name] = stats
        console.print(
            f"  [green]Done.[/green] {stats['n_picks']} picks, "
            f"avg score {stats['avg_score']:.1f}, "
            f"avg return [cyan]{stats['avg_return']:+.3f}%[/cyan]\n"
        )

    # Individual panels
    for name, stats in all_stats.items():
        print_strategy_panel(name, stats)

    # ── Comparison table ───────────────────────────────────────────────────────
    console.rule("[bold]Final Comparison Table[/bold]")

    s = list(all_stats.values())
    names = list(all_stats.keys())

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    table.add_column("Metric",           style="bold", width=24)
    for name in names:
        table.add_column(name, justify="right", width=16)
    table.add_column("Better is...", justify="left", width=12, style="dim")

    def row(label, key, fmt, higher_is_better=True, suffix=""):
        raw_vals = [s[i][key] for i in range(len(s))]
        fmt_vals = [fmt.format(v) + suffix for v in raw_vals]
        coloured = colour_best(raw_vals, higher_is_better)
        # Re-attach suffix to coloured versions
        coloured = [c + suffix for c in coloured]
        direction = "Higher" if higher_is_better else "Lower"
        table.add_row(label, *coloured, direction)

    row("Total picks",          "n_picks",        "{:d}",    higher_is_better=False)
    row("Avg score",            "avg_score",       "{:.1f}",  higher_is_better=False)
    row("Avg return / pick",    "avg_return",      "{:+.3f}", suffix="%")
    row("Profitable trades",    "profitable_pct",  "{:.1f}",  suffix="%")
    row("Directional accuracy", "dir_acc",         "{:.1f}",  suffix="%")
    row("Target hit rate",      "target_hit_pct",  "{:.1f}",  suffix="%")
    row("Stop-out rate",        "stop_pct",        "{:.1f}",  higher_is_better=False, suffix="%")
    row("Best week",            "best_week",       "{:+.3f}", suffix="%")
    row("Worst week",           "worst_week",      "{:+.3f}", suffix="%")
    row("Std dev (picks)",      "std_return",      "{:.3f}",  higher_is_better=False, suffix="%")
    row("Std dev (weekly)",     "std_weekly",      "{:.3f}",  higher_is_better=False, suffix="%")

    console.print(table)

    # ── Interpretation ─────────────────────────────────────────────────────────
    best_return      = max(all_stats, key=lambda k: all_stats[k]["avg_return"])
    best_consistency = min(all_stats, key=lambda k: all_stats[k]["std_weekly"])
    safest           = max(all_stats, key=lambda k: all_stats[k]["worst_week"])

    # C vs D comparison
    c_ret = all_stats.get("C: Ideal ~80", {}).get("avg_return", 0)
    d_ret = all_stats.get("D: Ideal+Cap", {}).get("avg_return", 0)
    cap_adds_value = d_ret > c_ret

    console.print(Panel(
        f"  Best avg return:      [bold cyan]{best_return}[/bold cyan]\n"
        f"  Most consistent:      [bold cyan]{best_consistency}[/bold cyan]  "
        f"(lowest weekly std dev)\n"
        f"  Best worst-week:      [bold cyan]{safest}[/bold cyan]  "
        f"(least downside in bad weeks)\n\n"
        f"  C vs D (does the 90+ cap add value on top of ideal ranking?):\n"
        f"    C avg return: {c_ret:+.3f}%   D avg return: {d_ret:+.3f}%\n"
        f"    {'[bright_green]Yes[/bright_green] — cap improves on ideal ranking alone' if cap_adds_value else '[yellow]No[/yellow] — ideal ranking already handles over-extended picks'}\n\n"
        f"  [dim]If A still wins: the sweet spot pattern from Tests 1/2 may not\n"
        f"  survive full backtest conditions (sector diversification changes\n"
        f"  which picks are available to each strategy).[/dim]",
        title="[bold]Interpretation[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
