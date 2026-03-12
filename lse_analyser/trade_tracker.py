"""
trade_tracker.py
----------------
Actual trade tracker — logs real buy/sell prices and calculates true returns.

Distinct from lse_screener_log.csv which records the model's predicted returns
using Monday close prices. This module tracks what you actually paid and received,
allowing comparison of model returns vs real returns over time.

Data model per trade:
  - week_date:       Run date of the live pick (links to lse_screener_log.csv)
  - ticker:          Stock ticker
  - sector:          Sector (from live pick)
  - buy_date:        Date shares were purchased (today when entered)
  - buy_price_p:     Price paid per share in pence
  - total_paid_gbp:  Total amount paid including stamp duty (£)
  - shares:          Calculated: (total_paid / 1.005) / (buy_price_p / 100)
  - stamp_duty_gbp:  Calculated: total_paid * 0.005 / 1.005
  - net_invested_gbp:Calculated: total_paid / 1.005
  - sell_date:       Date shares were sold (today when entered)
  - sell_price_p:    Price received per share in pence
  - gross_proceeds:  Calculated: shares * (sell_price_p / 100)
  - net_profit_gbp:  Calculated: gross_proceeds - total_paid_gbp
  - net_profit_pct:  Calculated: net_profit_gbp / total_paid_gbp * 100
  - status:          'open' (bought, not yet sold) or 'closed'

Stamp duty formula:
  stamp_duty    = total_paid * 0.005 / 1.005
  net_invested  = total_paid / 1.005
  shares        = net_invested / (buy_price_p / 100)
"""

import csv
import os
from datetime import datetime

from rich.table import Table
from rich.panel import Panel
from rich import box

from .config import CSV_FILE
from .utils import console
from .market_log import format_market_summary, get_market_return


TRADE_LOG_FILE   = "lse_trade_log.csv"
TRADE_LOG_HEADERS = [
    "week_date", "ticker", "sector",
    "buy_date", "buy_price_p", "total_paid_gbp",
    "shares", "stamp_duty_gbp", "net_invested_gbp",
    "sell_date", "sell_price_p", "gross_proceeds_gbp",
    "net_profit_gbp", "net_profit_pct",
    "status",
]


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _load_trades() -> list:
    if not os.path.isfile(TRADE_LOG_FILE):
        return []
    with open(TRADE_LOG_FILE, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_trades(trades: list):
    with open(TRADE_LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trades)


def _load_live_picks() -> dict:
    """Load live picks from lse_screener_log.csv grouped by run_date."""
    if not os.path.isfile(CSV_FILE):
        return {}
    runs = {}
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rd = row.get("run_date", "").strip()
            if rd:
                runs.setdefault(rd, []).append(row)
    return runs


# ── Calculations ──────────────────────────────────────────────────────────────

def _calculate_buy(total_paid_gbp: float, buy_price_p: float) -> dict:
    """Derive shares, stamp duty, and net invested from total paid."""
    net_invested  = total_paid_gbp / 1.005
    stamp_duty    = total_paid_gbp * 0.005 / 1.005
    shares        = net_invested / (buy_price_p / 100)
    return {
        "net_invested_gbp": round(net_invested, 2),
        "stamp_duty_gbp":   round(stamp_duty, 4),
        "shares":           round(shares, 4),
    }


def _calculate_sell(shares: float, sell_price_p: float, total_paid_gbp: float) -> dict:
    """Derive gross proceeds and profit from sell price."""
    gross_proceeds = shares * (sell_price_p / 100)
    net_profit_gbp = gross_proceeds - total_paid_gbp
    net_profit_pct = (net_profit_gbp / total_paid_gbp * 100) if total_paid_gbp > 0 else 0
    return {
        "gross_proceeds_gbp": round(gross_proceeds, 2),
        "net_profit_gbp":     round(net_profit_gbp, 2),
        "net_profit_pct":     round(net_profit_pct, 4),
    }


# ── Display helpers ───────────────────────────────────────────────────────────

def _profit_colour(val: float) -> str:
    if val > 0:  return "bright_green"
    if val < 0:  return "red"
    return "white"


def _fmt_profit(val: float, suffix: str = "") -> str:
    c    = _profit_colour(val)
    sign = "+" if val >= 0 else ""
    return f"[{c}]{sign}{val:.2f}{suffix}[/{c}]"


# ── Log a buy ─────────────────────────────────────────────────────────────────

def log_buy():
    """Prompt user to log a new buy trade against an existing live pick."""
    console.rule("[bold cyan]Log a Buy[/bold cyan]")

    # Select week from live picks
    runs = _load_live_picks()
    if not runs:
        console.print(Panel(
            "[yellow]No live picks found.[/yellow]\n"
            "[dim]Run in Live mode first to build a pick history.[/dim]",
            box=box.ROUNDED,
        ))
        return

    run_dates = sorted(runs.keys(), reverse=True)
    console.print("\n[bold]Select the week:[/bold]\n")
    for i, rd in enumerate(run_dates, 1):
        tickers = [r["ticker"] for r in runs[rd]]
        console.print(f"  [bold white]{i:>2}.[/bold white]  [yellow]{rd}[/yellow]  --  {', '.join(tickers)}")

    console.print()
    while True:
        try:
            raw = input(f"  Week number (1-{len(run_dates)}): ").strip()
            choice = int(raw)
            if 1 <= choice <= len(run_dates):
                break
            raise ValueError
        except ValueError:
            console.print(f"  [red]Please enter a number between 1 and {len(run_dates)}[/red]")

    week_date = run_dates[choice - 1]
    picks     = runs[week_date]

    # Inner loop — stay within this week until all tickers logged or user quits
    while True:
        # Reload trades each iteration so logged_tickers stays current
        trades         = _load_trades()
        logged_tickers = {t["ticker"] for t in trades if t["week_date"] == week_date}
        available      = [p for p in picks if p["ticker"] not in logged_tickers]

        if not available:
            console.print(
                f"\n[green]All picks from {week_date} have been logged.[/green]\n"
            )
            return

        # Select ticker
        console.print(f"\n[bold]Select ticker from {week_date}:[/bold]\n")
        for i, p in enumerate(available, 1):
            model_price = f"{float(p['price_p']):,.2f}p" if p.get("price_p") else "--"
            console.print(
                f"  [bold white]{i:>2}.[/bold white]  "
                f"[yellow]{p['ticker']}[/yellow]  "
                f"[dim]{p['sector']}  --  model price {model_price}[/dim]"
            )
        console.print(f"  [bold white] Q.[/bold white]  [dim]Back to tracker menu[/dim]")

        console.print()
        while True:
            raw = input(f"  Ticker number (1-{len(available)}) or Q to quit: ").strip().upper()
            if raw in ("Q", "QUIT"):
                return
            try:
                choice = int(raw)
                if 1 <= choice <= len(available):
                    break
                raise ValueError
            except ValueError:
                console.print(f"  [red]Please enter a number between 1 and {len(available)}, or Q[/red]")

        pick   = available[choice - 1]
        ticker = pick["ticker"]
        sector = pick.get("sector", "")

        # Buy price and total paid
        console.print(f"\n[bold]Enter buy details for [yellow]{ticker}[/yellow]:[/bold]\n")

        while True:
            try:
                buy_price_p = float(input("  Buy price per share (pence): ").strip())
                if buy_price_p <= 0:
                    raise ValueError
                break
            except ValueError:
                console.print("  [red]Please enter a valid price in pence (e.g. 523.5)[/red]")

        while True:
            try:
                raw_paid   = input("  Total paid including stamp duty (£): ").strip().replace("£", "").replace(",", "")
                total_paid = float(raw_paid)
                if total_paid <= 0:
                    raise ValueError
                break
            except ValueError:
                console.print("  [red]Please enter a valid amount in pounds (e.g. 500.00)[/red]")

        calcs    = _calculate_buy(total_paid, buy_price_p)
        buy_date = datetime.now().strftime("%Y-%m-%d")

        # Confirm before saving
        console.print(Panel(
            f"  Ticker:          [yellow]{ticker}[/yellow]  [dim]({sector})[/dim]\n"
            f"  Week:            {week_date}\n"
            f"  Buy date:        {buy_date}\n"
            f"  Buy price:       {buy_price_p:.2f}p per share\n"
            f"  Total paid:      £{total_paid:,.2f}  [dim](including stamp duty)[/dim]\n"
            f"  Stamp duty:      £{calcs['stamp_duty_gbp']:.4f}\n"
            f"  Net invested:    £{calcs['net_invested_gbp']:,.2f}\n"
            f"  Shares:          {calcs['shares']:.4f}",
            title="[bold]Confirm Buy[/bold]",
            box=box.ROUNDED,
        ))

        confirm = input("  Save? (Y/N): ").strip().upper()
        if confirm != "Y":
            console.print("[yellow]Cancelled.[/yellow]\n")
            # Don't exit — loop back so user can pick a different ticker
            continue

        trade = {
            "week_date":          week_date,
            "ticker":             ticker,
            "sector":             sector,
            "buy_date":           buy_date,
            "buy_price_p":        round(buy_price_p, 4),
            "total_paid_gbp":     round(total_paid, 2),
            "shares":             calcs["shares"],
            "stamp_duty_gbp":     calcs["stamp_duty_gbp"],
            "net_invested_gbp":   calcs["net_invested_gbp"],
            "sell_date":          "",
            "sell_price_p":       "",
            "gross_proceeds_gbp": "",
            "net_profit_gbp":     "",
            "net_profit_pct":     "",
            "status":             "open",
        }

        trades.append(trade)
        _save_trades(trades)
        console.print(f"[green]Buy logged for {ticker}.[/green]\n")
        # Loop continues — next iteration will show remaining tickers


# ── Log a sell ────────────────────────────────────────────────────────────────

def log_sell():
    """Prompt user to log a sell against an open trade."""
    console.rule("[bold cyan]Log a Sell[/bold cyan]")

    trades = _load_trades()
    open_trades = [t for t in trades if t.get("status") == "open"]

    if not open_trades:
        console.print(Panel(
            "[yellow]No open trades to sell.[/yellow]\n"
            "[dim]Log a buy first.[/dim]",
            box=box.ROUNDED,
        ))
        return

    # Inner loop — stay in sell section until all open trades are closed or user quits
    while True:
        # Reload each iteration so list stays current after each sell
        trades      = _load_trades()
        open_trades = [t for t in trades if t.get("status") == "open"]

        if not open_trades:
            console.print("\n[green]All open trades have been closed.[/green]\n")
            return

        console.print("\n[bold]Open trades:[/bold]\n")
        for i, t in enumerate(open_trades, 1):
            console.print(
                f"  [bold white]{i:>2}.[/bold white]  "
                f"[yellow]{t['ticker']}[/yellow]  "
                f"[dim]week {t['week_date']}  --  "
                f"bought {t['buy_date']} at {float(t['buy_price_p']):.2f}p  --  "
                f"£{float(t['total_paid_gbp']):,.2f} invested[/dim]"
            )
        console.print(f"  [bold white] Q.[/bold white]  [dim]Back to tracker menu[/dim]")

        console.print()
        while True:
            raw = input(f"  Trade number (1-{len(open_trades)}) or Q to quit: ").strip().upper()
            if raw in ("Q", "QUIT"):
                return
            try:
                choice = int(raw)
                if 1 <= choice <= len(open_trades):
                    break
                raise ValueError
            except ValueError:
                console.print(f"  [red]Please enter a number between 1 and {len(open_trades)}, or Q[/red]")

        trade = open_trades[choice - 1]

        console.print(f"\n[bold]Enter sell details for [yellow]{trade['ticker']}[/yellow]:[/bold]\n")

        while True:
            try:
                sell_price_p = float(input("  Sell price per share (pence): ").strip())
                if sell_price_p <= 0:
                    raise ValueError
                break
            except ValueError:
                console.print("  [red]Please enter a valid price in pence (e.g. 541.0)[/red]")

        shares     = float(trade["shares"])
        total_paid = float(trade["total_paid_gbp"])
        sell_calcs = _calculate_sell(shares, sell_price_p, total_paid)
        sell_date  = datetime.now().strftime("%Y-%m-%d")

        pc = _profit_colour(sell_calcs["net_profit_gbp"])
        console.print(Panel(
            f"  Ticker:          [yellow]{trade['ticker']}[/yellow]\n"
            f"  Bought:          {trade['buy_date']} at {float(trade['buy_price_p']):.2f}p\n"
            f"  Sold:            {sell_date} at {sell_price_p:.2f}p\n"
            f"  Shares:          {shares:.4f}\n"
            f"  Total paid:      £{total_paid:,.2f}\n"
            f"  Gross proceeds:  £{sell_calcs['gross_proceeds_gbp']:,.2f}\n"
            f"  Net profit:      [{pc}]{'+' if sell_calcs['net_profit_gbp'] >= 0 else ''}"
            f"£{sell_calcs['net_profit_gbp']:,.2f}[/{pc}]  "
            f"[{pc}]({'+' if sell_calcs['net_profit_pct'] >= 0 else ''}"
            f"{sell_calcs['net_profit_pct']:.2f}%)[/{pc}]",
            title="[bold]Confirm Sell[/bold]",
            box=box.ROUNDED,
        ))

        confirm = input("  Save? (Y/N): ").strip().upper()
        if confirm != "Y":
            console.print("[yellow]Cancelled.[/yellow]\n")
            # Don't exit — loop back so user can pick a different trade
            continue

        # Update the trade in the full list
        for t in trades:
            if (t["week_date"] == trade["week_date"] and
                    t["ticker"] == trade["ticker"] and
                    t["status"] == "open"):
                t["sell_date"]          = sell_date
                t["sell_price_p"]       = round(sell_price_p, 4)
                t["gross_proceeds_gbp"] = sell_calcs["gross_proceeds_gbp"]
                t["net_profit_gbp"]     = sell_calcs["net_profit_gbp"]
                t["net_profit_pct"]     = sell_calcs["net_profit_pct"]
                t["status"]             = "closed"
                break

        _save_trades(trades)
        console.print(f"[green]Sell logged for {trade['ticker']}.[/green]\n")
        # Loop continues — next iteration shows remaining open trades


# ── View summary ──────────────────────────────────────────────────────────────

def view_trade_summary():
    """Display full trade history with weekly and overall totals."""
    console.rule("[bold cyan]Actual Trade Returns[/bold cyan]")

    trades = _load_trades()
    if not trades:
        console.print(Panel(
            "[yellow]No trades logged yet.[/yellow]\n"
            "[dim]Use 'Log a buy' to start tracking your actual returns.[/dim]",
            box=box.ROUNDED,
        ))
        return

    closed = [t for t in trades if t.get("status") == "closed"]
    open_t = [t for t in trades if t.get("status") == "open"]

    # ── Per-trade table ────────────────────────────────────────────────────────
    trade_table = Table(
        title="[bold]All Trades[/bold]",
        box=box.ROUNDED, show_lines=True,
        style="cyan", header_style="bold magenta",
    )
    trade_table.add_column("Week",        style="dim",          width=12)
    trade_table.add_column("Ticker",      style="bold yellow",  width=7,  justify="center")
    trade_table.add_column("Sector",      style="dim white",    width=13)
    trade_table.add_column("Buy date",    style="dim",          width=11)
    trade_table.add_column("Buy (p)",     justify="right",      width=9)
    trade_table.add_column("Paid (£)",    justify="right",      width=10)
    trade_table.add_column("Shares",      justify="right",      width=8)
    trade_table.add_column("S.Duty (£)",  justify="right",      width=10)
    trade_table.add_column("Sell date",   style="dim",          width=11)
    trade_table.add_column("Sell (p)",    justify="right",      width=9)
    trade_table.add_column("Proceeds (£)",justify="right",      width=13)
    trade_table.add_column("Profit (£)",  justify="right",      width=11)
    trade_table.add_column("Profit %",    justify="right",      width=10)
    trade_table.add_column("Status",      justify="center",     width=8)

    for t in sorted(trades, key=lambda x: x["week_date"]):
        status   = t.get("status", "")
        is_open  = status == "open"

        profit_gbp_str = profit_pct_str = proceeds_str = sell_date_str = sell_price_str = "--"

        if not is_open:
            p_gbp = float(t.get("net_profit_gbp", 0))
            p_pct = float(t.get("net_profit_pct", 0))
            profit_gbp_str = _fmt_profit(p_gbp, "")
            profit_pct_str = _fmt_profit(p_pct, "%")
            proceeds_str   = f"£{float(t['gross_proceeds_gbp']):,.2f}"
            sell_date_str  = t.get("sell_date", "--")
            sell_price_str = f"{float(t['sell_price_p']):,.2f}"

        trade_table.add_row(
            t.get("week_date", ""),
            t.get("ticker", ""),
            t.get("sector", ""),
            t.get("buy_date", ""),
            f"{float(t['buy_price_p']):,.2f}",
            f"£{float(t['total_paid_gbp']):,.2f}",
            f"{float(t['shares']):.2f}",
            f"£{float(t['stamp_duty_gbp']):.4f}",
            sell_date_str,
            sell_price_str,
            proceeds_str,
            profit_gbp_str,
            profit_pct_str,
            "[dim]Open[/dim]" if is_open else "[bright_green]Closed[/bright_green]",
        )

    console.print(trade_table)

    # ── Weekly summary ─────────────────────────────────────────────────────────
    if closed:
        weeks = {}
        for t in closed:
            wd = t["week_date"]
            weeks.setdefault(wd, []).append(t)

        console.print()
        week_table = Table(
            title="[bold]Weekly Summary (closed trades)[/bold]",
            box=box.SIMPLE_HEAVY, header_style="bold cyan",
        )
        week_table.add_column("Week",          width=12)
        week_table.add_column("Trades",        justify="right", width=8)
        week_table.add_column("Total invested",justify="right", width=16)
        week_table.add_column("Total proceeds",justify="right", width=16)
        week_table.add_column("Net profit (£)",justify="right", width=15)
        week_table.add_column("Net profit %",  justify="right", width=13)
        week_table.add_column("FTSE 100",      justify="right", width=10)
        week_table.add_column("FTSE 250",      justify="right", width=10)
        week_table.add_column("Alpha",         justify="right", width=10)

        for wd in sorted(weeks.keys()):
            wt            = weeks[wd]
            total_paid    = sum(float(t["total_paid_gbp"])     for t in wt)
            total_proc    = sum(float(t["gross_proceeds_gbp"]) for t in wt)
            total_profit  = total_proc - total_paid
            profit_pct    = (total_profit / total_paid * 100) if total_paid > 0 else 0

            # Market data for this week
            mkt = get_market_return(wd)
            def _mkt_fmt(val):
                if val is None: return "[dim]–[/dim]"
                c    = "bright_green" if val > 0 else "red" if val < 0 else "white"
                sign = "+" if val >= 0 else ""
                return f"[{c}]{sign}{val:.2f}%[/{c}]"

            f100_str   = _mkt_fmt(mkt["ftse100_return_pct"] if mkt else None)
            f250_str   = _mkt_fmt(mkt["ftse250_return_pct"] if mkt else None)
            alpha_str  = "[dim]–[/dim]"
            if mkt and mkt.get("ftse100_return_pct") is not None:
                alpha     = profit_pct - mkt["ftse100_return_pct"]
                ac        = "bright_green" if alpha > 0 else "red" if alpha < 0 else "white"
                sign      = "+" if alpha >= 0 else ""
                alpha_str = f"[{ac}]{sign}{alpha:.2f}pp[/{ac}]"

            week_table.add_row(
                wd,
                str(len(wt)),
                f"£{total_paid:,.2f}",
                f"£{total_proc:,.2f}",
                _fmt_profit(total_profit),
                _fmt_profit(profit_pct, "%"),
                f100_str,
                f250_str,
                alpha_str,
            )

        console.print(week_table)

    # ── Overall summary ────────────────────────────────────────────────────────
    console.print()

    total_invested = sum(float(t["total_paid_gbp"]) for t in trades)
    open_invested  = sum(float(t["total_paid_gbp"]) for t in open_t)

    if closed:
        closed_paid   = sum(float(t["total_paid_gbp"])     for t in closed)
        closed_proc   = sum(float(t["gross_proceeds_gbp"]) for t in closed)
        overall_profit  = closed_proc - closed_paid
        overall_pct     = (overall_profit / closed_paid * 100) if closed_paid > 0 else 0
        avg_trade_pct   = sum(float(t["net_profit_pct"]) for t in closed) / len(closed)
        best_trade      = max(closed, key=lambda t: float(t["net_profit_pct"]))
        worst_trade     = min(closed, key=lambda t: float(t["net_profit_pct"]))
        winners         = sum(1 for t in closed if float(t["net_profit_pct"]) > 0)

        pc = _profit_colour(overall_profit)
        console.print(Panel(
            f"  Closed trades:       {len(closed)}\n"
            f"  Open trades:         {len(open_t)}"
            + (f"  [dim](£{open_invested:,.2f} invested)[/dim]" if open_t else "") + "\n\n"
            f"  Total invested:      £{closed_paid:,.2f}  [dim](closed trades only)[/dim]\n"
            f"  Total proceeds:      £{closed_proc:,.2f}\n"
            f"  Overall profit:      [{pc}]{'+' if overall_profit >= 0 else ''}£{overall_profit:,.2f}[/{pc}]  "
            f"[{pc}]({'+' if overall_pct >= 0 else ''}{overall_pct:.2f}%)[/{pc}]\n\n"
            f"  Avg profit / trade:  {_fmt_profit(avg_trade_pct, '%')}\n"
            f"  Win rate:            {winners}/{len(closed)} ({winners/len(closed)*100:.0f}%)\n"
            f"  Best trade:          [bright_green]{best_trade['ticker']}[/bright_green]  "
            f"{_fmt_profit(float(best_trade['net_profit_pct']), '%')}  "
            f"[dim](week {best_trade['week_date']})[/dim]\n"
            f"  Worst trade:         [red]{worst_trade['ticker']}[/red]  "
            f"{_fmt_profit(float(worst_trade['net_profit_pct']), '%')}  "
            f"[dim](week {worst_trade['week_date']})[/dim]",
            title="[bold]Overall Summary[/bold]",
            box=box.ROUNDED,
        ))
    else:
        console.print(Panel(
            f"  No closed trades yet.\n"
            f"  Open trades: {len(open_t)}"
            + (f"  (£{open_invested:,.2f} currently invested)" if open_t else ""),
            title="[bold]Overall Summary[/bold]",
            box=box.ROUNDED,
        ))


# ── Entry point ───────────────────────────────────────────────────────────────

def run_trade_tracker():
    """Sub-menu for the trade tracker within History mode."""
    while True:
        console.print(
            "\n[bold cyan]Actual Trade Tracker[/bold cyan]\n"
            "[dim]Track your real buy and sell prices to compare against\n"
            "the model's predicted returns.[/dim]\n\n"
            "  [B] Log a buy\n"
            "  [S] Log a sell\n"
            "  [V] View summary\n"
            "  [Q] Quit\n"
        )
        raw = input("  Choose (B / S / V / Q): ").strip().upper()
        if raw in ("B", "BUY"):
            log_buy()
        elif raw in ("S", "SELL"):
            log_sell()
        elif raw in ("V", "VIEW"):
            view_trade_summary()
        elif raw in ("Q", "QUIT", "EXIT"):
            return
        else:
            console.print("  [red]Please enter B, S, V, or Q[/red]")
