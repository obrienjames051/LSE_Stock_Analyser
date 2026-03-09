"""
display.py
----------
All Rich table and panel rendering functions.
"""

from rich.table import Table
from rich.panel import Panel
from rich import box

from .config import TOP_N, PROB_STRONG, PROB_MODERATE, PROB_CAUTIOUS
from .sizing import signal_label
from .utils import console


def _sentiment_colour(score, available):
    """Return a Rich colour tag and label for a sentiment score."""
    if not available:
        return "white", "No data"
    if score >= 0.35:  return "bright_green", "Very positive"
    if score >= 0.10:  return "green",         "Positive"
    if score >= -0.10: return "white",          "Neutral"
    if score >= -0.35: return "yellow",         "Negative"
    return "red", "Very negative"


def _prob_colour(prob: float) -> str:
    """Return Rich colour for a probability value in the directional range."""
    if prob >= 58: return "bright_green"
    if prob >= 52: return "yellow"
    return "white"


def print_results_table(top: list, cal: dict):
    """Print the main results table."""
    cal_note = ""
    if cal["calibrated"] and abs(cal["prob_adjustment"]) >= 1.0:
        direction = "adjusted down" if cal["prob_adjustment"] > 0 else "adjusted up"
        cal_note  = (f"  [dim italic](probabilities {direction} "
                     f"{abs(cal['prob_adjustment']):.1f}pp by calibration)[/dim italic]")

    table = Table(
        title=f"\n[bold]Top {TOP_N} LSE Picks -- Tuesday Open / Monday Close[/bold]{cal_note}",
        box=box.ROUNDED, show_lines=True, style="cyan", header_style="bold magenta",
    )
    table.add_column("Rank",           justify="center", style="bold white",  no_wrap=True)
    table.add_column("Ticker",         justify="center", style="bold yellow", no_wrap=True)
    table.add_column("Sector",         justify="left",   style="white",       no_wrap=True)
    table.add_column("Price (p)",      justify="right",  style="white")
    table.add_column("Target (p)",     justify="right",  style="green")
    table.add_column("Upside",         justify="right",  style="bright_green")
    table.add_column("P(rise)\n[dim]prob. of rising[/dim]", justify="right",  style="bright_cyan")
    table.add_column("Stop (p)",       justify="right",  style="red")
    table.add_column("R:R",            justify="center", style="magenta")
    table.add_column("Score",          justify="center", style="white")
    table.add_column("Co. News",       justify="left",   no_wrap=True)
    table.add_column("Sector News",    justify="left",   no_wrap=True)

    for i, r in enumerate(top, 1):
        pc = _prob_colour(r["prob"])

        # Company news
        news              = r.get("news", {})
        co_colour, co_lbl = _sentiment_colour(
            news.get("score", 0), news.get("available", False)
        )

        # Sector news
        sec_news               = r.get("sector_news", {})
        sec_colour, sec_lbl    = _sentiment_colour(
            sec_news.get("score", 0), sec_news.get("available", False)
        )

        table.add_row(
            str(i), r["ticker"], r["sector"],
            f"{r['price']:,.2f}", f"{r['target']:,.2f}",
            f"+{r['upside_pct']:.1f}%",
            f"[{pc}]{r['prob']:.0f}%[/{pc}]",
            f"{r['stop']:,.2f}",
            f"{r['reward_risk']:.2f}", str(r["score"]),
            f"[{co_colour}]{co_lbl}[/{co_colour}]",
            f"[{sec_colour}]{sec_lbl}[/{sec_colour}]",
        )
    console.print(table)


def print_sizing_table(top: list, total_capital: float):
    """Print the position sizing table and capital summary."""
    size_table = Table(
        title="[bold]Suggested Position Sizing[/bold]",
        box=box.SIMPLE_HEAVY, style="cyan", header_style="bold magenta",
    )
    size_table.add_column("Ticker",       justify="center", style="bold yellow")
    size_table.add_column("P(rise)",      justify="right",  style="bright_cyan")
    size_table.add_column("Allocation %", justify="right",  style="green")
    size_table.add_column("Invest (£)",   justify="right",  style="bright_green")
    size_table.add_column("Price (£)",    justify="right",  style="white")
    size_table.add_column("~Shares",      justify="right",  style="white")
    size_table.add_column("Signal",       style="white")

    for r in top:
        price_gbp = r["price"] / 100
        pc        = _prob_colour(r["prob"])

        if r["allocated_gbp"] == 0:
            note       = "[dim]Below confidence threshold -- skip[/dim]"
            shares_str = "--"
            invest_str = "£0.00"
        else:
            invest_str = f"£{r['allocated_gbp']:,.2f}"
            note       = signal_label(r["prob"])
            if r["shares"] == 0:
                shares_str = f"<1  (1 share = £{price_gbp:,.2f})"
                note       = f"{note}  · fractional share"
            else:
                shares_str = f"~{r['shares']}"

        size_table.add_row(
            r["ticker"],
            f"[{pc}]{r['prob']:.0f}%[/{pc}]",
            f"{r['allocation_pct']:.1f}%",
            invest_str,
            f"£{price_gbp:,.2f}",
            shares_str,
            note,
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
    """Print the per-stock signal breakdown with tiered probabilities prominently displayed."""
    console.print("[bold]Detailed signal breakdown:[/bold]\n")

    for i, r in enumerate(top, 1):
        console.print(
            f"  [bold yellow]{i}. {r['ticker']}[/bold yellow]  "
            f"[white]({r['sector']})[/white]"
        )

        # ── Tiered probabilities -- shown prominently at the top ──────────
        pt = r.get("prob_tiers", {})
        if pt:
            p0 = pt.get("rises_at_all", 0)
            p1 = pt.get("rises_1pct",   0)
            p2 = pt.get("rises_2pct",   0)
            p3 = pt.get("rises_3pct",   0)
            console.print(
                f"     [bold]Probability of rising:[/bold]  "
                f"at all [{_prob_colour(p0)}]{p0:.0f}%[/{_prob_colour(p0)}]  "
                f"[dim]|[/dim]  "
                f">1% [{_prob_colour(p1)}]{p1:.0f}%[/{_prob_colour(p1)}]  "
                f"[dim]|[/dim]  "
                f">2% [{_prob_colour(p2)}]{p2:.0f}%[/{_prob_colour(p2)}]  "
                f"[dim]|[/dim]  "
                f">3% [{_prob_colour(p3)}]{p3:.0f}%[/{_prob_colour(p3)}]"
            )

        # ── News sentiment summary ─────────────────────────────────────────
        news     = r.get("news", {})
        sec_news = r.get("sector_news", {})
        if news.get("available") or sec_news.get("available"):
            co_colour,  co_lbl  = _sentiment_colour(
                news.get("score", 0), news.get("available", False)
            )
            sec_colour, sec_lbl = _sentiment_colour(
                sec_news.get("score", 0), sec_news.get("available", False)
            )
            console.print(
                f"     [bold]News:[/bold]  "
                f"Company: [{co_colour}]{co_lbl}[/{co_colour}]  "
                f"[dim]|[/dim]  "
                f"Sector: [{sec_colour}]{sec_lbl}[/{sec_colour}]"
            )

        # ── Technical signals ──────────────────────────────────────────────
        console.print(f"     [dim]Technical signals:[/dim]")
        for sig in r["signals"]:
            console.print(f"       [dim]•[/dim] [white]{sig}[/white]")

        console.print(
            f"     [dim]ATR(14) = {r['atr']:.2f}p  |  "
            f"Stop is {r['downside_pct']:.1f}% below entry  |  "
            f"Reward:Risk = {r['reward_risk']:.2f}[/dim]"
        )

        # ── Company headlines ──────────────────────────────────────────────
        if news.get("available") and news.get("headlines"):
            console.print("     [dim]Recent company headlines:[/dim]")
            for h in news["headlines"]:
                console.print(f"       [dim]-[/dim] [dim]{h}[/dim]")

        console.print()

    if skipped_events:
        console.print(
            f"[dim]Skipped (event within 7 days): {', '.join(skipped_events)}[/dim]\n"
        )


def print_macro_table(macro: dict, sector_cache: dict, top: list):
    """Print the macro and sector news summary table."""
    console.print("[bold]Macro & Sector News Summary[/bold]\n")

    table = Table(
        box=box.ROUNDED, show_lines=True, style="cyan", header_style="bold magenta",
    )
    table.add_column("Level",         justify="left",  style="bold white",  no_wrap=True)
    table.add_column("Sentiment",     justify="left",  no_wrap=True)
    table.add_column("Event / Notes", justify="left",  style="white")
    table.add_column("Key Headlines", justify="left",  style="white")

    # Market-wide row
    if macro.get("available"):
        mc, ml    = _sentiment_colour(macro["score"], True)
        event_str = macro.get("event_label", "General conditions")
        headlines = "  /  ".join(macro.get("headlines", [])[:3]) or "--"
        table.add_row(
            "Market-wide",
            f"[{mc}]{ml}[/{mc}]",
            f"[white]{event_str}[/white]",
            headlines,
        )
    else:
        table.add_row(
            "Market-wide",
            "[dim]No data[/dim]",
            "[dim]NewsAPI unavailable[/dim]",
            "--",
        )

    # One row per sector in the final picks
    seen_sectors = []
    for r in top:
        sector = r["sector"]
        if sector in seen_sectors:
            continue
        seen_sectors.append(sector)

        sec               = sector_cache.get(sector, {})
        sc, sl            = _sentiment_colour(
            sec.get("score", 0), sec.get("available", False)
        )
        headlines = "  /  ".join(sec.get("headlines", [])[:3]) or "[dim]No headlines found[/dim]"

        table.add_row(
            sector,
            f"[{sc}]{sl}[/{sc}]",
            "--",
            headlines,
        )

    console.print(table)
    console.print()


def print_disclaimer():
    """Print the how-it-works and disclaimer panel."""
    console.print(Panel(
        "[dim]"
        "[bold]Column guide:[/bold]\n"
        "  Price / Target / Stop / Limit  All in PENCE (divide by 100 for £)\n"
        "  P(rise)      Probability the stock rises at all over the 7-day window\n"
        "               (based on directional accuracy, not target hit rate)\n"
        "  R:R          Reward:Risk ratio\n"
        "  Stop (p)     Mental stop price -- sell before close if daily close hits this level\n"
        "  Score        Technical score + news adjustment\n"
        "  Co. News     Sentiment of company-specific headlines\n"
        "  Sector News  Sentiment of sector-wide headlines\n"
        "  Invest (£)   Suggested amount -- the primary sizing guide\n\n"
        "[bold]Signal strength labels:[/bold]\n"
        "  Strong signal    P(rise) >= 70% -- well above adjusted baseline\n"
        "  Moderate signal  P(rise) >= 65%\n"
        "  Cautious signal  P(rise) >= 60% -- at or near adjusted baseline\n"
        "  Below floor      P(rise) < 60%  -- skip or minimal allocation\n\n"
        "[bold]Probability of rising tiers (signal breakdown):[/bold]\n"
        "  Shows estimated probability of the stock rising by each threshold\n"
        "  based on the historical return distribution from the backtest.\n"
        "  'At all' is the headline figure used for position sizing.\n\n"
        "[bold red]Disclaimer:[/bold red] Quantitative screening tool only -- NOT financial\n"
        "advice. All probabilities are model estimates. Past patterns do not\n"
        "guarantee future results. Consult a regulated adviser before trading.[/dim]",
        title="[bold]Column Guide & Disclaimer[/bold]",
        box=box.ROUNDED,
    ))
