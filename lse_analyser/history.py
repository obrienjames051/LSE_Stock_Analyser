"""
history.py
----------
History viewer mode -- lets the user browse past runs and their outcomes.
"""

import os
import csv

from rich.table import Table
from rich.panel import Panel
from rich import box

from .config import CSV_FILE
from .utils import console


def load_history() -> dict:
    """Read the CSV and group rows by run_date."""
    if not os.path.isfile(CSV_FILE):
        return {}
    runs = {}
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rd = row.get("run_date", "").strip()
            if rd:
                runs.setdefault(rd, []).append(row)
    return runs


def show_history():
    """Let the user pick a past run and display its predictions and outcomes."""
    runs = load_history()
    if not runs:
        console.print(Panel(
            "[yellow]No history found.[/yellow]\n"
            "[dim]Run in Live mode first to start building a history.[/dim]",
            title="History", box=box.ROUNDED,
        ))
        return

    run_dates = list(runs.keys())
    console.print("\n[bold cyan]Previous runs[/bold cyan]\n")

    for i, rd in enumerate(run_dates, 1):
        rows     = runs[rd]
        n_picks  = len(rows)
        resolved = [r for r in rows if r.get("outcome_hit", "").strip() in ("YES", "NO")]
        hits     = sum(1 for r in resolved if r.get("outcome_hit") == "YES")

        if not resolved:
            status_str = "[dim]all pending[/dim]"
        elif len(resolved) < n_picks:
            status_str = f"[dim]{len(resolved)} resolved[/dim]"
        else:
            hc = "bright_green" if hits >= n_picks / 2 else "yellow"
            status_str = f"[{hc}]{hits}/{n_picks} targets hit[/{hc}]"

        console.print(f"  [bold white]{i:>2}.[/bold white]  "
                      f"[yellow]{rd}[/yellow]  --  {n_picks} picks  --  {status_str}")

    console.print()
    while True:
        try:
            raw    = input(f"  Enter number (1-{len(run_dates)}): ").strip()
            choice = int(raw)
            if 1 <= choice <= len(run_dates):
                break
            raise ValueError
        except ValueError:
            console.print(f"  [red]Please enter a number between 1 and {len(run_dates)}[/red]")

    selected_date = run_dates[choice - 1]
    rows          = runs[selected_date]

    table = Table(
        title=f"[bold]Predictions for {selected_date}[/bold]",
        box=box.ROUNDED, show_lines=True, style="cyan", header_style="bold magenta",
    )
    table.add_column("Ticker",        justify="center", style="bold yellow",  no_wrap=True)
    table.add_column("Sector",        justify="left",   style="dim white",    no_wrap=True)
    table.add_column("Price (p)",     justify="right",  style="white")
    table.add_column("Target (p)",    justify="right",  style="green")
    table.add_column("Pred. Upside",  justify="right",  style="bright_green")
    table.add_column("Prob.",         justify="right",  style="bright_cyan")
    table.add_column("Stop (p)",      justify="right",  style="red")
    table.add_column("Limit (p)",     justify="right",  style="bright_yellow")
    table.add_column("R:R",           justify="center", style="magenta")
    table.add_column("Score",         justify="center", style="dim magenta")
    table.add_column("Actual (p)",    justify="right",  style="white")
    table.add_column("Actual Return", justify="right",  style="white")
    table.add_column("Hit?",          justify="center", style="bold white")

    for r in rows:
        hit       = r.get("outcome_hit", "").strip()
        out_price = r.get("outcome_price_p", "").strip()
        out_ret   = r.get("outcome_return_pct", "").strip()

        if hit == "YES":
            hit_str   = "[bright_green]YES ✔[/bright_green]"
            ret_str   = f"[bright_green]+{float(out_ret):.1f}%[/bright_green]" if out_ret else "--"
            price_str = f"{float(out_price):,.2f}" if out_price else "--"
        elif hit == "NO":
            ret_val   = float(out_ret) if out_ret else 0
            rc        = "red" if ret_val < 0 else "yellow"
            sign      = "+" if ret_val >= 0 else ""
            hit_str   = "[red]NO ✘[/red]"
            ret_str   = f"[{rc}]{sign}{ret_val:.1f}%[/{rc}]" if out_ret else "--"
            price_str = f"{float(out_price):,.2f}" if out_price else "--"
        else:
            hit_str = price_str = ret_str = "[dim]Pending[/dim]"

        try:
            prob_val = float(r.get("prob", 0))
            pc = "bright_green" if prob_val >= 60 else "yellow" if prob_val >= 45 else "red"
            prob_str = f"[{pc}]{prob_val:.0f}%[/{pc}]"
        except ValueError:
            prob_str = r.get("prob", "--")

        table.add_row(
            r.get("ticker", ""), r.get("sector", ""),
            r.get("price_p", ""), r.get("target_p", ""),
            f"+{float(r['upside_pct']):.1f}%" if r.get("upside_pct") else "--",
            prob_str,
            r.get("stop_p", ""), r.get("limit_p", ""),
            r.get("reward_risk", ""), r.get("score", ""),
            price_str, ret_str, hit_str,
        )

    console.print()
    console.print(table)

    resolved = [r for r in rows if r.get("outcome_hit", "").strip() in ("YES", "NO")]
    if resolved:
        hits    = sum(1 for r in resolved if r["outcome_hit"] == "YES")
        returns = [float(r["outcome_return_pct"]) for r in resolved
                   if r.get("outcome_return_pct", "").strip()]
        avg_ret = sum(returns) / len(returns) if returns else 0
        rc      = "bright_green" if avg_ret >= 0 else "red"
        sign    = "+" if avg_ret >= 0 else ""
        console.print(
            f"\n  [bold]Run summary:[/bold]  "
            f"Targets hit: [bright_green]{hits}/{len(resolved)}[/bright_green]  |  "
            f"Avg return: [{rc}]{sign}{avg_ret:.2f}%[/{rc}]\n"
        )
    else:
        console.print("\n  [dim]No outcomes resolved yet for this run.[/dim]\n")
