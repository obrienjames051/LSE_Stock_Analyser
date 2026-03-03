"""
main.py
-------
Entry point and run-mode orchestration for the LSE Analyser.
"""

import warnings
warnings.filterwarnings("ignore")

from datetime import datetime
from rich.panel import Panel
from rich import box

from .config import TOP_N
from .utils import console
from .tickers import get_tickers
from .screener import score_ticker, has_event_in_window, diversify
from .calibration import resolve_pending_outcomes, compute_calibration, print_performance_report
from .sizing import calculate_allocations, ask_for_capital
from .csv_log import save_to_csv
from .display import print_results_table, print_sizing_table, print_signal_breakdown, print_disclaimer
from .history import show_history


def ask_for_mode() -> str:
    console.print(
        "\n[bold cyan]Run mode[/bold cyan]\n"
        "[dim]  [L] Live mode    -- results saved to CSV log (use once a week)\n"
        "  [P] Preview mode -- results shown but nothing saved\n"
        "  [H] History      -- view a previous week's predictions and outcomes[/dim]\n"
    )
    while True:
        raw = input("  Choose mode (L / P / H): ").strip().upper()
        if raw in ("L", "LIVE"):      return "live"
        elif raw in ("P", "PREVIEW"): return "preview"
        elif raw in ("H", "HISTORY"): return "history"
        else: console.print("  [red]Please enter L, P, or H[/red]")


def main():
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode     = ask_for_mode()

    if mode == "history":
        show_history()
        return

    live_mode  = (mode == "live")
    mode_label = "[green]LIVE[/green]" if live_mode else "[yellow]PREVIEW[/yellow]"

    console.print(Panel(
        f"[bold cyan]LSE Stock Screener  v4.0[/bold cyan]\n"
        f"[dim]Run at {run_date}[/dim]  --  Mode: {mode_label}\n\n"
        "[dim]Features:  Volume filter  |  Sector diversification  |  "
        "Event filter  |  Auto-outcomes  |  Self-calibration[/dim]",
        box=box.ROUNDED,
    ))

    # Step 1: resolve pending outcomes
    if live_mode:
        resolve_pending_outcomes()
    else:
        console.print("[dim]Preview mode -- outcome resolution skipped.[/dim]\n")

    # Step 2: calibration
    cal = compute_calibration()
    print_performance_report(cal)

    # Step 3: fetch ticker universe
    with console.status("[bold green]Fetching FTSE constituent list..."):
        tickers = get_tickers()

    source        = getattr(get_tickers, "_source", f"{len(tickers)} stocks")
    source_colour = "green" if "live" in source else "yellow" if "cache" in source else "red"
    console.print(f"[dim]Ticker universe: [{source_colour}]{source}[/{source_colour}][/dim]\n")

    # Step 4: screen all tickers
    all_results, skipped_events = [], []

    with console.status("[bold green]Fetching data & scoring tickers...") as status:
        for i, (ticker, sector) in enumerate(tickers.items()):
            status.update(f"[bold green]Analysing {ticker} ({i+1}/{len(tickers)})...")

            has_event, event_reason = has_event_in_window(ticker)
            if has_event:
                skipped_events.append(f"{ticker.replace('.L', '')} ({event_reason})")
                continue

            r = score_ticker(ticker, sector, prob_adjustment=cal["prob_adjustment"])
            if r:
                all_results.append(r)

    if not all_results:
        console.print("[red]No data retrieved. Check your internet connection.[/red]")
        return

    all_results.sort(key=lambda x: x["score"], reverse=True)
    top = diversify(all_results, TOP_N)

    # Step 5: capital input and position sizing
    total_capital = ask_for_capital()
    top, deployed, reserve = calculate_allocations(top, total_capital)

    # Step 6: display results
    print_results_table(top, cal)
    print_sizing_table(top, total_capital)
    print_signal_breakdown(top, skipped_events)

    # Step 7: save or notify
    if live_mode:
        save_to_csv(top, run_date)
    else:
        console.print(
            "[yellow]⏭  Preview mode -- results not saved to CSV.[/yellow]\n"
            "[dim]Run in Live mode (L) to log this week's picks.[/dim]\n"
        )

    print_disclaimer()
