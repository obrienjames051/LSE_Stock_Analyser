"""
backtest_sector_diversification.py
-----------------------------------
Research script: compares three sector diversification strategies
over the same 52-week backtest window.

Strategies tested:
  A) Max 1 per sector  (current rule)
  B) Max 2 per sector  (relaxed rule)
  C) No sector limit   (pure top 5 by score)

For each strategy, reports:
  - Total picks
  - Avg return per pick
  - Profitable trade rate
  - Directional accuracy
  - Target hit rate
  - Stop-out rate
  - Best week / Worst week
  - Return std deviation (consistency)
  - Avg picks per sector (concentration measure)

Final summary table compares all three side by side.

Run from the LSE_Stock_Analyser folder:
    python backtest_sector_diversification.py
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


# ── Diversification strategies ────────────────────────────────────────────────

def diversify_max_n_per_sector(results: list, top_n: int, max_per_sector: int) -> list:
    """
    Select top_n picks allowing up to max_per_sector from the same sector.
    Results must already be sorted by score descending.
    """
    sector_counts = defaultdict(int)
    picks = []
    for r in results:
        if sector_counts[r["sector"]] < max_per_sector:
            picks.append(r)
            sector_counts[r["sector"]] += 1
        if len(picks) == top_n:
            break
    # If not enough picks found within sector limit, fill from remaining
    for r in results:
        if r not in picks:
            picks.append(r)
        if len(picks) == top_n:
            break
    return picks


def diversify_no_limit(results: list, top_n: int) -> list:
    """Pure top N by score with no sector constraint."""
    return results[:top_n]


# ── Core simulation ───────────────────────────────────────────────────────────

def run_strategy(tickers, price_data, strategy_name, diversify_fn, n_weeks):
    """
    Run the full backtest for one diversification strategy.
    Returns list of resolved pick dicts with week_return added.
    """
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

        # Score all tickers for this week
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

        week_scores.sort(key=lambda x: x["score"], reverse=True)
        top = diversify_fn(week_scores)

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
    """Compute all comparison metrics for a set of picks."""
    if not picks:
        return {}

    resolved = [p for p in picks if p.get("outcome_return_pct") != ""]

    returns      = [float(p["outcome_return_pct"]) for p in resolved]
    went_up      = [p for p in resolved if str(p.get("went_up", "")) == "1"]
    profitable   = [p for p in resolved if str(p.get("profitable", "")) == "1"]
    target_hits  = [p for p in resolved if p.get("outcome_hit") == "YES"]
    stops        = [p for p in resolved if "Stop" in str(p.get("outcome_notes", ""))]

    # Weekly returns (avg return across all picks in each week)
    weeks = defaultdict(list)
    for p in resolved:
        weeks[p["week_label"]].append(float(p["outcome_return_pct"]))
    weekly_avgs = [sum(v) / len(v) for v in weeks.values()]

    # Sector concentration
    sector_counts = defaultdict(int)
    for p in picks:
        sector_counts[p["sector"]] += 1
    n_weeks_run = len(weeks)
    avg_per_sector = (
        {s: round(c / n_weeks_run, 2) for s, c in sector_counts.items()}
        if n_weeks_run > 0 else {}
    )
    # Top sector concentration
    top_sector       = max(sector_counts, key=sector_counts.get) if sector_counts else "N/A"
    top_sector_count = sector_counts.get(top_sector, 0)
    top_sector_pct   = round(top_sector_count / len(picks) * 100, 1) if picks else 0

    return {
        "n_picks":        len(resolved),
        "n_weeks":        n_weeks_run,
        "avg_return":     round(sum(returns) / len(returns), 3) if returns else 0,
        "std_return":     round(statistics.stdev(returns), 3) if len(returns) > 1 else 0,
        "profitable_pct": round(len(profitable) / len(resolved) * 100, 1) if resolved else 0,
        "dir_acc":        round(len(went_up) / len(resolved) * 100, 1) if resolved else 0,
        "target_hit_pct": round(len(target_hits) / len(resolved) * 100, 1) if resolved else 0,
        "stop_pct":       round(len(stops) / len(resolved) * 100, 1) if resolved else 0,
        "best_week":      round(max(weekly_avgs), 3) if weekly_avgs else 0,
        "worst_week":     round(min(weekly_avgs), 3) if weekly_avgs else 0,
        "std_weekly":     round(statistics.stdev(weekly_avgs), 3) if len(weekly_avgs) > 1 else 0,
        "top_sector":     top_sector,
        "top_sector_pct": top_sector_pct,
        "sector_counts":  dict(sector_counts),
    }


def colour_better(val_a, val_b, val_c, higher_is_better=True):
    """
    Return rich colour tags for three values where the best is highlighted.
    higher_is_better=False for metrics like stop_pct and std where lower is better.
    """
    vals = [val_a, val_b, val_c]
    if higher_is_better:
        best = max(vals)
    else:
        best = min(vals)

    def tag(v):
        if v == best:
            return f"[bold bright_green]{v}[/bold bright_green]"
        return f"[dim]{v}[/dim]"

    return tag(val_a), tag(val_b), tag(val_c)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold cyan]Sector Diversification Backtest[/bold cyan]")
    console.print(
        "\n[dim]Comparing three sector diversification strategies over the same\n"
        f"{BACKTEST_WEEKS_TECHNICAL}-week window ({BACKTEST_END_DATE} end date).\n\n"
        "  Strategy A: Max 1 per sector  (current rule)\n"
        "  Strategy B: Max 2 per sector  (relaxed)\n"
        "  Strategy C: No sector limit   (pure top 5 by score)\n\n"
        "All three strategies use the same price data download.\n"
        "This may take 10-15 minutes.[/dim]\n"
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

    strategies = [
        (
            "A: Max 1/sector",
            lambda r: diversify_max_n_per_sector(r, TOP_N, max_per_sector=1),
        ),
        (
            "B: Max 2/sector",
            lambda r: diversify_max_n_per_sector(r, TOP_N, max_per_sector=2),
        ),
        (
            "C: No limit",
            lambda r: diversify_no_limit(r, TOP_N),
        ),
    ]

    all_stats = {}

    for name, diversify_fn in strategies:
        console.print(f"[bold]Running strategy {name}...[/bold]")
        picks = run_strategy(
            tickers, price_data, name, diversify_fn,
            BACKTEST_WEEKS_TECHNICAL,
        )
        stats = compute_stats(picks)
        all_stats[name] = stats
        console.print(
            f"  [green]Done.[/green] {stats['n_picks']} picks across "
            f"{stats['n_weeks']} weeks. "
            f"Avg return: [cyan]{stats['avg_return']:+.3f}%[/cyan]\n"
        )

    # ── Print individual strategy panels ──────────────────────────────────────
    for name, stats in all_stats.items():
        rc  = "bright_green" if stats["avg_return"] >= 0 else "red"
        wc  = "bright_green" if stats["worst_week"] >= -1.0 else "red"

        console.print(Panel(
            f"  Picks:               {stats['n_picks']} across {stats['n_weeks']} weeks\n"
            f"  Avg return:          [{rc}]{stats['avg_return']:+.3f}%[/{rc}] per pick\n"
            f"  Std dev (picks):     {stats['std_return']:.3f}%\n"
            f"  Profitable trades:   {stats['profitable_pct']:.1f}%\n"
            f"  Directional acc:     {stats['dir_acc']:.1f}%\n"
            f"  Target hit rate:     {stats['target_hit_pct']:.1f}%\n"
            f"  Stop-out rate:       {stats['stop_pct']:.1f}%\n"
            f"  Best week (avg):     [bright_green]{stats['best_week']:+.3f}%[/bright_green]\n"
            f"  Worst week (avg):    [{wc}]{stats['worst_week']:+.3f}%[/{wc}]\n"
            f"  Std dev (weekly):    {stats['std_weekly']:.3f}%\n"
            f"  Top sector:          {stats['top_sector']} ({stats['top_sector_pct']:.0f}% of picks)",
            title=f"[bold]Strategy {name}[/bold]",
            box=box.ROUNDED,
        ))

    # ── Final comparison table ─────────────────────────────────────────────────
    console.rule("[bold]Final Comparison Table[/bold]")

    s = list(all_stats.values())
    if len(s) < 3:
        console.print("[red]Not enough strategies completed.[/red]")
        return

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    table.add_column("Metric",              style="bold",  width=28)
    table.add_column("A: Max 1/sector",     justify="right", width=18)
    table.add_column("B: Max 2/sector",     justify="right", width=18)
    table.add_column("C: No limit",         justify="right", width=18)
    table.add_column("Better is...",        justify="left",  width=14, style="dim")

    def row(label, key, fmt, higher_is_better=True, suffix=""):
        va = fmt.format(s[0][key]) + suffix
        vb = fmt.format(s[1][key]) + suffix
        vc = fmt.format(s[2][key]) + suffix
        ca, cb, cc = colour_better(
            s[0][key], s[1][key], s[2][key], higher_is_better
        )
        direction = "Higher" if higher_is_better else "Lower"
        table.add_row(label, ca + suffix, cb + suffix, cc + suffix, direction)

    row("Total picks",          "n_picks",        "{:d}",   higher_is_better=False)
    row("Avg return / pick",    "avg_return",      "{:+.3f}", suffix="%")
    row("Profitable trades",    "profitable_pct",  "{:.1f}",  suffix="%")
    row("Directional accuracy", "dir_acc",         "{:.1f}",  suffix="%")
    row("Target hit rate",      "target_hit_pct",  "{:.1f}",  suffix="%")
    row("Stop-out rate",        "stop_pct",        "{:.1f}",  higher_is_better=False, suffix="%")
    row("Best week",            "best_week",       "{:+.3f}", suffix="%")
    row("Worst week",           "worst_week",      "{:+.3f}")
    row("Std dev (picks)",      "std_return",      "{:.3f}",  higher_is_better=False, suffix="%")
    row("Std dev (weekly)",     "std_weekly",      "{:.3f}",  higher_is_better=False, suffix="%")
    row("Top sector %",         "top_sector_pct",  "{:.1f}",  higher_is_better=False, suffix="%")

    console.print(table)

    # ── Interpretation ─────────────────────────────────────────────────────────
    best_return = max(all_stats, key=lambda k: all_stats[k]["avg_return"])
    best_consistency = min(all_stats, key=lambda k: all_stats[k]["std_weekly"])
    worst_week_vals = {k: v["worst_week"] for k, v in all_stats.items()}
    safest = max(worst_week_vals, key=worst_week_vals.get)

    console.print(Panel(
        f"  Best avg return:      [bold cyan]{best_return}[/bold cyan]\n"
        f"  Most consistent:      [bold cyan]{best_consistency}[/bold cyan]  "
        f"(lowest weekly std dev)\n"
        f"  Best worst-week:      [bold cyan]{safest}[/bold cyan]  "
        f"(least downside in bad weeks)\n\n"
        f"  [dim]Note: higher avg return with higher std dev may indicate the strategy\n"
        f"  is picking up correlated sector risk rather than genuine alpha.\n"
        f"  Check whether the worst week improvement justifies any return trade-off.[/dim]",
        title="[bold]Interpretation[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
