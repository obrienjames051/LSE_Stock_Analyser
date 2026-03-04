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

from .config import (
    TOP_N, NEWS_CANDIDATE_COUNT, NEWS_FALLBACK_BATCH,
    SECTOR_REPLACE_THRESHOLD,
)
from .utils import console
from .tickers import get_tickers
from .screener import score_ticker, has_event_in_window, diversify
from .calibration import resolve_pending_outcomes, compute_calibration, print_performance_report
from .sizing import calculate_allocations, ask_for_capital
from .csv_log import save_to_csv
from .display import (
    print_results_table, print_sizing_table, print_signal_breakdown,
    print_macro_table, print_disclaimer,
)
from .history import show_history
from .news import fetch_news_sentiment, apply_news_adjustment
from .macro import (
    fetch_macro_sentiment, fetch_sector_sentiment,
    apply_macro_to_pick, check_macro_warning, sector_needs_replacement,
)


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


def _run_company_news(candidates: list) -> list:
    """Fetch and apply company-specific news sentiment to a list of candidates."""
    with console.status("[bold green]Fetching company news...") as status:
        for r in candidates:
            status.update(f"[bold green]Fetching company news for {r['ticker']}...")
            sentiment = fetch_news_sentiment(r["ticker"] + ".L")
            apply_news_adjustment(r, sentiment)
    return candidates


def _run_sector_news(candidates: list, macro: dict) -> list:
    """
    Fetch sector sentiment for each unique sector in the candidate list
    and apply macro + sector adjustments to each pick.
    Caches sector fetches so each sector is only searched once.
    """
    sector_cache = {}
    with console.status("[bold green]Fetching sector news...") as status:
        for r in candidates:
            sector = r["sector"]
            if sector not in sector_cache:
                status.update(f"[bold green]Fetching sector news for {sector}...")
                sector_cache[sector] = fetch_sector_sentiment(sector)
            apply_macro_to_pick(r, macro, sector_cache[sector])
    return candidates, sector_cache


def _select_picks(all_results: list, macro: dict) -> tuple:
    """
    Select the final TOP_N picks using a three-layer approach:
      1. Technical score (pre-computed for all candidates)
      2. Company news sentiment
      3. Macro + sector sentiment

    Selection logic:
      - Start with top NEWS_CANDIDATE_COUNT technically scored candidates
      - Run company news and sector sentiment on them
      - Apply diversification
      - If any picks have negative sector sentiment, try to replace just
        those picks with the next best candidates (surgical replacement)
      - If replacement candidates also have bad sector news, accept the
        best available rather than leaving a slot empty
      - If overall pool is too thin, expand in NEWS_FALLBACK_BATCH increments

    Returns (top_picks, sector_cache) where sector_cache holds all sector
    sentiment results for display in the macro table.
    """
    evaluated    = []
    sector_cache = {}
    batch_end    = min(NEWS_CANDIDATE_COUNT, len(all_results))

    while True:
        # Fetch news for any candidates not yet evaluated
        new_batch = all_results[len(evaluated):batch_end]
        if not new_batch:
            break

        _run_company_news(new_batch)

        # Fetch sector news for new sectors in this batch
        with console.status("[bold green]Fetching sector news...") as status:
            for r in new_batch:
                sector = r["sector"]
                if sector not in sector_cache:
                    status.update(f"[bold green]Fetching sector news: {sector}...")
                    sector_cache[sector] = fetch_sector_sentiment(sector)
                apply_macro_to_pick(r, macro, sector_cache[sector])

        evaluated.extend(new_batch)

        # Sort by final adjusted score and get best diversified set
        evaluated.sort(key=lambda x: x["score"], reverse=True)
        top = diversify(evaluated, TOP_N)

        # Identify picks with bad sector sentiment
        needs_replacement = [
            r for r in top
            if sector_needs_replacement(sector_cache.get(r["sector"], {}))
        ]

        if not needs_replacement:
            # All picks have acceptable sector sentiment
            return top, sector_cache

        # Try surgical replacement: keep good picks, find replacements for bad ones
        good_picks    = [r for r in top if r not in needs_replacement]
        replacements  = []

        for bad_pick in needs_replacement:
            # Find best candidate not already in good_picks or replacements,
            # preferring different sectors but accepting duplicates if needed
            current_tickers = {r["ticker"] for r in good_picks + replacements}
            current_sectors = {r["sector"] for r in good_picks + replacements}

            # First pass: different sector
            replacement = next(
                (r for r in evaluated
                 if r["ticker"] not in current_tickers
                 and r not in needs_replacement
                 and r["sector"] not in current_sectors
                 and not sector_needs_replacement(sector_cache.get(r["sector"], {}))),
                None
            )

            # Second pass: allow sector overlap
            if replacement is None:
                replacement = next(
                    (r for r in evaluated
                     if r["ticker"] not in current_tickers
                     and r not in needs_replacement
                     and not sector_needs_replacement(sector_cache.get(r["sector"], {}))),
                    None
                )

            # Third pass: accept bad sector if nothing better available
            if replacement is None:
                replacement = next(
                    (r for r in evaluated
                     if r["ticker"] not in current_tickers
                     and r not in needs_replacement),
                    None
                )

            if replacement:
                replacements.append(replacement)
                console.print(
                    f"[dim]Replacing {bad_pick['ticker']} "
                    f"(negative {bad_pick['sector']} sector news) "
                    f"with {replacement['ticker']}[/dim]"
                )

        final = good_picks + replacements

        # If we still don't have TOP_N, fill from remaining evaluated
        if len(final) < TOP_N:
            used = {r["ticker"] for r in final}
            for r in evaluated:
                if r["ticker"] not in used:
                    final.append(r)
                if len(final) == TOP_N:
                    break

        if len(final) >= TOP_N:
            return final[:TOP_N], sector_cache

        # Not enough candidates yet -- expand batch
        if batch_end >= len(all_results):
            break

        console.print(
            f"[dim]Expanding candidate pool to find replacements "
            f"(checked {batch_end}/{len(all_results)})...[/dim]"
        )
        batch_end = min(batch_end + NEWS_FALLBACK_BATCH, len(all_results))

    # Return best available
    evaluated.sort(key=lambda x: x["score"], reverse=True)
    return diversify(evaluated, TOP_N), sector_cache


def _print_macro_warning(macro: dict, warning_level: str):
    """Print macro warning panel if threshold is exceeded."""
    if warning_level == "ok":
        return

    if warning_level == "skip":
        console.print(Panel(
            f"[bold red]MACRO WARNING -- CONSIDER SKIPPING THIS WEEK[/bold red]\n\n"
            f"Market-wide sentiment is [red]very negative[/red] "
            f"(score: {macro['score']:+.2f}).\n"
            f"Event detected: [bold]{macro.get('event_label', 'General market stress')}[/bold]\n\n"
            f"[dim]The model has still produced picks below, but conditions suggest\n"
            f"elevated risk across the board. Review carefully before entering\n"
            f"any positions and consider sitting out this week.[/dim]",
            title="Market Warning",
            box=box.ROUNDED,
            style="red",
        ))
    elif warning_level == "warning":
        console.print(Panel(
            f"[bold yellow]MACRO CAUTION[/bold yellow]\n\n"
            f"Market-wide sentiment is [yellow]negative[/yellow] "
            f"(score: {macro['score']:+.2f}).\n"
            f"Event detected: [bold]{macro.get('event_label', 'General market weakness')}[/bold]\n\n"
            f"[dim]Sector sensitivities have been applied. Picks in defensive\n"
            f"sectors may be less affected. Review each pick's sector response\n"
            f"in the signal breakdown below.[/dim]",
            title="Market Caution",
            box=box.ROUNDED,
            style="yellow",
        ))
    console.print()


def main():
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode     = ask_for_mode()

    if mode == "history":
        show_history()
        return

    live_mode  = (mode == "live")
    mode_label = "[green]LIVE[/green]" if live_mode else "[yellow]PREVIEW[/yellow]"

    console.print(Panel(
        f"[bold cyan]LSE Stock Screener  v5.1[/bold cyan]\n"
        f"[dim]Run at {run_date}[/dim]  --  Mode: {mode_label}\n\n"
        "[dim]Features:  Volume filter  |  Sector diversification  |  Event filter\n"
        "          Company news  |  Macro & sector sentiment  |  "
        "Auto-outcomes  |  Self-calibration[/dim]",
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

    # Step 4: macro sentiment (runs once, before any stock analysis)
    console.print("[dim]Fetching macro market sentiment...[/dim]")
    macro         = fetch_macro_sentiment()
    warning_level = check_macro_warning(macro)

    if macro.get("available"):
        event_label = macro.get("event_label", "General conditions")
        console.print(
            f"[dim]Market sentiment: {macro['label']}  |  "
            f"{event_label}  |  score {macro['score']:+.2f}[/dim]\n"
        )
    else:
        console.print("[dim]Macro sentiment unavailable -- proceeding without it.[/dim]\n")

    # Step 5: technical screening of all tickers
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

    # Step 6: company news + sector sentiment + surgical replacement
    console.print(
        f"[dim]Running news & sector analysis on top candidates...[/dim]"
    )
    top, sector_cache = _select_picks(all_results, macro)

    # Step 7: print macro warning if needed (after picks are shown in context)
    _print_macro_warning(macro, warning_level)

    # Step 8: capital input and position sizing
    total_capital = ask_for_capital()
    top, deployed, reserve = calculate_allocations(top, total_capital)

    # Step 9: display results
    print_results_table(top, cal)
    print_sizing_table(top, total_capital)
    print_signal_breakdown(top, skipped_events)
    print_macro_table(macro, sector_cache, top)

    # Step 10: save or notify
    if live_mode:
        save_to_csv(top, run_date)
    else:
        console.print(
            "[yellow]Preview mode -- results not saved to CSV.[/yellow]\n"
            "[dim]Run in Live mode (L) to log this week's picks.[/dim]\n"
        )

    print_disclaimer()
