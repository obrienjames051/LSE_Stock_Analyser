"""
analyse_worst_week.py
---------------------
Investigates the worst week in Strategy E (cap 85, diversify-then-cap)
which produced a -6.078% average return.

For that week, shows:
  1. Which 5 picks were selected under Strategy E — ticker, sector,
     score, whether it was a cap replacement, and individual return
  2. What the no-cap picks would have been that week — so we can
     compare replacement performance vs original over-extended picks
  3. FTSE 100 return that same week — market-wide context
  4. Whether other strategies also had a bad week at the same time

Run from LSE_Stock_Analyser folder:
    python analyse_worst_week.py
"""

import sys
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

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


CAP = 85
POOL_SIZE = 15  # 3x TOP_N


# ── Selection helpers ─────────────────────────────────────────────────────────

def select_no_cap(week_scores):
    sorted_scores = sorted(week_scores, key=lambda x: x["score"], reverse=True)
    return sector_diversify(sorted_scores, TOP_N)


def select_div_then_cap(week_scores, cap=CAP, pool_size=POOL_SIZE):
    """Strategy E: diversify to pool, then remove cap+ picks."""
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


# ── Full backtest to find worst week ─────────────────────────────────────────

def find_worst_week(tickers, price_data):
    """
    Run Strategy E over all weeks and return weekly avg returns
    so we can identify which week was worst.
    """
    end_monday   = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")
    weekly_data  = {}  # week_label -> list of resolved picks

    for week_offset in range(BACKTEST_WEEKS_TECHNICAL, 0, -1):
        sim_monday    = end_monday - timedelta(weeks=week_offset)
        entry_tuesday = sim_monday + timedelta(days=1)
        exit_monday   = sim_monday + timedelta(days=8)

        if exit_monday > end_monday:
            continue

        sim_monday_pd    = pd.Timestamp(sim_monday)
        entry_tuesday_pd = pd.Timestamp(entry_tuesday)
        exit_monday_pd   = pd.Timestamp(exit_monday)
        week_label       = sim_monday.strftime("%Y-%m-%d")

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

        top = select_div_then_cap(week_scores)

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
                    r["run_date"]    = week_label
                    r["week_label"]  = week_label
                    r["was_capped"]  = r["score"] >= CAP  # fallback pick
                    week_resolved.append(r)
            except Exception:
                continue

        if week_resolved:
            calculate_allocations(week_resolved, BACKTEST_CAPITAL)
            weekly_data[week_label] = week_resolved

    return weekly_data


def run_no_cap_week(tickers, price_data, worst_monday_str):
    """Re-run just the worst week under no-cap to get comparison picks."""
    end_monday    = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")
    sim_monday    = datetime.strptime(worst_monday_str, "%Y-%m-%d")
    entry_tuesday = sim_monday + timedelta(days=1)
    exit_monday   = sim_monday + timedelta(days=8)

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

    top = select_no_cap(week_scores)

    resolved = []
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
                resolved.append(r)
        except Exception:
            continue

    return resolved


def get_ftse_return(entry_tuesday_str, exit_monday_str):
    """Fetch FTSE 100 return for the same entry/exit window."""
    try:
        entry = datetime.strptime(entry_tuesday_str, "%Y-%m-%d")
        exit_ = datetime.strptime(exit_monday_str,   "%Y-%m-%d")

        # Add buffer days around the window for data availability
        fetch_start = (entry - timedelta(days=5)).strftime("%Y-%m-%d")
        fetch_end   = (exit_ + timedelta(days=5)).strftime("%Y-%m-%d")

        ftse = yf.download(
            "^FTSE", start=fetch_start, end=fetch_end,
            interval="1d", progress=False, auto_adjust=True
        )
        if ftse is None or len(ftse) < 2:
            return None

        ftse.columns = [
            c[0].lower() if isinstance(c, tuple) else c.lower()
            for c in ftse.columns
        ]

        entry_pd = pd.Timestamp(entry)
        exit_pd  = pd.Timestamp(exit_)

        # Find closest available bars
        entry_bars = ftse[ftse.index >= entry_pd]
        exit_bars  = ftse[ftse.index <= exit_pd]

        if entry_bars.empty or exit_bars.empty:
            return None

        entry_open  = float(entry_bars.iloc[0]["open"])
        exit_close  = float(exit_bars.iloc[-1]["close"])

        return round((exit_close - entry_open) / entry_open * 100, 3)

    except Exception as e:
        console.print(f"[dim]FTSE fetch error: {e}[/dim]")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold cyan]Worst Week Analysis — Strategy E (Cap 85, div→cap)[/bold cyan]")
    console.print(
        "\n[dim]Identifies the worst week in Strategy E and investigates:\n"
        "  1. Which picks were selected and their individual returns\n"
        "  2. Whether picks were cap replacements (score < 85) or regular\n"
        "  3. What the no-cap picks would have returned that week\n"
        "  4. FTSE 100 return that week — market context\n"
        "  5. Weekly return distribution — was this week an outlier?\n\n"
        "  Requires a fresh price data download (~10-15 minutes).[/dim]\n"
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

    # ── Run Strategy E ─────────────────────────────────────────────────────────
    console.print("[bold]Running Strategy E to find worst week...[/bold]")
    weekly_data = find_worst_week(tickers, price_data)

    weekly_avgs = {
        week: sum(float(p["outcome_return_pct"]) for p in picks) / len(picks)
        for week, picks in weekly_data.items()
        if picks
    }

    if not weekly_avgs:
        console.print("[red]No weekly data found.[/red]")
        return

    worst_week   = min(weekly_avgs, key=weekly_avgs.get)
    worst_return = round(weekly_avgs[worst_week], 3)
    worst_picks  = weekly_data[worst_week]

    console.print(f"\n[bold]Worst week identified: [red]{worst_week}[/red] "
                  f"(avg return: [red]{worst_return:+.3f}%[/red])[/bold]\n")

    # ── Section 1: Pick detail ─────────────────────────────────────────────────
    console.rule("[bold]1. Strategy E Picks That Week[/bold]")

    pick_table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    pick_table.add_column("Ticker",        width=10)
    pick_table.add_column("Sector",        width=14)
    pick_table.add_column("Score",         justify="right", width=7)
    pick_table.add_column("Replacement?",  justify="center", width=13)
    pick_table.add_column("Return",        justify="right", width=10)
    pick_table.add_column("Profitable",    justify="center", width=11)
    pick_table.add_column("Went up",       justify="center", width=10)
    pick_table.add_column("Notes",         width=20)

    for p in sorted(worst_picks, key=lambda x: float(x.get("outcome_return_pct", 0))):
        ret  = float(p.get("outcome_return_pct", 0))
        rc   = "bright_green" if ret >= 0 else "red"
        repl = "[yellow]Yes (cap repl)[/yellow]" if p.get("was_capped") else "[dim]No[/dim]"
        pick_table.add_row(
            p.get("ticker", ""),
            p.get("sector", ""),
            str(p.get("score", "")),
            repl,
            f"[{rc}]{ret:+.2f}%[/{rc}]",
            "✓" if str(p.get("profitable", "")) == "1" else "✗",
            "✓" if str(p.get("went_up", "")) == "1" else "✗",
            str(p.get("outcome_notes", ""))[:20],
        )

    console.print(pick_table)

    # Split by cap replacement vs regular
    replacements = [p for p in worst_picks if p.get("was_capped")]
    regulars     = [p for p in worst_picks if not p.get("was_capped")]

    if replacements:
        rep_avg = sum(float(p["outcome_return_pct"]) for p in replacements) / len(replacements)
        reg_avg = sum(float(p["outcome_return_pct"]) for p in regulars) / len(regulars) if regulars else 0
        console.print(Panel(
            f"  Regular picks (score < {CAP}):     {len(regulars)} picks  "
            f"avg return [{'bright_green' if reg_avg >= 0 else 'red'}]{reg_avg:+.3f}%[/{'bright_green' if reg_avg >= 0 else 'red'}]\n"
            f"  Replacement picks (score >= {CAP}): {len(replacements)} picks  "
            f"avg return [{'bright_green' if rep_avg >= 0 else 'red'}]{rep_avg:+.3f}%[/{'bright_green' if rep_avg >= 0 else 'red'}]\n\n"
            f"  [dim]Replacements are picks that would have been capped but were\n"
            f"  included because the pool ran out of eligible picks below {CAP}.[/dim]",
            title="[bold]Replacement vs Regular Pick Returns[/bold]",
            box=box.ROUNDED,
        ))
    else:
        console.print("[dim]No cap replacements were needed this week — "
                      "all 5 picks scored below the cap.[/dim]\n")

    # ── Section 2: No-cap comparison ──────────────────────────────────────────
    console.rule("[bold]2. No-Cap Picks That Same Week (Comparison)[/bold]")
    console.print("[dim]What would the programme have picked without any cap?[/dim]\n")

    entry_tuesday = (datetime.strptime(worst_week, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    exit_monday   = (datetime.strptime(worst_week, "%Y-%m-%d") + timedelta(days=8)).strftime("%Y-%m-%d")

    no_cap_picks = run_no_cap_week(tickers, price_data, worst_week)

    if no_cap_picks:
        no_cap_avg = sum(float(p["outcome_return_pct"]) for p in no_cap_picks) / len(no_cap_picks)

        nc_table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
        nc_table.add_column("Ticker",   width=10)
        nc_table.add_column("Sector",   width=14)
        nc_table.add_column("Score",    justify="right", width=7)
        nc_table.add_column("Return",   justify="right", width=10)
        nc_table.add_column("Notes",    width=20)

        for p in sorted(no_cap_picks, key=lambda x: float(x.get("outcome_return_pct", 0))):
            ret = float(p.get("outcome_return_pct", 0))
            rc  = "bright_green" if ret >= 0 else "red"
            nc_table.add_row(
                p.get("ticker", ""),
                p.get("sector", ""),
                str(p.get("score", "")),
                f"[{rc}]{ret:+.2f}%[/{rc}]",
                str(p.get("outcome_notes", ""))[:20],
            )

        console.print(nc_table)

        nc = "bright_green" if no_cap_avg >= 0 else "red"
        wc = "bright_green" if worst_return >= 0 else "red"
        diff = round(worst_return - no_cap_avg, 3)
        dc   = "bright_green" if diff >= 0 else "red"

        console.print(Panel(
            f"  Strategy E avg return:   [{wc}]{worst_return:+.3f}%[/{wc}]\n"
            f"  No-cap avg return:       [{nc}]{no_cap_avg:+.3f}%[/{nc}]\n"
            f"  Difference (E vs no-cap): [{dc}]{diff:+.3f}%[/{dc}]\n\n"
            + (
                f"  [bright_green]Strategy E outperformed no-cap even in its worst week.[/bright_green]"
                if diff >= 0 else
                f"  [yellow]No-cap would have done better this week.[/yellow]\n"
                f"  [dim]Check whether no-cap picks scored 90+ (over-extended) or\n"
                f"  whether the replacement picks genuinely underperformed.[/dim]"
            ),
            title="[bold]E vs No-Cap This Week[/bold]",
            box=box.ROUNDED,
        ))

    # ── Section 3: FTSE context ────────────────────────────────────────────────
    console.rule("[bold]3. Market Context — FTSE 100[/bold]")
    console.print("[dim]Was this a market-wide down week or pick-specific?[/dim]\n")

    ftse_ret = get_ftse_return(entry_tuesday, exit_monday)

    if ftse_ret is not None:
        fc = "bright_green" if ftse_ret >= 0 else "red"
        market_driven = ftse_ret <= -2.0

        console.print(Panel(
            f"  FTSE 100 return (Tue open → Mon close): [{fc}]{ftse_ret:+.3f}%[/{fc}]\n"
            f"  Strategy E return that week:             [red]{worst_return:+.3f}%[/red]\n"
            f"  Difference (E vs FTSE):                  "
            f"{round(worst_return - ftse_ret, 3):+.3f}%\n\n"
            + (
                "[yellow]Market-driven week — FTSE was also significantly down.\n"
                "  The strategy's loss is largely explained by broad market conditions,\n"
                "  not by the cap or replacement picks specifically.[/yellow]"
                if market_driven else
                "[dim]FTSE was not significantly down this week.\n"
                "  The strategy's loss was pick-specific rather than market-driven.[/dim]"
            ),
            title="[bold]FTSE 100 Context[/bold]",
            box=box.ROUNDED,
        ))
    else:
        console.print("[yellow]Could not fetch FTSE 100 data for this period.[/yellow]\n")

    # ── Section 4: Weekly distribution ────────────────────────────────────────
    console.rule("[bold]4. Weekly Return Distribution — Is This Week an Outlier?[/bold]")

    avgs   = sorted(weekly_avgs.values())
    mean   = sum(avgs) / len(avgs)
    std    = statistics.stdev(avgs)
    z_score = (worst_return - mean) / std if std else 0

    dist_table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    dist_table.add_column("Percentile",   width=14)
    dist_table.add_column("Weekly return", justify="right", width=16)

    percentiles = [10, 25, 50, 75, 90]
    for pct in percentiles:
        idx = int(len(avgs) * pct / 100)
        val = avgs[min(idx, len(avgs)-1)]
        dist_table.add_row(f"{pct}th", f"{val:+.3f}%")

    console.print(dist_table)

    weeks_worse = sum(1 for v in avgs if v <= worst_return)
    console.print(Panel(
        f"  Total weeks:        {len(avgs)}\n"
        f"  Mean weekly return: {mean:+.3f}%\n"
        f"  Std dev (weekly):   {std:.3f}%\n"
        f"  Worst week return:  [red]{worst_return:+.3f}%[/red]\n"
        f"  Z-score:            {z_score:.2f}  "
        f"({'extreme outlier' if abs(z_score) > 2.5 else 'notable but within normal range' if abs(z_score) > 1.5 else 'within normal range'})\n"
        f"  Weeks at or below:  {weeks_worse} of {len(avgs)} "
        f"({'once-per-year event' if weeks_worse <= 2 else 'occasional occurrence'})",
        title="[bold]Distribution Summary[/bold]",
        box=box.ROUNDED,
    ))

    # ── Final summary ──────────────────────────────────────────────────────────
    console.rule("[bold]Summary[/bold]")
    console.print(Panel(
        f"  Worst week date:    [bold]{worst_week}[/bold]\n"
        f"  Strategy E return:  [red]{worst_return:+.3f}%[/red]\n"
        f"  FTSE 100 return:    {f'{ftse_ret:+.3f}%' if ftse_ret is not None else 'unavailable'}\n"
        f"  No-cap return:      {f'{no_cap_avg:+.3f}%' if no_cap_picks else 'unavailable'}\n"
        f"  Z-score:            {z_score:.2f}\n\n"
        f"  [dim]Use this to determine whether implementing the cap 85 div→cap\n"
        f"  strategy introduces genuine new risk, or whether the worst week\n"
        f"  was a market event that any strategy would have suffered.[/dim]",
        title="[bold]Conclusions[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
