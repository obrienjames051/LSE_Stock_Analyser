"""
backtest.py
-----------
Backtesting engine for the LSE Analyser.

Runs two phases of historical simulation to bootstrap the calibration
system without waiting weeks for live picks to accumulate:

Phase 1 -- Technical backtest (default: 52 weeks)
  Simulates weekly runs using only technical scoring on historical price
  data. News/macro sentiment is excluded because historical article data
  is not reliably available. Results stored in lse_backtest_technical.csv.
  Calibration weight: 0.6 (genuinely historical but news-blind)

Phase 2 -- News-enhanced backtest (default: 4 weeks)
  Reruns the last 4 weeks with company, sector, and macro news included.
  Uses NewsAPI with a date filter, but note that the articles returned
  reflect today's index filtered by date -- not a true reconstruction of
  what was available at that exact point in time. Results stored in
  lse_backtest_news.csv. Calibration weight: 0.3 (full model but
  news data is approximate)

Both CSV files use the same column structure as lse_screener_log.csv so
the calibration engine can read all three sources consistently.

The backtest is designed to be run once or twice to bootstrap calibration,
then left alone as live picks accumulate. Once CALIBRATION_LIVE_THRESHOLD
live picks are resolved, the calibration engine phases out backtest data
automatically.
"""

import csv
import os
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from .config import (
    TOP_N, CSV_HEADERS,
    BACKTEST_TECHNICAL_CSV, BACKTEST_NEWS_CSV,
    BACKTEST_WEEKS_TECHNICAL, BACKTEST_WEEKS_NEWS,
)
from .utils import console, silent
from .tickers import get_tickers
from .screener import diversify
from .news import fetch_news_sentiment, apply_news_adjustment
from .macro import (
    fetch_macro_sentiment, fetch_sector_sentiment, apply_macro_to_pick,
)


# ── Public API ────────────────────────────────────────────────────────────────

def run_backtest():
    """
    Entry point for backtest mode.
    Runs Phase 1 (technical) then Phase 2 (news-enhanced) and saves results.
    """
    console.rule("[bold cyan]Backtest Mode[/bold cyan]")
    console.print(
        "\n[dim]This will simulate historical weekly runs to bootstrap the\n"
        "calibration system. Phase 1 covers the last 52 weeks using technical\n"
        "analysis only. Phase 2 covers the last 4 weeks with news sentiment\n"
        "included (approximate -- see README for limitations).\n\n"
        "This may take 10-20 minutes to complete.[/dim]\n"
    )

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        console.print("[yellow]Backtest cancelled.[/yellow]\n")
        return

    tickers = get_tickers()
    source  = getattr(get_tickers, "_source", f"{len(tickers)} stocks")
    console.print(f"[dim]Ticker universe: {source}[/dim]\n")

    # Phase 1: technical-only backtest
    console.rule("[bold]Phase 1 — Technical Backtest (52 weeks)[/bold]")
    phase1_results = _run_technical_backtest(tickers, BACKTEST_WEEKS_TECHNICAL)
    _save_backtest_results(phase1_results, BACKTEST_TECHNICAL_CSV)
    console.print(
        f"[green]Phase 1 complete.[/green] "
        f"{len(phase1_results)} simulated picks saved to {BACKTEST_TECHNICAL_CSV}\n"
    )

    # Phase 2: news-enhanced backtest
    console.rule("[bold]Phase 2 — News-Enhanced Backtest (4 weeks)[/bold]")
    console.print(
        "[dim]Note: NewsAPI articles are filtered by date but reflect today's\n"
        "index -- not a perfect reconstruction of historical news.[/dim]\n"
    )
    phase2_results = _run_news_backtest(tickers, BACKTEST_WEEKS_NEWS)
    _save_backtest_results(phase2_results, BACKTEST_NEWS_CSV)
    console.print(
        f"[green]Phase 2 complete.[/green] "
        f"{len(phase2_results)} simulated picks saved to {BACKTEST_NEWS_CSV}\n"
    )

    # Print summary
    _print_backtest_summary(phase1_results, phase2_results)


# ── Phase 1: technical backtest ───────────────────────────────────────────────

def _run_technical_backtest(tickers: dict, n_weeks: int) -> list:
    """
    Simulate n_weeks of weekly technical-only picks on historical data.
    Returns a list of result dicts with outcome columns filled in.
    """
    all_results = []
    now         = datetime.now()

    # Download full price history for all tickers once upfront
    # to avoid hundreds of individual API calls
    console.print("[dim]Downloading historical price data (this may take a few minutes)...[/dim]")
    price_data = _download_all_prices(tickers)
    console.print(f"[dim]Price data downloaded for {len(price_data)} tickers.[/dim]\n")

    for week_offset in range(n_weeks, 0, -1):
        sim_date    = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=7)

        # Skip if outcome date is in the future
        if outcome_date > now:
            continue

        sim_date_str = sim_date.strftime("%Y-%m-%d")
        console.print(f"  [dim]Simulating week of {sim_date_str}...[/dim]", end="\r")

        week_picks = _score_week_technical(
            tickers, price_data, sim_date, outcome_date
        )

        if week_picks:
            all_results.extend(week_picks)

    console.print()
    return all_results


def _score_week_technical(
    tickers: dict, price_data: dict, sim_date: datetime, outcome_date: datetime
) -> list:
    """
    Score all tickers as of sim_date using technical indicators only.
    Returns the top TOP_N diversified picks with outcomes filled in.
    """
    from .config import ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER
    import numpy as np

    results = []
    sim_date_pd = pd.Timestamp(sim_date)

    for ticker, sector in tickers.items():
        df = price_data.get(ticker)
        if df is None or len(df) < 30:
            continue

        # Slice data to only what was available at sim_date
        try:
            hist = df[df.index <= sim_date_pd].copy()
        except Exception:
            continue

        if len(hist) < 30:
            continue

        try:
            r = _score_historical(ticker, sector, hist, ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER)
            if r:
                results.append(r)
        except Exception:
            continue

    if not results:
        return []

    results.sort(key=lambda x: x["score"], reverse=True)
    top = diversify(results, TOP_N)

    # Fill in outcomes
    outcome_date_pd = pd.Timestamp(outcome_date)
    for r in top:
        df = price_data.get(r["ticker"] + ".L") or price_data.get(r["ticker"])
        if df is None:
            continue
        try:
            future = df[df.index > sim_date_pd]
            if future.empty:
                continue
            # Find closest available price to outcome_date
            closest = future[future.index <= outcome_date_pd]
            if closest.empty:
                closest = future.head(1)
            outcome_price = float(closest["close"].iloc[-1])
            entry_price   = r["price"]
            target_price  = r["target"]
            return_pct    = (outcome_price - entry_price) / entry_price * 100
            hit           = "YES" if outcome_price >= target_price else "NO"

            r["outcome_price_p"]    = round(outcome_price, 2)
            r["outcome_hit"]        = hit
            r["outcome_return_pct"] = round(return_pct, 2)
            r["outcome_notes"]      = (
                f"Backtest: {'Target reached' if hit == 'YES' else 'Target missed'}. "
                f"{return_pct:+.1f}%"
            )
            r["run_date"] = sim_date.strftime("%Y-%m-%d %H:%M")
        except Exception:
            continue

    return [r for r in top if r.get("outcome_hit")]


def _score_historical(ticker, sector, hist, atr_mult, stop_mult, limit_buf):
    """
    Compute technical score for a ticker using a historical price slice.
    Mirrors score_ticker() in screener.py but works on a pre-sliced dataframe.
    """
    from ta.momentum import RSIIndicator, StochasticOscillator
    from ta.trend import MACD, EMAIndicator, SMAIndicator
    from ta.volatility import BollingerBands, AverageTrueRange
    from ta.volume import OnBalanceVolumeIndicator

    close, high, low, vol = hist["close"], hist["high"], hist["low"], hist["volume"]

    # Volume filter
    recent        = hist.tail(20)
    avg_value_gbp = (recent["close"] * recent["volume"] / 100).mean()
    from .config import MIN_AVG_VOLUME_GBP
    if avg_value_gbp < MIN_AVG_VOLUME_GBP:
        return None

    rsi       = RSIIndicator(close, window=14).rsi()
    macd_obj  = MACD(close)
    macd_hist = macd_obj.macd_diff()
    bb        = BollingerBands(close, window=20, window_dev=2)
    bb_pct    = bb.bollinger_pband()
    ema20     = EMAIndicator(close, window=20).ema_indicator()
    ema50     = EMAIndicator(close, window=50).ema_indicator()
    sma200    = SMAIndicator(close, window=min(200, len(close)-1)).sma_indicator()
    atr       = AverageTrueRange(high, low, close, window=14).average_true_range()
    obv       = OnBalanceVolumeIndicator(close, vol).on_balance_volume()
    stoch     = StochasticOscillator(high, low, close, window=14, smooth_window=3)
    stoch_k   = stoch.stoch()
    stoch_d   = stoch.stoch_signal()

    c        = float(close.iloc[-1])
    r        = float(rsi.iloc[-1])
    mh       = float(macd_hist.iloc[-1])
    mh_p     = float(macd_hist.iloc[-2])
    bp       = float(bb_pct.iloc[-1])
    e20      = float(ema20.iloc[-1])
    e50      = float(ema50.iloc[-1])
    s200     = float(sma200.iloc[-1])
    atr_v    = float(atr.iloc[-1])
    sk       = float(stoch_k.iloc[-1])
    sd       = float(stoch_d.iloc[-1])
    obv_slope = (float(obv.iloc[-1]) - float(obv.iloc[-10])) / (abs(float(obv.iloc[-10])) + 1)
    mom5     = (c - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

    score, signals = 0, []

    if 40 <= r <= 65:
        score += 20; signals.append(f"RSI {r:.0f}")
    elif r < 35:
        score += 10; signals.append(f"RSI {r:.0f} oversold")

    if mh > 0 and mh_p < 0:
        score += 25; signals.append("MACD bullish crossover")
    elif mh > mh_p and mh > 0:
        score += 15; signals.append("MACD rising")

    if 0.2 <= bp <= 0.5:
        score += 15; signals.append(f"BB %B {bp:.2f}")
    elif bp < 0.2:
        score += 8; signals.append(f"BB %B {bp:.2f} near lower band")

    if e20 > e50 > s200:
        score += 20; signals.append("EMA20>EMA50>SMA200")
    elif e20 > e50:
        score += 10; signals.append("EMA20>EMA50")

    if sk > sd and 20 < sk < 80:
        score += 10; signals.append(f"Stoch {sk:.0f}/{sd:.0f}")

    if obv_slope > 0.01:
        score += 10; signals.append("OBV rising")

    if 0.5 <= mom5 <= 4.0:
        score += 10; signals.append(f"Mom +{mom5:.1f}%")
    elif -1.0 <= mom5 < 0:
        score += 5; signals.append(f"Mom {mom5:.1f}%")

    target       = round(c + atr_mult * atr_v, 2)
    stop         = round(c - stop_mult * atr_v, 2)
    limit        = round(target * limit_buf, 2)
    upside_pct   = (target - c) / c * 100
    downside_pct = (c - stop) / c * 100
    prob         = round(min(78.0, max(20.0, 35.0 + (score / 110) * 40)), 1)

    return {
        "ticker":       ticker.replace(".L", ""),
        "sector":       sector,
        "score":        score,
        "price":        c,
        "target":       target,
        "stop":         stop,
        "limit":        limit,
        "upside_pct":   upside_pct,
        "downside_pct": downside_pct,
        "prob":         prob,
        "signals":      signals,
        "atr":          round(atr_v, 4),
        "reward_risk":  round(upside_pct / downside_pct, 2) if downside_pct > 0 else 0,
        "allocated_gbp": "",
        "shares":        "",
        "outcome_price_p":    "",
        "outcome_hit":        "",
        "outcome_return_pct": "",
        "outcome_notes":      "",
        "run_date":           "",
    }


# ── Phase 2: news-enhanced backtest ──────────────────────────────────────────

def _run_news_backtest(tickers: dict, n_weeks: int) -> list:
    """
    Simulate the last n_weeks of weekly runs with news sentiment included.
    Uses the same technical scoring as Phase 1, then adds news/macro layers.
    """
    price_data  = _download_all_prices(tickers, period="35d")
    all_results = []
    now         = datetime.now()

    for week_offset in range(n_weeks, 0, -1):
        sim_date     = now - timedelta(weeks=week_offset)
        outcome_date = sim_date + timedelta(days=7)

        if outcome_date > now:
            continue

        sim_date_str = sim_date.strftime("%Y-%m-%d")
        console.print(f"  [dim]Simulating week of {sim_date_str} (with news)...[/dim]")

        # Technical scoring
        results = []
        sim_date_pd = pd.Timestamp(sim_date)
        from .config import ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER

        for ticker, sector in tickers.items():
            df = price_data.get(ticker)
            if df is None or len(df) < 30:
                continue
            try:
                hist = df[df.index <= sim_date_pd].copy()
                if len(hist) < 30:
                    continue
                r = _score_historical(
                    ticker, sector, hist, ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER
                )
                if r:
                    results.append(r)
            except Exception:
                continue

        if not results:
            continue

        results.sort(key=lambda x: x["score"], reverse=True)
        candidates = results[:20]

        # Company news
        with console.status("[dim]Fetching company news...[/dim]"):
            for r in candidates:
                sentiment = fetch_news_sentiment(r["ticker"] + ".L")
                apply_news_adjustment(r, sentiment)

        # Macro + sector news
        macro        = fetch_macro_sentiment()
        sector_cache = {}
        for r in candidates:
            sector = r["sector"]
            if sector not in sector_cache:
                sector_cache[sector] = fetch_sector_sentiment(sector)
            apply_macro_to_pick(r, macro, sector_cache[sector])

        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = diversify(candidates, TOP_N)

        # Fill outcomes
        outcome_date_pd = pd.Timestamp(outcome_date)
        for r in top:
            df = price_data.get(r["ticker"] + ".L") or price_data.get(r["ticker"])
            if df is None:
                continue
            try:
                future  = df[df.index > sim_date_pd]
                if future.empty:
                    continue
                closest = future[future.index <= outcome_date_pd]
                if closest.empty:
                    closest = future.head(1)
                outcome_price = float(closest["close"].iloc[-1])
                entry_price   = r["price"]
                return_pct    = (outcome_price - entry_price) / entry_price * 100
                hit           = "YES" if outcome_price >= r["target"] else "NO"

                r["outcome_price_p"]    = round(outcome_price, 2)
                r["outcome_hit"]        = hit
                r["outcome_return_pct"] = round(return_pct, 2)
                r["outcome_notes"]      = (
                    f"Backtest+news: {'Target reached' if hit == 'YES' else 'Target missed'}. "
                    f"{return_pct:+.1f}%"
                )
                r["run_date"] = sim_date.strftime("%Y-%m-%d %H:%M")
            except Exception:
                continue

        all_results.extend([r for r in top if r.get("outcome_hit")])

    return all_results


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _download_all_prices(tickers: dict, period: str = "400d") -> dict:
    """
    Batch download price history for all tickers.
    Returns dict of {ticker: DataFrame}.
    """
    price_data = {}
    ticker_list = list(tickers.keys())

    # Download in batches of 50 to avoid yfinance timeouts
    batch_size = 50
    for i in range(0, len(ticker_list), batch_size):
        batch = ticker_list[i:i+batch_size]
        try:
            with silent():
                raw = yf.download(
                    batch, period=period, interval="1d",
                    progress=False, auto_adjust=True, group_by="ticker",
                )
            for ticker in batch:
                try:
                    if len(batch) == 1:
                        df = raw.copy()
                    else:
                        df = raw[ticker].copy() if ticker in raw.columns.get_level_values(0) else None
                    if df is not None and len(df) >= 30:
                        df.columns = [
                            c[0].lower() if isinstance(c, tuple) else c.lower()
                            for c in df.columns
                        ]
                        df.dropna(inplace=True)
                        price_data[ticker] = df
                except Exception:
                    continue
        except Exception:
            continue

    return price_data


def _save_backtest_results(results: list, filepath: str):
    """Save backtest results to a CSV file using the standard CSV_HEADERS format."""
    if not results:
        console.print(f"[yellow]No results to save to {filepath}[/yellow]")
        return

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow({
                "run_date":           r.get("run_date", ""),
                "ticker":             r.get("ticker", ""),
                "sector":             r.get("sector", ""),
                "score":              r.get("score", ""),
                "price_p":            r.get("price", ""),
                "target_p":           r.get("target", ""),
                "stop_p":             r.get("stop", ""),
                "limit_p":            r.get("limit", ""),
                "upside_pct":         round(r["upside_pct"], 2) if r.get("upside_pct") else "",
                "downside_pct":       round(r["downside_pct"], 2) if r.get("downside_pct") else "",
                "prob":               r.get("prob", ""),
                "reward_risk":        r.get("reward_risk", ""),
                "atr":                r.get("atr", ""),
                "allocated_gbp":      "",
                "shares":             "",
                "signals":            " | ".join(r["signals"]) if r.get("signals") else "",
                "outcome_price_p":    r.get("outcome_price_p", ""),
                "outcome_hit":        r.get("outcome_hit", ""),
                "outcome_return_pct": r.get("outcome_return_pct", ""),
                "outcome_notes":      r.get("outcome_notes", ""),
            })


def _print_backtest_summary(phase1: list, phase2: list):
    """Print a summary of backtest results."""
    from rich.panel import Panel
    from rich import box

    def _stats(results, label):
        resolved = [r for r in results if r.get("outcome_hit") in ("YES", "NO")]
        if not resolved:
            return f"  {label}: no resolved picks"
        hits      = sum(1 for r in resolved if r["outcome_hit"] == "YES")
        hit_rate  = hits / len(resolved) * 100
        returns   = [float(r["outcome_return_pct"]) for r in resolved if r.get("outcome_return_pct") != ""]
        avg_ret   = sum(returns) / len(returns) if returns else 0
        hc = "bright_green" if hit_rate >= 50 else "yellow" if hit_rate >= 35 else "red"
        rc = "bright_green" if avg_ret >= 0 else "red"
        return (
            f"  {label}: [{hc}]{hit_rate:.1f}% hit rate[/{hc}]  |  "
            f"[{rc}]{avg_ret:+.2f}% avg return[/{rc}]  |  "
            f"{len(resolved)} picks"
        )

    console.print(Panel(
        f"[bold]Backtest Complete[/bold]\n\n"
        f"{_stats(phase1, 'Phase 1 (technical)')}\n"
        f"{_stats(phase2, 'Phase 2 (news-enhanced)')}\n\n"
        f"[dim]Results saved to {BACKTEST_TECHNICAL_CSV} and {BACKTEST_NEWS_CSV}.\n"
        f"The calibration engine will incorporate these on the next run.\n"
        f"Backtest data will be phased out automatically once you have\n"
        f"enough live picks.[/dim]",
        title="Backtest Summary",
        box=box.ROUNDED,
    ))
