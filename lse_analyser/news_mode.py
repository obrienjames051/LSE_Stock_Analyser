"""
news_mode.py
------------
Standalone news collection and sentiment dashboard.

Three features accessible from the main menu via [N] News mode:

  [M] Market scan
      Runs a macro search, all sector searches, and company-specific
      searches for the most frequently picked stocks. Saves everything
      to lse_news_standalone.csv. Useful on days you are not running
      live/preview mode so the news dataset stays continuous.

  [D] Sentiment dashboard
      Reads lse_news_standalone.csv and lse_news_log.csv to show the
      current macro and sector sentiment scores with a 3-point trend,
      freshness indicator, and the most recent headline driving each score.
      Scores are displayed as horizontal Rich progress bars.

  [C] Company search
      Fetches company-specific news for a single ticker you enter,
      shows company and sector sentiment in context, and saves the
      result to lse_news_standalone.csv.
"""

import csv
import json
import os
import requests
from collections import Counter
from datetime import datetime, timedelta

from rich.table import Table
from rich.panel import Panel
from rich import box

from .config import (
    CSV_FILE, NEWS_STANDALONE_FILE, NEWS_STANDALONE_HEADERS,
    NEWS_MARKET_SCAN_TOP_N, SECTOR_QUERIES, ANTHROPIC_API_KEY,
)
from .utils import console
from .macro import fetch_macro_sentiment, fetch_sector_sentiment
from .news import fetch_news_sentiment


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _append_rows(rows: list):
    """Append a list of row dicts to lse_news_standalone.csv."""
    file_exists = os.path.isfile(NEWS_STANDALONE_FILE)
    with open(NEWS_STANDALONE_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=NEWS_STANDALONE_HEADERS, extrasaction="ignore"
        )
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def _load_all_news() -> list:
    """
    Load rows from both lse_news_standalone.csv and lse_news_log.csv,
    combining them into a single list for dashboard analysis.
    """
    rows = []
    for fpath in (NEWS_STANDALONE_FILE, "lse_news_log.csv"):
        if os.path.isfile(fpath):
            with open(fpath, "r", newline="", encoding="utf-8") as f:
                rows.extend(csv.DictReader(f))
    return rows


def _build_row(run_date: str, mode: str, ticker: str, sector: str,
               company: dict, sector_s: dict, macro: dict) -> dict:
    """Build a single row dict for the standalone log."""
    return {
        "run_date":               run_date,
        "mode":                   mode,
        "ticker":                 ticker,
        "sector":                 sector,
        "company_news_score":     round(company.get("score", 0), 4) if company.get("available") else "",
        "company_news_available": company.get("available", False),
        "company_headlines":      " | ".join(company.get("headlines", [])[:5]),
        "sector_news_score":      round(sector_s.get("score", 0), 4) if sector_s.get("available") else "",
        "sector_news_available":  sector_s.get("available", False),
        "sector_headlines":       " | ".join(sector_s.get("headlines", [])[:3]),
        "macro_score":            round(macro.get("score", 0), 4) if macro.get("available") else "",
        "macro_event":            macro.get("event_label", ""),
        "macro_available":        macro.get("available", False),
        "macro_headlines":        " | ".join(macro.get("headlines", [])[:5]),
    }


# ── Frequent tickers ──────────────────────────────────────────────────────────

def _get_frequent_tickers(n: int) -> list:
    """
    Return up to n tickers from lse_screener_log.csv ranked by pick frequency,
    with most-recent as tiebreaker. Returns list of (ticker, sector) tuples.
    """
    if not os.path.isfile(CSV_FILE):
        return []

    counts   = Counter()
    latest   = {}   # ticker -> most recent run_date string
    sectors  = {}   # ticker -> sector

    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = row.get("ticker", "").strip()
            d = row.get("run_date", "").strip()
            s = row.get("sector", "").strip()
            if t:
                counts[t]  += 1
                sectors[t]  = s
                if t not in latest or d > latest[t]:
                    latest[t] = d

    # Sort: primary = count desc, secondary = most recent date desc
    ranked = sorted(counts.keys(), key=lambda t: (counts[t], latest.get(t, "")), reverse=True)
    return [(t, sectors.get(t, "")) for t in ranked[:n]]


# ── Feature 1: Market scan ────────────────────────────────────────────────────

def run_market_scan():
    """
    Fetch macro, all sector, and top-company news. Save to standalone log.
    """
    console.rule("[bold cyan]Market Scan[/bold cyan]")
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows     = []

    # ── Macro ──────────────────────────────────────────────────────────────────
    with console.status("[bold green]Fetching macro news..."):
        macro = fetch_macro_sentiment()

    mc   = "bright_green" if macro.get("score", 0) > 0.1 else "red" if macro.get("score", 0) < -0.1 else "yellow"
    sign = "+" if macro.get("score", 0) >= 0 else ""
    console.print(
        f"  Macro: [{mc}]{sign}{macro.get('score', 0):+.3f}[/{mc}]  "
        f"[dim]{macro.get('event_label', '')}[/dim]"
    )

    # ── All sectors ────────────────────────────────────────────────────────────
    sector_cache = {}
    console.print(f"\n  Fetching {len(SECTOR_QUERIES)} sectors...\n")
    with console.status("[bold green]Fetching sector news...") as status:
        for sector, _ in SECTOR_QUERIES.items():
            status.update(f"[bold green]Fetching sector news: {sector}...")
            result = fetch_sector_sentiment(sector)
            sector_cache[sector] = result

            sc   = "bright_green" if result.get("score", 0) > 0.1 else "red" if result.get("score", 0) < -0.1 else "yellow"
            sign = "+" if result.get("score", 0) >= 0 else ""
            console.print(f"    {sector:<14} [{sc}]{sign}{result.get('score', 0):+.3f}[/{sc}]")

            # Sector-only row (no company)
            rows.append(_build_row(
                run_date, "market_scan", "", sector,
                {"available": False},
                result, macro,
            ))

    # ── Frequent companies ─────────────────────────────────────────────────────
    frequent = _get_frequent_tickers(NEWS_MARKET_SCAN_TOP_N)
    if frequent:
        console.print(f"\n  Fetching news for {len(frequent)} tracked companies...\n")
        with console.status("[bold green]Fetching company news...") as status:
            for ticker, sector in frequent:
                status.update(f"[bold green]Fetching company news: {ticker}...")
                company = fetch_news_sentiment(ticker + ".L")
                sec_s   = sector_cache.get(sector) or fetch_sector_sentiment(sector)
                sector_cache[sector] = sec_s

                sc   = "bright_green" if company.get("score", 0) > 0.05 else "red" if company.get("score", 0) < -0.05 else "dim"
                sign = "+" if company.get("score", 0) >= 0 else ""
                avail = f"[{sc}]{sign}{company.get('score', 0):+.3f}[/{sc}]" if company.get("available") else "[dim]no data[/dim]"
                console.print(f"    {ticker:<8}  {avail}")

                rows.append(_build_row(
                    run_date, "market_scan", ticker, sector,
                    company, sec_s, macro,
                ))
    else:
        console.print("\n  [dim]No pick history found — run in Live mode first to enable company tracking.[/dim]")

    _append_rows(rows)
    console.print(
        f"\n[green]Market scan complete.[/green]  "
        f"[dim]{len(rows)} rows saved to {NEWS_STANDALONE_FILE}[/dim]\n"
    )


# ── Feature 2: Sentiment dashboard ───────────────────────────────────────────

def _score_colour(score: float) -> str:
    if score >  0.15: return "bright_green"
    if score >  0.05: return "green"
    if score < -0.15: return "red"
    if score < -0.05: return "yellow"
    return "white"


def _trend_arrow(scores: list) -> str:
    """Given up to 3 recent scores (oldest first), return a trend symbol."""
    if len(scores) < 2:
        return "[dim]–[/dim]"
    delta = scores[-1] - scores[-2]
    if delta > 0.05:  return "[bright_green]↑[/bright_green]"
    if delta < -0.05: return "[red]↓[/red]"
    return "[yellow]→[/yellow]"


def _freshness(date_str: str) -> str:
    """Return a freshness label given a run_date string."""
    if not date_str:
        return "[dim]no data[/dim]"
    try:
        dt   = datetime.strptime(date_str[:10], "%Y-%m-%d")
        days = (datetime.now() - dt).days
        if days == 0: return "[bright_green]today[/bright_green]"
        if days == 1: return "[green]yesterday[/green]"
        if days <= 3: return f"[yellow]{days}d ago[/yellow]"
        return f"[red]{days}d ago[/red]"
    except ValueError:
        return "[dim]?[/dim]"


def _condense_headlines(headlines: list) -> list:
    """
    Use the Claude API to condense a list of headlines to 4-5 word summaries.
    Falls back to simple truncation if the API is unavailable or fails.

    Example:
      "Bank of England raises interest rates by 0.25% amid inflation concerns"
      -> "BoE raises rates 0.25%"
    """
    if not headlines:
        return []

    # Fallback: truncate to first 5 words
    def _truncate(h):
        words = h.split()
        return " ".join(words[:6]) + ("..." if len(words) > 6 else "")

    if not ANTHROPIC_API_KEY:
        return [_truncate(h) for h in headlines]

    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    prompt   = (
        "Condense each headline below to 4-6 words, keeping the key fact. "
        "Return ONLY a JSON array of strings in the same order, no other text.\n\n"
        f"{numbered}"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=10,
        )
        resp.raise_for_status()
        text    = resp.json()["content"][0]["text"].strip()
        # Strip any accidental markdown fences
        text    = text.strip("```json").strip("```").strip()
        condensed = json.loads(text)
        if isinstance(condensed, list) and len(condensed) == len(headlines):
            return [str(c) for c in condensed]
    except Exception:
        pass  # Fall through to truncation

    return [_truncate(h) for h in headlines]


def run_sentiment_dashboard():
    """
    Display macro and per-sector sentiment scores with trend and freshness,
    derived from both lse_news_standalone.csv and lse_news_log.csv.
    """
    console.rule("[bold cyan]Sentiment Dashboard[/bold cyan]")

    rows = _load_all_news()
    if not rows:
        console.print(Panel(
            "[yellow]No news data found.[/yellow]\n"
            "[dim]Run a Market Scan or use Live/Preview mode to collect data first.[/dim]",
            box=box.ROUNDED,
        ))
        return

    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [r for r in rows if r.get("run_date", "") >= cutoff]

    # ── Macro summary ──────────────────────────────────────────────────────────
    macro_rows = [r for r in recent if r.get("macro_available", "") in ("True", "1", True)]
    macro_rows.sort(key=lambda r: r.get("run_date", ""))

    if macro_rows:
        # Last 3 distinct macro scores by date
        seen_dates = []
        macro_scores = []
        for r in reversed(macro_rows):
            d = r.get("run_date", "")[:10]
            if d not in seen_dates:
                seen_dates.append(d)
                try:
                    macro_scores.insert(0, float(r.get("macro_score", 0)))
                except (ValueError, TypeError):
                    pass
            if len(seen_dates) == 3:
                break

        latest_macro  = macro_rows[-1]
        mscore        = macro_scores[-1] if macro_scores else 0
        mc            = _score_colour(mscore)
        trend         = _trend_arrow(macro_scores)
        fresh         = _freshness(latest_macro.get("run_date", ""))
        event         = latest_macro.get("macro_event", "")

        # Collect macro headlines from rows on the latest date
        latest_date   = latest_macro.get("run_date", "")[:10]
        raw_headlines = []
        for r in macro_rows:
            if r.get("run_date", "")[:10] == latest_date:
                for h in (r.get("macro_headlines") or "").split(" | "):
                    h = h.strip()
                    if h and h not in raw_headlines:
                        raw_headlines.append(h)
                if raw_headlines:
                    break  # one row is enough for macro headlines
        raw_headlines = raw_headlines[:5]

        with console.status("[dim]Condensing headlines...[/dim]"):
            condensed = _condense_headlines(raw_headlines)

        heads_text = ""
        for h in condensed:
            heads_text += f"\n  [dim]• {h}[/dim]"

        console.print(Panel(
            f"  Score:   [{mc}]{mscore:+.3f}[/{mc}]  {trend}  {fresh}\n"
            f"  Event:   [dim]{event if event else 'No significant event detected'}[/dim]"
            + heads_text,
            title="[bold]Macro Sentiment[/bold]",
            box=box.ROUNDED,
        ))
    else:
        console.print(Panel("[dim]No macro data in last 7 days.[/dim]",
                            title="[bold]Macro Sentiment[/bold]", box=box.ROUNDED))

    # ── Sector table ───────────────────────────────────────────────────────────
    console.print()
    # Seed all known sectors so sectors with no data still appear in the table
    sector_data = {s: {"entries": [], "headline": ""} for s in SECTOR_QUERIES.keys()}

    for r in recent:
        sec = r.get("sector", "").strip()
        if not sec or sec not in sector_data:
            continue
        avail = r.get("sector_news_available", "")
        if avail not in ("True", "1", True):
            continue
        try:
            score = float(r.get("sector_news_score", ""))
        except (ValueError, TypeError):
            continue

        sector_data[sec]["entries"].append((r.get("run_date", ""), score))
        if r.get("sector_headlines"):
            sector_data[sec]["headline"] = r["sector_headlines"].split(" | ")[0]

    if not any(d["entries"] for d in sector_data.values()):
        console.print("[dim]No sector data in last 7 days.[/dim]\n")
        return

    # Condense only sectors that have a headline (single API call)
    all_raw_headlines = [sector_data[s].get("headline", "") for s in sorted(sector_data.keys())]
    with console.status("[dim]Condensing sector headlines...[/dim]"):
        all_condensed = _condense_headlines([h for h in all_raw_headlines if h])

    # Map back: build a condensed headline per sector in order
    condensed_iter = iter(all_condensed)
    condensed_by_sector = {}
    for s in sorted(sector_data.keys()):
        raw = sector_data[s].get("headline", "")
        condensed_by_sector[s] = next(condensed_iter) if raw else ""

    table = Table(
        title="[bold]Sector Sentiment (last 7 days)[/bold]",
        box=box.ROUNDED, show_lines=False,
        header_style="bold magenta", style="cyan",
    )
    table.add_column("Sector",      width=14, style="bold white")
    table.add_column("Score",       width=8,  justify="right")
    table.add_column("Trend",       width=6,  justify="center")
    table.add_column("Bar",         width=22)
    table.add_column("Fresh",       width=12)
    table.add_column("Latest headline", min_width=30, no_wrap=False)

    BAR_FULL  = 10   # total bar width each side
    SCALE     = 1.0  # scores range -1.0 to +1.0

    for sector in sorted(sector_data.keys()):
        data    = sector_data[sector]
        entries = sorted(data["entries"], key=lambda x: x[0])

        # No data available for this sector
        if not entries:
            table.add_row(
                sector,
                "[dim]–[/dim]",
                "[dim]–[/dim]",
                " " * (BAR_FULL * 2),
                "[dim]–[/dim]",
                "[dim]no data[/dim]",
            )
            continue

        # Last 3 distinct-date scores for trend
        seen   = []
        scores = []
        for date, sc in reversed(entries):
            d = date[:10]
            if d not in seen:
                seen.append(d)
                scores.insert(0, sc)
            if len(seen) == 3:
                break

        latest_score = scores[-1]
        latest_date  = entries[-1][0]
        cc           = _score_colour(latest_score)
        trend        = _trend_arrow(scores)
        fresh        = _freshness(latest_date)
        sign         = "+" if latest_score >= 0 else ""

        # ASCII bar: negative fills left, positive fills right, centre = 0
        filled = min(BAR_FULL, int(abs(latest_score) / SCALE * BAR_FULL))
        if latest_score >= 0:
            bar = " " * BAR_FULL + f"[{cc}]" + "█" * filled + "░" * (BAR_FULL - filled) + f"[/{cc}]"
        else:
            bar = f"[{cc}]" + "░" * (BAR_FULL - filled) + "█" * filled + f"[/{cc}]" + " " * BAR_FULL

        headline = condensed_by_sector.get(sector, "")

        table.add_row(
            sector,
            f"[{cc}]{sign}{latest_score:.3f}[/{cc}]",
            trend,
            bar,
            fresh,
            f"[dim]{headline}[/dim]" if headline else "[dim]–[/dim]",
        )

    console.print(table)
    console.print()


# ── Feature 3: Company search ─────────────────────────────────────────────────

def run_company_search():
    """
    Fetch and display news for a user-specified ticker, show alongside
    sector context, and save to the standalone log.
    """
    console.rule("[bold cyan]Company News Search[/bold cyan]")

    ticker_raw = input("\n  Enter ticker (e.g. BARC, VOD, SHEL): ").strip().upper()
    if not ticker_raw:
        console.print("[yellow]No ticker entered.[/yellow]\n")
        return

    # Normalise — strip .L if user added it
    ticker = ticker_raw.replace(".L", "")

    # Try to find sector from pick history; fall back to asking
    sector = ""
    if os.path.isfile(CSV_FILE):
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("ticker", "").strip().upper() == ticker:
                    sector = row.get("sector", "").strip()

    if not sector:
        sector_options = sorted(SECTOR_QUERIES.keys())
        console.print("\n  [dim]Sector not found in pick history. Please select:[/dim]\n")
        for i, s in enumerate(sector_options, 1):
            console.print(f"    [bold white]{i:>2}.[/bold white]  {s}")
        console.print()
        while True:
            try:
                raw = input(f"  Sector number (1-{len(sector_options)}): ").strip()
                idx = int(raw) - 1
                if 0 <= idx < len(sector_options):
                    sector = sector_options[idx]
                    break
                raise ValueError
            except ValueError:
                console.print(f"  [red]Please enter a number between 1 and {len(sector_options)}[/red]")

    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    with console.status(f"[bold green]Fetching news for {ticker}..."):
        company = fetch_news_sentiment(ticker + ".L")

    with console.status(f"[bold green]Fetching sector news: {sector}..."):
        sector_s = fetch_sector_sentiment(sector)

    with console.status("[bold green]Fetching macro news..."):
        macro = fetch_macro_sentiment()

    # ── Display ────────────────────────────────────────────────────────────────
    def _fmt_score(d: dict, label: str) -> str:
        if not d.get("available"):
            return f"  {label:<16} [dim]No data available[/dim]"
        sc   = d.get("score", 0)
        cc   = _score_colour(sc)
        sign = "+" if sc >= 0 else ""
        heads = d.get("headlines", [])
        out  = f"  {label:<16} [{cc}]{sign}{sc:.3f}[/{cc}]\n"
        for h in heads[:3]:
            out += f"    [dim]• {h}[/dim]\n"
        return out.rstrip()

    macro_sc   = macro.get("score", 0)
    macro_cc   = _score_colour(macro_sc)
    macro_sign = "+" if macro_sc >= 0 else ""

    console.print(Panel(
        _fmt_score(company,  "Company news:") + "\n\n" +
        _fmt_score(sector_s, "Sector news:")  + "\n\n" +
        f"  {'Macro news:':<16} [{macro_cc}]{macro_sign}{macro_sc:.3f}[/{macro_cc}]"
        + (f"  [dim]{macro.get('event_label', '')}[/dim]" if macro.get("available") else "  [dim]No data[/dim]"),
        title=f"[bold yellow]{ticker}[/bold yellow]  [dim]{sector}[/dim]",
        box=box.ROUNDED,
    ))

    # ── Save ───────────────────────────────────────────────────────────────────
    row = _build_row(run_date, "company_search", ticker, sector, company, sector_s, macro)
    _append_rows([row])
    console.print(f"[dim]Saved to {NEWS_STANDALONE_FILE}[/dim]\n")


# ── Sub-menu entry point ──────────────────────────────────────────────────────

def run_news_mode():
    """Main entry point for News mode — loops until user quits."""
    while True:
        console.print(
            "\n[bold cyan]News Mode[/bold cyan]\n"
            "[dim]  [M] Market scan      -- fetch macro, all sectors, and tracked companies\n"
            "  [D] Dashboard        -- current sentiment scores and trends\n"
            "  [C] Company search   -- look up a specific stock's news\n"
            "  [Q] Quit[/dim]\n"
        )
        raw = input("  Choose (M / D / C / Q): ").strip().upper()
        if raw in ("M", "MARKET"):
            run_market_scan()
        elif raw in ("D", "DASHBOARD"):
            run_sentiment_dashboard()
        elif raw in ("C", "COMPANY"):
            run_company_search()
        elif raw in ("Q", "QUIT", "EXIT"):
            return
        else:
            console.print("  [red]Please enter M, D, C, or Q[/red]")
