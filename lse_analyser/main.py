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

from .config import TOP_N, NEWS_CANDIDATE_COUNT, NEWS_FALLBACK_BATCH
from .utils import console
from .tickers import get_tickers
from .screener import score_ticker, has_event_in_window, diversify
from .calibration import resolve_pending_outcomes, compute_calibration, print_performance_report
from .sizing import calculate_allocations, ask_for_capital
from .csv_log import save_to_csv
from .display import print_results_table, print_sizing_table, print_signal_breakdown, print_disclaimer
from .history import show_history
from .news import fetch_news_sentiment, apply_news_adjustment


def ask_for_mode() -> str:
    console.print(
        "\n[bold cyan]Run mode[/bold cyan]\n"
        "[dim]  [L] Live mode    -- results saved to CSV log (use once a week)\n"
        "  [P] Preview mode -- results shown but nothing saved\n"
        "  [H] History      -- view a previous week's predictions and outcomes[/dim]\n"
    )
    while True:
        raw = input("  Choose mode (L / P / H): ").strip().upper()
        if raw in ("L", "LIVE"):       return "live"
        elif raw in ("P", "PREVIEW"):  return "preview"
        elif raw in ("H", "HISTORY"):  return "history"
        else: console.print("  [red]Please enter L, P, or H[/red]")


def _run_news_on_candidates(candidates: list) -> list:
    """
    Run news sentiment analysis on a list of scored candidates.
    Returns the candidates with news adjustments applied.
    """
    with console.status("[bold green]Fetching news sentiment...") as status:
        for r in candidates:
            status.update(f"[bold green]Fetching news for {r['ticker']}...")
            sentiment = fetch_news_sentiment(r["ticker"] + ".L")
            apply_news_adjustment(r, sentiment)
    return candidates


def _select_top_with_news(all_results: list) -> list:
    """
    Select the final TOP_N picks using technical scoring plus news sentiment,
    with an expanding fallback if sentiment knocks too many candidates out.

    Logic:
      1. Take the top NEWS_CANDIDATE_COUNT technically scored candidates
      2. Run news sentiment on them
      3. Apply diversification to the news-adjusted scores
      4. If fewer than TOP_N remain with non-negative sentiment, expand by
         NEWS_FALLBACK_BATCH and repeat until we have enough or exhaust the list
    """
    evaluated  = []
    batch_end  = NEWS_CANDIDATE_COUNT

    while len(evaluated) < len(all_results):
        batch     = all_results[len(evaluated):batch_end]
        if not batch:
            break

        batch     = _run_news_on_candidates(batch)
        evaluated.extend(batch)

        # Re-sort by adjusted score and diversify
        evaluated.sort(key=lambda x: x["score"], reverse=True)
        top = diversify(evaluated, TOP_N)

        # Count picks with neutral or better sentiment
        viable = [r for r in top
                  if not r.get("news", {}).get("available", False)
                  or r.get("news", {}).get("score", 0) >= -0.10]

        if len(viable) >= TOP_N:
            return top

        # Not enough viable picks -- expand the batch and try again
        console.print(
            f"[dim]Only {len(viable)} picks with acceptable sentiment -- "
            f"checking next {NEWS_FALLBACK_BATCH} candidates...[/dim]"
        )
        batch_end += NEWS_FALLBACK_BATCH

    # Return best available even if we couldn't fill TOP_N with good sentiment
    evaluated.sort(key=lambda x: x["score"], reverse=True)
    return diversify(evaluated, TOP_N)


def main():
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode     = ask_for_mode()

    if mode == "history":
        show_history()
        return

    live_mode  = (mode == "live")
    mode_label = "[green]LIVE[/green]" if live_mode else "[yellow]PREVIEW[/yellow]"

    console.print(Panel(
        f"[bold cyan]LSE Stock Screener  v5.0[/bold cyan]\n"
        f"[dim]Run at {run_date}[/dim]  --  Mode: {mode_label}\n\n"
        "[dim]Features:  Volume filter  |  Sector diversification  |  "
        "Event filter  |  News sentiment  |  Auto-outcomes  |  Self-calibration[/dim]",
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
    source_colour = getattr(get_tickers, "_source_colour", "yellow")
    console.print(f"[dim]Ticker universe: [{source_colour}]{source}[/{source_colour}][/dim]\n")

    # Step 4: technical screening of all tickers
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

    # Step 5: news sentiment on top candidates, with expanding fallback
    console.print(
        f"[dim]Running news sentiment on top {min(NEWS_CANDIDATE_COUNT, len(all_results))} candidates...[/dim]"
    )
    top = _select_top_with_news(all_results)

    # Step 6: capital input and position sizing
    total_capital = ask_for_capital()
    top, deployed, reserve = calculate_allocations(top, total_capital)

    # Step 7: display results
    print_results_table(top, cal)
    print_sizing_table(top, total_capital)
    print_signal_breakdown(top, skipped_events)

    # Step 8: save or notify
    if live_mode:
        save_to_csv(top, run_date)
    else:
        console.print(
            "[yellow]Preview mode -- results not saved to CSV.[/yellow]\n"
            "[dim]Run in Live mode (L) to log this week's picks.[/dim]\n"
        )

    print_disclaimer()
