"""
backtest_score_cap.py
---------------------
Finds the optimal score cap by testing seven cap values over the same
52-week backtest window.

For each cap, any pick scoring >= cap is excluded from selection entirely
and replaced by the next best eligible pick below the cap.

Caps tested:
  - 90  (baseline from ranking method test)
  - 89
  - 88
  - 87
  - 86
  - 85
  - 83

Also includes an uncapped baseline (A: No cap) for direct comparison.

Sector diversification (1 per sector) applied in all strategies.

Run from LSE_Stock_Analyser folder:
    python backtest_score_cap.py
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
from lse_analyser.screener import diversify as sector_diversify

from rich.table import Table
from rich.panel import Panel
from rich import box


# ── Cap strategies ────────────────────────────────────────────────────────────

CAPS = [None, 90, 89, 88, 87, 86, 85, 83]  # None = no cap


def apply_cap(candidates: list, top_n: int, cap) -> list:
    """
    Select top_n picks by score descending.
    If cap is set, exclude any pick with score >= cap first.
    Falls back to including capped picks if not enough eligible picks exist.
    """
    if cap is None:
        return sorted(candidates, key=lambda x: x["score"], reverse=True)[:top_n]

    eligible  = [c for c in candidates if c["score"] < cap]
    shortfall = top_n - len(eligible)

    if shortfall > 0:
        # Not enough picks below cap — fill remainder from capped picks
        # sorted by score descending so we take the least over-extended first
        overflow = sorted(
            [c for c in candidates if c["score"] >= cap],
            key=lambda x: x["score"]
        )
        eligible = eligible + overflow[:shortfall]

    return sorted(eligible, key=lambda x: x["score"], reverse=True)[:top_n]


# ── Core simulation ───────────────────────────────────────────────────────────

def run_cap(tickers, price_data, cap, n_weeks):
    """Run the full backtest for one cap value."""
    all_results = []
    end_monday  = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")
    fallback_weeks = 0  # weeks where cap couldn't be fully respected

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

        # Apply cap BEFORE diversification — high-scoring stocks must be
        # removed from the candidate pool entirely, not just deprioritised.
        # If cap is applied after diversify(), diversify() has already chosen
        # the high-scoring picks and the cap has nothing left to replace them with.
        sorted_scores = sorted(week_scores, key=lambda x: x["score"], reverse=True)

        if cap is not None:
            eligible = [r for r in sorted_scores if r["score"] < cap]
            if len(eligible) < TOP_N:
                # Not enough picks below cap — track fallback and fill shortfall
                # from over-cap stocks, taking the least over-extended first
                fallback_weeks += 1
                overflow = [r for r in sorted_scores if r["score"] >= cap]
                eligible = eligible + overflow[:(TOP_N - len(eligible))]
        else:
            eligible = sorted_scores

        # Sector diversification on the cap-filtered pool
        diverse_pool = sector_diversify(eligible, min(TOP_N * 4, len(eligible)))
        top = diverse_pool[:TOP_N]

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

    return all_results, fallback_weeks


# ── Statistics ────────────────────────────────────────────────────────────────

def compute_stats(picks, fallback_weeks=0):
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

    # Score distribution
    score_dist = defaultdict(int)
    for p in picks:
        band = (p["score"] // 5) * 5
        score_dist[f"{band}–{band+5}"] += 1

    # Picks that were above cap (fallback picks)
    above_cap_count = sum(1 for p in picks if p.get("_above_cap", False))

    return {
        "n_picks":         len(resolved),
        "n_weeks":         len(weeks),
        "fallback_weeks":  fallback_weeks,
        "avg_return":      round(sum(returns) / len(returns), 3) if returns else 0,
        "std_return":      round(statistics.stdev(returns), 3) if len(returns) > 1 else 0,
        "profitable_pct":  round(len(profitable) / len(resolved) * 100, 1) if resolved else 0,
        "dir_acc":         round(len(went_up) / len(resolved) * 100, 1) if resolved else 0,
        "target_hit_pct":  round(len(target_hits) / len(resolved) * 100, 1) if resolved else 0,
        "stop_pct":        round(len(stops) / len(resolved) * 100, 1) if resolved else 0,
        "best_week":       round(max(weekly_avgs), 3) if weekly_avgs else 0,
        "worst_week":      round(min(weekly_avgs), 3) if weekly_avgs else 0,
        "std_weekly":      round(statistics.stdev(weekly_avgs), 3) if len(weekly_avgs) > 1 else 0,
        "avg_score":       round(sum(p["score"] for p in picks) / len(picks), 1),
        "score_dist":      dict(sorted(score_dist.items())),
    }


# ── Display ───────────────────────────────────────────────────────────────────

def colour_best(vals, higher_is_better=True):
    """Highlight best value green, dim the rest. Handles ties."""
    best = max(vals) if higher_is_better else min(vals)
    return [
        f"[bold bright_green]{v}[/bold bright_green]" if v == best else f"[dim]{v}[/dim]"
        for v in vals
    ]


def cap_label(cap):
    return "No cap" if cap is None else f"Cap {cap}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold cyan]Score Cap Optimisation Backtest[/bold cyan]")
    console.print(
        f"\n[dim]Testing {len(CAPS)} score cap values over {BACKTEST_WEEKS_TECHNICAL} weeks.\n\n"
        "  Any pick scoring >= cap is excluded and replaced by the next\n"
        "  best eligible pick below the cap. Sector diversification (1 per\n"
        "  sector) is applied before the cap in all strategies.\n\n"
        "  Caps: No cap, " + ", ".join(str(c) for c in CAPS if c is not None) + "\n\n"
        "  Shared price data download — ~10-15 minutes.[/dim]\n"
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

    for cap in CAPS:
        label = cap_label(cap)
        console.print(f"[bold]Running {label}...[/bold]")
        picks, fallback_weeks = run_cap(
            tickers, price_data, cap, BACKTEST_WEEKS_TECHNICAL
        )
        stats = compute_stats(picks, fallback_weeks)
        all_stats[label]  = stats
        all_labels.append(label)

        fb_note = f"  ({fallback_weeks} weeks needed fallback picks)" if fallback_weeks else ""
        console.print(
            f"  [green]Done.[/green] {stats['n_picks']} picks, "
            f"avg score {stats['avg_score']:.1f}, "
            f"avg return [cyan]{stats['avg_return']:+.3f}%[/cyan]{fb_note}\n"
        )

    # ── Individual panels ──────────────────────────────────────────────────────
    for label in all_labels:
        stats = all_stats[label]
        rc    = "bright_green" if stats["avg_return"] >= 0 else "red"
        wc    = "bright_green" if stats["worst_week"] >= -5.0 else "red"
        dist  = "  ".join(
            f"{band}: {count}" for band, count in stats["score_dist"].items()
        )
        fb    = f"\n  Fallback weeks:     {stats['fallback_weeks']} (cap couldn't be fully respected)" \
                if stats["fallback_weeks"] else ""

        console.print(Panel(
            f"  Picks:             {stats['n_picks']} across {stats['n_weeks']} weeks{fb}\n"
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
    table.add_column("Metric", style="bold", width=22)
    for label in all_labels:
        table.add_column(label, justify="right", width=11)
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
    row("Fallback weeks",      "fallback_weeks",  "{:d}",    higher_is_better=False)

    console.print(table)

    # ── Return vs cap curve ────────────────────────────────────────────────────
    console.rule("[bold]Return vs Cap[/bold]")

    curve = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    curve.add_column("Cap",          width=10)
    curve.add_column("Avg return",   justify="right", width=14)
    curve.add_column("vs No cap",    justify="right", width=12)
    curve.add_column("Worst week",   justify="right", width=12)
    curve.add_column("Fallback wks", justify="right", width=14)
    curve.add_column("Return bar",   width=26)

    no_cap_ret = all_stats["No cap"]["avg_return"]

    for label in all_labels:
        stats  = all_stats[label]
        ret    = stats["avg_return"]
        diff   = round(ret - no_cap_ret, 3)
        rc     = "bright_green" if ret >= no_cap_ret else "yellow" if ret >= 0 else "red"
        dc     = "bright_green" if diff > 0 else "red" if diff < -0.05 else "dim"
        wc     = "bright_green" if stats["worst_week"] >= -5.0 else "red"
        filled = int(min(abs(ret) / 2.0 * 24, 24))
        bar    = ("[green]" + "█" * filled + "[/green]" + "░" * (24 - filled)
                  if ret >= 0 else
                  "[red]" + "█" * filled + "[/red]" + "░" * (24 - filled))

        curve.add_row(
            label,
            f"[{rc}]{ret:+.3f}%[/{rc}]",
            f"[{dc}]{diff:+.3f}%[/{dc}]",
            f"[{wc}]{stats['worst_week']:+.3f}%[/{wc}]",
            str(stats["fallback_weeks"]),
            bar,
        )

    console.print(curve)

    # ── Interpretation ─────────────────────────────────────────────────────────
    best_return      = max(all_labels, key=lambda l: all_stats[l]["avg_return"])
    best_worst_week  = max(all_labels, key=lambda l: all_stats[l]["worst_week"])
    best_consistency = min(all_labels, key=lambda l: all_stats[l]["std_weekly"])

    best_ret_val  = all_stats[best_return]["avg_return"]
    improvement   = round(best_ret_val - no_cap_ret, 3)

    # Find the cap that best balances return and worst-week
    # Score = avg_return normalised - std_weekly normalised
    returns_list    = [all_stats[l]["avg_return"] for l in all_labels]
    worst_list      = [all_stats[l]["worst_week"]  for l in all_labels]
    ret_range       = max(returns_list) - min(returns_list) or 1
    worst_range     = max(worst_list)   - min(worst_list)   or 1

    def balance_score(label):
        r = (all_stats[label]["avg_return"] - min(returns_list)) / ret_range
        w = (all_stats[label]["worst_week"]  - min(worst_list))   / worst_range
        return r * 0.6 + w * 0.4  # weight return more than worst-week

    best_balanced = max(all_labels, key=balance_score)

    console.print(Panel(
        f"  Best avg return:       [bold cyan]{best_return}[/bold cyan]  "
        f"({best_ret_val:+.3f}%  vs no-cap: {no_cap_ret:+.3f}%  improvement: {improvement:+.3f}pp)\n"
        f"  Most consistent:       [bold cyan]{best_consistency}[/bold cyan]  "
        f"(lowest weekly std dev)\n"
        f"  Best worst-week:       [bold cyan]{best_worst_week}[/bold cyan]  "
        f"(least downside in bad weeks)\n"
        f"  Best balanced (60/40): [bold cyan]{best_balanced}[/bold cyan]  "
        f"(return weighted 60%, worst-week 40%)\n\n"
        f"  [dim]Key things to look for:\n"
        f"  1. Where does avg return peak as cap tightens?\n"
        f"  2. Does worst-week worsen as cap tightens, or stay stable?\n"
        f"  3. How many fallback weeks occur at tight caps — if high,\n"
        f"     the programme is struggling to find enough picks below the cap.\n"
        f"  4. The best balanced cap avoids sacrificing worst-week\n"
        f"     for marginal return gains.[/dim]",
        title="[bold]Interpretation[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
