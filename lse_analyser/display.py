"""
display.py
----------
All Rich table and panel rendering functions.
"""

from rich.table import Table
from rich.panel import Panel
from rich import box

from .config import TOP_N
from .utils import console


def _sentiment_colour(score, available):
    """Return a Rich colour tag and label for a sentiment score."""
    if not available:
        return "dim", "--"
    if score >= 0.35:  return "bright_green", "Very positive"
    if score >= 0.10:  return "green",         "Positive"
    if score >= -0.10: return "dim",            "Neutral"
    if score >= -0.35: return "yellow",         "Negative"
    return "red", "Very negative"


def print_results_table(top: list, cal: dict):
    """Print the main results table with company news and sector news columns."""
    cal_note = ""
    if cal["calibrated"] and abs(cal["prob_adjustment"]) >= 1.0:
        direction = "adjusted down" if cal["prob_adjustment"] > 0 else "adjusted up"
        cal_note  = (f"  [dim italic](probabilities {direction} "
                     f"{abs(cal['prob_adjustment']):.1f}pp by calibration)[/dim italic]")

    table = Table(
        title=f"\n[bold]Top {TOP_N} LSE Stocks -- 7-Day Outlook[/bold]{cal_note}",
        box=box.ROUNDED, show_lines=True, style="cyan", header_style="bold magenta",
    )
    table.add_column("Rank",        justify="center", style="bold white",  no_wrap=True)
    table.add_column("Ticker",      justify="center", style="bold yellow", no_wrap=True)
    table.add_column("Sector",      justify="left",   style="dim white",   no_wrap=True)
    table.add_column("Price (p)",   justify="right",  style="white")
    table.add_column("Target (p)",  justify="right",  style="green")
    table.add_column("Upside",      justify="right",  style="bright_green")
    table.add_column("Prob.",       justify="right",  style="bright_cyan")
    table.add_column("Stop (p)",    justify="right",  style="red")
    table.add_column("Limit (p)",   justify="right",  style="bright_yellow")
    table.add_column("R:R",         justify="center", style="magenta")
    table.add_column("Score",       justify="center", style="dim magenta")
    table.add_column("Co. News",    justify="left",   style="dim white",   no_wrap=True)
    table.add_column("Sector News", justify="left",   style="dim white",   no_wrap=True)

    for i, r in enumerate(top, 1):
        pc = "bright_green" if r["prob"] >= 60 else "yellow" if r["prob"] >= 45 else "red"

        # Company news
        news       = r.get("news", {})
        co_colour, co_label = _sentiment_colour(
            news.get("score", 0), news.get("available", False)
        )
        co_str = f"[{co_colour}]{co_label}[/{co_colour}]"

        # Sector news
        sec_news   = r.get("sector_news", {})
        sec_colour, sec_label = _sentiment_colour(
            sec_news.get("score", 0), sec_news.get("available", False)
        )
        sec_str = f"[{sec_colour}]{sec_label}[/{sec_colour}]"

        table.add_row(
            str(i), r["ticker"], r["sector"],
            f"{r['price']:,.2f}", f"{r['target']:,.2f}",
            f"+{r['upside_pct']:.1f}%",
            f"[{pc}]{r['prob']:.0f}%[/{pc}]",
            f"{r['stop']:,.2f}", f"{r['limit']:,.2f}",
            f"{r['reward_risk']:.2f}", str(r["score"]),
            co_str, sec_str,
        )
    console.print(table)


def print_sizing_table(top: list, total_capital: float):
    """Print the position sizing table and capital summary."""
    size_table = Table(
        title="[bold]Suggested Position Sizing[/bold]",
        box=box.SIMPLE_HEAVY, style="cyan", header_style="bold magenta",
    )
    size_table.add_column("Ticker",       justify="center", style="bold yellow")
    size_table.add_column("Probability",  justify="right",  style="bright_cyan")
    size_table.add_column("Allocation %", justify="right",  style="green")
    size_table.add_column("Invest (£)",   justify="right",  style="bright_green")
    size_table.add_column("Price (£)",    justify="right",  style="white")
    size_table.add_column("~Shares",      justify="right",  style="dim white")
    size_table.add_column("Note",         style="dim white")

    for r in top:
        price_gbp = r["price"] / 100
        if r["allocated_gbp"] == 0:
            note       = "Below confidence threshold -- skip"
            shares_str = "--"
            invest_str = "£0.00"
        else:
            invest_str = f"£{r['allocated_gbp']:,.2f}"
            if r["prob"] >= 60:
                signal_note = "Strong signal -- favoured"
            elif r["prob"] >= 50:
                signal_note = "Moderate signal"
            else:
                signal_note = "Weak signal -- small stake only"
            if r["shares"] == 0:
                shares_str = f"<1  (1 share = £{price_gbp:,.2f})"
                note       = f"{signal_note}  · fractional share"
            else:
                shares_str = f"~{r['shares']}"
                note       = signal_note

        size_table.add_row(
            r["ticker"], f"{r['prob']:.0f}%", f"{r['allocation_pct']:.1f}%",
            invest_str, f"£{price_gbp:,.2f}", shares_str, note,
        )

    console.print(size_table)
    total_suggested = sum(r["allocated_gbp"] for r in top)
    reserve_shown   = round(total_capital - total_suggested, 2)
    console.print(
        f"\n  [bold]Capital summary:[/bold]  "
        f"Total entered: [cyan]£{total_capital:,.2f}[/cyan]  |  "
        f"Suggested to deploy: [green]£{total_suggested:,.2f}[/green]  |  "
        f"Keep in reserve: [yellow]£{reserve_shown:,.2f}[/yellow]\n"
    )


def print_signal_breakdown(top: list, skipped_events: list):
    """Print the per-stock signal breakdown including news headlines."""
    console.print("[bold]Detailed signal breakdown:[/bold]\n")
    for i, r in enumerate(top, 1):
        console.print(
            f"  [bold yellow]{i}. {r['ticker']}[/bold yellow]  [dim]({r['sector']})[/dim]"
        )
        for sig in r["signals"]:
            console.print(f"     [dim]•[/dim] {sig}")
        console.print(
            f"     [dim]ATR(14) = {r['atr']:.2f}p  |  "
            f"Stop is {r['downside_pct']:.1f}% below entry  |  "
            f"Reward:Risk = {r['reward_risk']:.2f}[/dim]"
        )
        news = r.get("news", {})
        if news.get("available") and news.get("headlines"):
            console.print("     [dim]Company headlines:[/dim]")
            for h in news["headlines"]:
                console.print(f"       [dim]-[/dim] [dim italic]{h}[/dim italic]")
        console.print()

    if skipped_events:
        console.print(
            f"[dim]Skipped (event within 7 days): {', '.join(skipped_events)}[/dim]\n"
        )


def print_macro_table(macro: dict, sector_cache: dict, top: list):
    """
    Print the macro and sector news summary table.
    Shows market-wide sentiment plus one row per sector in the final picks.
    """
    console.print("[bold]Macro & Sector News Summary[/bold]\n")

    table = Table(
        box=box.ROUNDED, show_lines=True, style="cyan", header_style="bold magenta",
    )
    table.add_column("Level",      justify="left",  style="bold white",  no_wrap=True)
    table.add_column("Sentiment",  justify="left",  no_wrap=True)
    table.add_column("Event",      justify="left",  style="dim white")
    table.add_column("Key Headlines", justify="left", style="dim italic")

    # Market-wide row
    if macro.get("available"):
        mc, _ = _sentiment_colour(macro["score"], True)
        event_str = macro.get("event_label", "General conditions")
        headlines_str = "  /  ".join(macro.get("headlines", [])[:3])
        table.add_row(
            "Market-wide",
            f"[{mc}]{macro['label']}[/{mc}]",
            event_str,
            headlines_str or "--",
        )
    else:
        table.add_row("Market-wide", "[dim]No data[/dim]", "--", "--")

    # One row per sector in the final top picks
    seen_sectors = []
    for r in top:
        sector = r["sector"]
        if sector in seen_sectors:
            continue
        seen_sectors.append(sector)

        sec = sector_cache.get(sector, {})
        sc, _ = _sentiment_colour(sec.get("score", 0), sec.get("available", False))
        headlines_str = "  /  ".join(sec.get("headlines", [])[:3])

        table.add_row(
            sector,
            f"[{sc}]{sec.get('label', 'No data')}[/{sc}]",
            "--",
            headlines_str or "[dim]No headlines found[/dim]",
        )

    console.print(table)
    console.print()


def print_disclaimer():
    """Print the how-it-works and disclaimer panel."""
    console.print(Panel(
        "[dim]"
        "[bold]How the self-calibration works:[/bold]\n"
        "  Each run looks back at picks from 7+ days ago, fetches actual closing\n"
        "  prices, and records whether the target was hit. Once 10+ outcomes exist,\n"
        "  it compares predicted probabilities against the real hit rate and adjusts\n"
        "  today's outputs accordingly.\n\n"
        "[bold]How news & macro sentiment works:[/bold]\n"
        "  Company news: recent headlines scored per stock using VADER sentiment.\n"
        "  Macro: market-wide headlines classified by event type (geopolitical,\n"
        "  recession, inflation, currency). Each sector has a defined sensitivity\n"
        "  to each event type -- Energy and Mining benefit from geopolitical events\n"
        "  while Tech and Leisure are penalised. Sector-specific headlines add a\n"
        "  further layer. If a sector has very negative news, the screener tries\n"
        "  to replace that pick with a better alternative.\n\n"
        "[bold]Column guide:[/bold]\n"
        "  Price / Target / Stop / Limit  All in PENCE (divide by 100 for £)\n"
        "  R:R          Reward:Risk ratio (aim >= 1.5)\n"
        "  Score        Technical score + news adjustment\n"
        "  Co. News     Sentiment of company-specific headlines\n"
        "  Sector News  Sentiment of sector-wide headlines\n"
        "  Invest (£)   Suggested amount -- the primary sizing guide\n"
        "  Prob.        Calibration + macro adjusted probability\n\n"
        "[bold red]Disclaimer:[/bold red] Quantitative screening tool only -- NOT financial\n"
        "advice. All probabilities are model estimates. Past patterns do not\n"
        "guarantee future results. Consult a regulated adviser before trading.[/dim]",
        title="[bold]How It Works & Disclaimer[/bold]",
        box=box.ROUNDED,
    ))
