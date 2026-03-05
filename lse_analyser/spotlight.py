"""
spotlight.py
------------
Single-stock spotlight mode (mode S).

Accepts a stock code in any format:
  - Short LSE code  (e.g. CWR)       -- tries CWR.L first, falls back to CWR
  - Full Yahoo ticker (e.g. CWR.L)   -- used as-is
  - US stock        (e.g. AAPL)      -- used as-is

Runs the full analysis pipeline on that one stock:
  1. Technical scoring
  2. Company news sentiment
  3. Macro market sentiment + sector sensitivity
  4. Sector news sentiment
  5. Tiered probability display
  6. Optional position sizing

Results are displayed but never saved to the CSV log.
"""

import yfinance as yf
from rich.panel import Panel
from rich import box

from .config import ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER, PROB_FLOOR
from .utils import console, silent
from .screener import score_ticker
from .news import fetch_news_sentiment, apply_news_adjustment
from .macro import (
    fetch_macro_sentiment, fetch_sector_sentiment,
    apply_macro_to_pick, check_macro_warning,
)
from .sizing import calculate_allocations, signal_label
from .display import (
    _sentiment_colour, _prob_colour, print_macro_table, print_disclaimer,
)
from .calibration import compute_calibration


def run_spotlight():
    """Entry point for single-stock spotlight mode."""
    console.print(
        "\n[bold cyan]Spotlight Mode[/bold cyan]\n"
        "[dim]Run the full analysis pipeline on a single stock.\n"
        "Results are shown but not saved to the log.[/dim]\n"
    )

    # ── Step 1: get ticker input ───────────────────────────────────────────
    ticker_raw = input("  Enter stock code (e.g. CWR, CWR.L, AAPL): ").strip().upper()
    if not ticker_raw:
        console.print("[red]No ticker entered.[/red]")
        return

    ticker, company_name, sector, currency = _resolve_ticker(ticker_raw)
    if ticker is None:
        console.print(
            f"[red]Could not find data for '{ticker_raw}'. "
            f"Check the code and try again.[/red]\n"
        )
        return

    console.print(
        f"\n[bold yellow]{ticker}[/bold yellow]  "
        f"[white]{company_name}[/white]  "
        f"[dim]({sector})[/dim]\n"
    )

    # ── Step 2: technical scoring ──────────────────────────────────────────
    cal = compute_calibration()

    with console.status(f"[bold green]Running technical analysis on {ticker}..."):
        result = score_ticker(ticker, sector, prob_adjustment=cal["prob_adjustment"])

    if result is None:
        console.print(
            f"[red]{ticker} did not pass the technical screening filters.\n"
            f"This could mean insufficient price history, low volume, or "
            f"no bullish signals detected.[/red]\n"
        )
        _offer_raw_data(ticker)
        return

    # ── Step 3: company news ───────────────────────────────────────────────
    console.print("[dim]Fetching company news...[/dim]")
    sentiment = fetch_news_sentiment(ticker, company_name)
    apply_news_adjustment(result, sentiment)

    # ── Step 4: macro + sector sentiment ──────────────────────────────────
    console.print("[dim]Fetching macro and sector sentiment...[/dim]")
    macro        = fetch_macro_sentiment()
    sector_sent  = fetch_sector_sentiment(sector)
    apply_macro_to_pick(result, macro, sector_sent)
    result["sector_news"] = sector_sent

    # Store sector cache for macro table display
    sector_cache = {sector: sector_sent}

    # ── Step 5: optional capital input ────────────────────────────────────
    total_capital = _ask_for_spotlight_capital()

    # ── Step 6: position sizing (if capital entered) ───────────────────────
    if total_capital:
        calculate_allocations([result], total_capital)
    else:
        result["allocation_pct"] = 0.0
        result["allocated_gbp"]  = 0.0
        result["shares"]         = 0
        result["kelly_weight"]   = 0.0

    # ── Step 7: display ────────────────────────────────────────────────────
    _print_spotlight_result(result, macro, sector_cache, total_capital, cal, currency)


def _resolve_ticker(raw: str) -> tuple:
    """
    Try to resolve a stock code to a valid Yahoo Finance ticker.
    Returns (ticker_string, company_name, sector, currency) or (None, None, None, None).

    For ambiguous codes (no '.' in input), tries both raw+'.L' and raw,
    then picks whichever has higher average daily volume. This ensures
    that e.g. 'TSLA' resolves to the US stock rather than a thinly-traded
    LSE ETP tracker with the same code.
    """
    if "." in raw:
        candidates = [raw]
    else:
        candidates = [raw + ".L", raw]

    best_ticker = best_name = best_sector = best_currency = None
    best_volume = -1

    for candidate in candidates:
        try:
            with silent():
                t    = yf.Ticker(candidate)
                info = t.info
            price = (
                info.get("regularMarketPrice") or
                info.get("currentPrice") or
                info.get("previousClose")
            )
            if not price:
                continue

            avg_vol = (
                info.get("averageVolume") or
                info.get("averageDailyVolume10Day") or
                info.get("volume") or
                0
            )

            if avg_vol > best_volume:
                best_volume   = avg_vol
                best_ticker   = candidate
                best_name     = (
                    info.get("longName") or
                    info.get("shortName") or
                    candidate.replace(".L", "")
                )
                yf_sector     = info.get("sector") or info.get("industry") or ""
                best_sector   = _map_sector(yf_sector) if yf_sector else "Other"
                best_currency = info.get("currency", "GBp")
        except Exception:
            continue

    return best_ticker, best_name, best_sector, best_currency


def _map_sector(yf_sector: str) -> str:
    """Map a Yahoo Finance sector string to our internal sector labels."""
    from .config import normalise_sector
    return normalise_sector(yf_sector)


def _ask_for_spotlight_capital() -> float:
    """
    Ask the user how much they are considering investing in this one stock.
    Returns 0.0 if skipped.
    """
    console.print(
        "\n[bold cyan]Position Sizing[/bold cyan]\n"
        "[dim]Enter the capital you are considering for this stock,\n"
        "or press Enter to skip sizing.[/dim]\n"
    )
    raw = input("  Capital to consider (£), or Enter to skip: ").strip().replace("£", "").replace(",", "")
    if not raw:
        return 0.0
    try:
        val = float(raw)
        return val if val > 0 else 0.0
    except ValueError:
        return 0.0


def _currency_info(currency: str) -> tuple:
    """
    Return (symbol, is_pence, price_divisor) for a given currency code.
    LSE stocks are quoted in GBp (pence) -- divide by 100 for pounds.
    US/other stocks are quoted in their native currency -- no division.
    """
    if currency in ("GBp", "GBX"):
        return "£", True, 100          # Pence -- divide by 100 for £
    elif currency == "GBP":
        return "£", False, 1           # Already in pounds
    elif currency == "USD":
        return "$", False, 1
    elif currency == "EUR":
        return "€", False, 1
    else:
        return currency + " ", False, 1


def _get_gbp_rate(currency: str) -> float:
    """
    Fetch live GBP exchange rate for currency conversion.
    Returns 1.0 if unavailable or not needed.
    """
    if currency in ("GBp", "GBX", "GBP"):
        return 1.0
    try:
        pair = f"{currency}GBP=X"
        with silent():
            info = yf.Ticker(pair).info
        rate = info.get("regularMarketPrice") or info.get("previousClose") or 0
        return float(rate) if rate else 0.0
    except Exception:
        return 0.0


def _print_spotlight_result(
    r: dict, macro: dict, sector_cache: dict,
    total_capital: float, cal: dict, currency: str = "GBp"
):
    """Print the full spotlight analysis for a single stock."""

    sym, is_pence, divisor = _currency_info(currency)

    # Price display -- LSE stocks in pence with £ conversion shown
    # Non-GBP stocks shown in their native currency
    price_display  = f"{sym}{r['price'] / divisor:,.2f}"
    target_display = f"{sym}{r['target'] / divisor:,.2f}"
    stop_display   = f"{sym}{r['stop'] / divisor:,.2f}"
    limit_display  = f"{sym}{r['limit'] / divisor:,.2f}"

    if is_pence:
        price_display += f"  [dim]({r['price']:.0f}p)[/dim]"

    # ── Main panel ─────────────────────────────────────────────────────────
    pc      = _prob_colour(r["prob"])
    news    = r.get("news", {})
    co_col, co_lbl = _sentiment_colour(news.get("score", 0), news.get("available", False))
    sec     = r.get("sector_news", {})
    sc_col, sc_lbl = _sentiment_colour(sec.get("score", 0), sec.get("available", False))

    warning_level = check_macro_warning(macro)
    macro_note    = ""
    if warning_level == "skip":
        macro_note = "\n  [red]⚠ Macro conditions suggest skipping this week[/red]"
    elif warning_level == "warning":
        macro_note = "\n  [yellow]⚠ Macro caution -- review sector sensitivity below[/yellow]"

    console.print(Panel(
        f"  [bold]Price:[/bold]       [white]{price_display}[/white]\n"
        f"  [bold]Target:[/bold]      [green]{target_display}[/green]  "
        f"[dim]+{r['upside_pct']:.1f}% upside[/dim]\n"
        f"  [bold]Stop:[/bold]        [red]{stop_display}[/red]  "
        f"[dim]{r['downside_pct']:.1f}% below entry[/dim]\n"
        f"  [bold]Limit:[/bold]       [bright_yellow]{limit_display}[/bright_yellow]\n"
        f"  [bold]R:R:[/bold]         [magenta]{r['reward_risk']:.2f}[/magenta]\n"
        f"  [bold]Score:[/bold]       {r['score']}\n\n"
        f"  [bold]P(rise):[/bold]     [{pc}]{r['prob']:.0f}%[/{pc}]  "
        f"[dim]({signal_label(r['prob'])})[/dim]\n"
        f"  [bold]Co. News:[/bold]    [{co_col}]{co_lbl}[/{co_col}]\n"
        f"  [bold]Sector News:[/bold] [{sc_col}]{sc_lbl}[/{sc_col}]"
        f"{macro_note}",
        title=f"[bold yellow]{r['ticker']}[/bold yellow]  [white]{r['sector']}[/white]  "
              f"[dim]({currency})[/dim]",
        box=box.ROUNDED,
    ))

    # ── Tiered probabilities ───────────────────────────────────────────────
    pt = r.get("prob_tiers", {})
    if pt:
        p0 = pt.get("rises_at_all", 0)
        p1 = pt.get("rises_1pct",   0)
        p2 = pt.get("rises_2pct",   0)
        p3 = pt.get("rises_3pct",   0)
        console.print(
            f"  [bold]Probability of rising:[/bold]  "
            f"at all [{_prob_colour(p0)}]{p0:.0f}%[/{_prob_colour(p0)}]  [dim]|[/dim]  "
            f">1% [{_prob_colour(p1)}]{p1:.0f}%[/{_prob_colour(p1)}]  [dim]|[/dim]  "
            f">2% [{_prob_colour(p2)}]{p2:.0f}%[/{_prob_colour(p2)}]  [dim]|[/dim]  "
            f">3% [{_prob_colour(p3)}]{p3:.0f}%[/{_prob_colour(p3)}]\n"
        )

    # ── Technical signals ──────────────────────────────────────────────────
    console.print("  [bold]Technical signals:[/bold]")
    for sig in r["signals"]:
        console.print(f"    [dim]•[/dim] [white]{sig}[/white]")
    console.print(
        f"\n  [dim]ATR(14) = {r['atr'] / divisor:.2f}{sym.strip()}  |  "
        f"Reward:Risk = {r['reward_risk']:.2f}[/dim]\n"
    )

    # ── Company headlines ──────────────────────────────────────────────────
    if news.get("available") and news.get("headlines"):
        console.print("  [bold]Recent company headlines:[/bold]")
        for h in news["headlines"]:
            console.print(f"    [dim]-[/dim] [dim]{h}[/dim]")
        console.print()

    # ── Macro & sector table ───────────────────────────────────────────────
    print_macro_table(macro, sector_cache, [r])

    # ── Position sizing ────────────────────────────────────────────────────
    if total_capital and r["allocated_gbp"] > 0:
        # Convert suggested GBP allocation to stock's native currency for shares calc
        gbp_to_deploy  = r["allocated_gbp"]
        price_gbp      = r["price"] / divisor if not is_pence else r["price"] / 100

        if currency not in ("GBp", "GBX", "GBP"):
            # Fetch live exchange rate for share count calculation
            rate = _get_gbp_rate(currency)
            if rate > 0:
                native_to_deploy = gbp_to_deploy / rate
                price_native     = r["price"] / divisor
                shares_est       = int(native_to_deploy / price_native) if price_native > 0 else 0
                rate_note        = f"[dim](rate: £1 = {sym.strip()}{rate:.4f})[/dim]"
                deploy_note      = (
                    f"[green]£{gbp_to_deploy:,.2f}[/green]  [dim]≈ "
                    f"{sym}{native_to_deploy:,.2f} {rate_note}[/dim]"
                )
            else:
                shares_est  = r["shares"]
                deploy_note = f"[green]£{gbp_to_deploy:,.2f}[/green]  [dim](rate unavailable)[/dim]"
        else:
            shares_est  = r["shares"]
            deploy_note = f"[green]£{gbp_to_deploy:,.2f}[/green]"

        shares_str = f"~{shares_est}" if shares_est > 0 else f"<1 share (1 share = {sym}{r['price']/divisor:,.2f})"

        console.print(Panel(
            f"  [bold]Capital considered:[/bold]  [cyan]£{total_capital:,.2f}[/cyan]\n"
            f"  [bold]Suggested to deploy:[/bold] {deploy_note}\n"
            f"  [bold]Keep in reserve:[/bold]     [yellow]£{total_capital - gbp_to_deploy:,.2f}[/yellow]\n"
            f"  [bold]Approx. shares:[/bold]      {shares_str}\n\n"
            f"  [dim]Signal: {signal_label(r['prob'])}[/dim]",
            title="[bold]Suggested Position Sizing[/bold]",
            box=box.ROUNDED,
        ))
    elif total_capital:
        console.print(
            f"[yellow]P(rise) is below the confidence floor "
            f"({PROB_FLOOR}%) -- no capital allocation suggested.[/yellow]\n"
        )

    print_disclaimer()


def _offer_raw_data(ticker: str):
    """If scoring failed, offer to show raw price data so the user can investigate."""
    try:
        with silent():
            hist = yf.Ticker(ticker).history(period="5d")
        if not hist.empty:
            console.print("[dim]Last 5 days of price data:[/dim]")
            for date, row in hist.iterrows():
                console.print(
                    f"  [dim]{str(date)[:10]}  "
                    f"Close: {float(row['Close']):,.2f}  "
                    f"Vol: {int(row['Volume']):,}[/dim]"
                )
    except Exception:
        pass
