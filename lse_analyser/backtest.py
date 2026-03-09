"""
backtest.py
-----------
Backtesting engine for the LSE Analyser.

Simulates historical weekly runs using technical scoring on historical price
data. News/macro sentiment is excluded because historical article data is not
reliably available.

Strategy modelled (confirmed in RESEARCH.md):
  - Programme is run on Tuesday morning before market open
  - Data available: up to and including Monday close
  - Entry price: Tuesday open
  - Stop price:  Monday close - 1x ATR  (known before Tuesday open)
  - Stop check:  daily close, Tuesday through Friday
                 If any close <= stop price, exit at that close
  - Normal exit: Monday close of following week
  - No upper limits

Results are saved to lse_backtest_technical.csv. The calibration engine
reads this file to bootstrap probability correction before live picks
accumulate. Once CALIBRATION_LIVE_THRESHOLD live picks are resolved,
backtest data is phased out automatically.
"""

import csv
import os
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from .config import (
    TOP_N, CSV_HEADERS,
    BACKTEST_TECHNICAL_CSV,
    BACKTEST_WEEKS_TECHNICAL, BACKTEST_CAPITAL,
    ATR_MULTIPLIER, STOP_MULTIPLIER,
)
from .utils import console, silent
from .tickers import get_tickers
from .screener import diversify
from .sizing import calculate_allocations


# ── Public API ────────────────────────────────────────────────────────────────

def run_backtest():
    """
    Entry point for backtest mode.
    Runs Phase 1 (technical only, Tuesday open -> Monday close) and saves results.
    """
    console.rule("[bold cyan]Backtest Mode[/bold cyan]")
    console.print(
        "\n[dim]This will simulate historical weekly runs to bootstrap the\n"
        "calibration system. It covers approximately 52 weeks using technical\n"
        "analysis only, with the confirmed strategy:\n\n"
        "  Entry:  Tuesday open\n"
        "  Exit:   Monday close (following week)\n"
        "  Stop:   Monday close - 1x ATR, checked each close Tue-Fri\n"
        "  Limits: None\n\n"
        "News/macro sentiment is excluded from the backtest because\n"
        "historical article data is not reliably available.\n\n"
        "This may take 10-15 minutes to complete.[/dim]\n"
    )

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        console.print("[yellow]Backtest cancelled.[/yellow]\n")
        return

    tickers = get_tickers()
    source  = getattr(get_tickers, "_source", f"{len(tickers)} stocks")
    console.print(f"[dim]Ticker universe: {source}[/dim]\n")

    console.rule("[bold]Phase 1 — Technical Backtest[/bold]")
    results = _run_technical_backtest(tickers, BACKTEST_WEEKS_TECHNICAL)
    _save_backtest_results(results, BACKTEST_TECHNICAL_CSV)
    console.print(
        f"[green]Backtest complete.[/green] "
        f"{len(results)} simulated picks saved to {BACKTEST_TECHNICAL_CSV}\n"
    )

    _print_backtest_summary(results)


# ── Phase 1: technical backtest ───────────────────────────────────────────────

def _run_technical_backtest(tickers: dict, n_weeks: int) -> list:
    """
    Simulate n_weeks of weekly picks on historical data.
    Each week: score on Monday close data, enter Tuesday open, exit Monday close.
    """
    all_results = []
    from .config import BACKTEST_END_DATE
    end_monday = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")

    console.print("[dim]Downloading historical price data (this may take a few minutes)...[/dim]")
    price_data = _download_all_prices(tickers)
    console.print(f"[dim]Price data downloaded for {len(price_data)} tickers.[/dim]\n")

    for week_offset in range(n_weeks, 0, -1):
        sim_monday    = end_monday - timedelta(weeks=week_offset)
        entry_tuesday = sim_monday + timedelta(days=1)
        exit_monday   = sim_monday + timedelta(days=8)

        if exit_monday > end_monday:
            continue

        console.print(f"  [dim]Simulating week of {sim_monday.strftime('%Y-%m-%d')}...[/dim]", end="\r")

        week_picks = _score_week(tickers, price_data, sim_monday, entry_tuesday, exit_monday)
        if week_picks:
            all_results.extend(week_picks)

    console.print()
    return all_results


def _score_week(tickers, price_data, sim_monday, entry_tuesday, exit_monday):
    """
    Score all tickers using data up to sim_monday close.
    Enter at entry_tuesday open, check stops daily, exit at exit_monday close.
    """
    results       = []
    sim_monday_pd = pd.Timestamp(sim_monday)

    for ticker, sector in tickers.items():
        # Tickers already include .L suffix in the universe dict
        df = price_data.get(ticker)
        if df is None or len(df) < 30:
            continue

        try:
            hist = df[df.index <= sim_monday_pd].copy()
        except Exception:
            continue
        if len(hist) < 30:
            continue

        try:
            r = _score_historical(ticker, sector, hist)
            if r:
                results.append(r)
        except Exception:
            continue

    if not results:
        return []

    results.sort(key=lambda x: x["score"], reverse=True)
    top = diversify(results, TOP_N)

    entry_tuesday_pd = pd.Timestamp(entry_tuesday)
    exit_monday_pd   = pd.Timestamp(exit_monday)

    resolved = []
    for r in top:
        df = price_data.get(r["ticker"] + ".L")
        if df is None:
            df = price_data.get(r["ticker"])
        if df is None:
            continue
        try:
            outcome = _simulate_trade(df, r, entry_tuesday_pd, exit_monday_pd)
            if outcome:
                r.update(outcome)
                r["run_date"] = sim_monday.strftime("%Y-%m-%d %H:%M")
                resolved.append(r)
        except Exception:
            continue

    if resolved:
        calculate_allocations(resolved, BACKTEST_CAPITAL)
    return resolved


def _simulate_trade(df, r, entry_tuesday_pd, exit_monday_pd):
    """
    Simulate the trade:
      - Entry at Tuesday open
      - Stop = entry_price - 1x ATR (recalculated from Monday close data in r)
      - Check each close Tuesday through Friday; exit if close <= stop
      - Otherwise exit at Monday close
    Returns outcome dict or None if data unavailable.
    """
    tuesday_bars = df[df.index >= entry_tuesday_pd]
    if tuesday_bars.empty:
        return None

    entry_bar   = tuesday_bars.iloc[0]
    entry_price = float(entry_bar["open"])
    if entry_price <= 0:
        return None

    # Stop was calculated from Monday close data
    stop_price = r["stop"]

    window_bars = df[
        (df.index >= entry_tuesday_pd) & (df.index <= exit_monday_pd)
    ]
    if window_bars.empty:
        return None

    exit_price  = None
    exit_reason = None

    monday_close = float(window_bars["close"].iloc[-1])

    for idx, bar in window_bars.iterrows():
        day_close = float(bar["close"])

        if idx == window_bars.index[-1]:
            exit_price  = day_close
            exit_reason = "window_end"
            break

        if day_close <= stop_price:
            exit_price  = day_close
            exit_reason = "stop"
            break

    if exit_price is None:
        return None

    return_pct = (exit_price - entry_price) / entry_price * 100
    target_hit = "YES" if monday_close >= r["target"] else "NO"

    # went_up: did Monday close exceed entry? (raw directional accuracy -- display only)
    went_up = 1 if monday_close > entry_price else 0

    # profitable: did the actual exit price exceed entry? (used for calibration)
    # Accounts for stop-outs: a stock that recovered by Monday but was stopped
    # out at a loss correctly counts as unprofitable.
    profitable = 1 if exit_price > entry_price else 0

    return {
        "outcome_price_p":    round(exit_price, 2),
        "outcome_hit":        target_hit,
        "outcome_return_pct": round(return_pct, 2),
        "outcome_notes": (
            f"Backtest: {'Stop triggered' if exit_reason == 'stop' else 'Held to Monday close'}. "
            f"{return_pct:+.1f}%"
        ),
        "went_up":    went_up,
        "profitable": profitable,
    }


def _score_historical(ticker, sector, hist):
    """
    Compute technical score for a ticker using a historical price slice.
    """
    from ta.momentum import RSIIndicator, StochasticOscillator
    from ta.trend import MACD, EMAIndicator, SMAIndicator
    from ta.volatility import BollingerBands, AverageTrueRange
    from ta.volume import OnBalanceVolumeIndicator

    close, high, low, vol = hist["close"], hist["high"], hist["low"], hist["volume"]

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

    c         = float(close.iloc[-1])
    r_val     = float(rsi.iloc[-1])
    mh        = float(macd_hist.iloc[-1])
    mh_p      = float(macd_hist.iloc[-2])
    bp        = float(bb_pct.iloc[-1])
    e20       = float(ema20.iloc[-1])
    e50       = float(ema50.iloc[-1])
    s200      = float(sma200.iloc[-1])
    atr_v     = float(atr.iloc[-1])
    sk        = float(stoch_k.iloc[-1])
    sd        = float(stoch_d.iloc[-1])
    obv_slope = (float(obv.iloc[-1]) - float(obv.iloc[-10])) / (abs(float(obv.iloc[-10])) + 1)
    mom5      = (c - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

    score, signals = 0, []

    if 40 <= r_val <= 65:
        score += 20; signals.append(f"RSI {r_val:.0f}")
    elif r_val < 35:
        score += 10; signals.append(f"RSI {r_val:.0f} oversold")

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

    target       = round(c + ATR_MULTIPLIER * atr_v, 2)
    stop         = round(c - STOP_MULTIPLIER * atr_v, 2)
    upside_pct   = (target - c) / c * 100
    downside_pct = (c - stop) / c * 100
    prob         = round(min(68.0, max(45.0, 45.0 + (score / 110) * 23.0)), 1)

    return {
        "ticker":             ticker.replace(".L", ""),
        "sector":             sector,
        "score":              score,
        "price":              c,
        "target":             target,
        "stop":               stop,
        "upside_pct":         upside_pct,
        "downside_pct":       downside_pct,
        "prob":               prob,
        "signals":            signals,
        "atr":                round(atr_v, 4),
        "reward_risk":        round(upside_pct / downside_pct, 2) if downside_pct > 0 else 0,
        "allocated_gbp":      "",
        "shares":             "",
        "outcome_price_p":    "",
        "outcome_hit":        "",
        "outcome_return_pct": "",
        "outcome_notes":      "",
        "went_up":            "",
        "profitable":         "",
        "run_date":           "",
    }


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _download_all_prices(tickers: dict, period: str = "400d") -> dict:
    """Batch download price history for all tickers.
    Tickers in the universe already include .L suffix (e.g. III.L) -- do not add it again.
    Price data is stored keyed by the ticker as-is.
    """
    price_data  = {}
    ticker_list = list(tickers.keys())
    batch_size  = 50

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
                "upside_pct":         round(r["upside_pct"], 2) if r.get("upside_pct") else "",
                "downside_pct":       round(r["downside_pct"], 2) if r.get("downside_pct") else "",
                "prob":               r.get("prob", ""),
                "reward_risk":        r.get("reward_risk", ""),
                "atr":                r.get("atr", ""),
                "allocated_gbp":      "",
                "allocation_pct":     round(r["allocation_pct"], 2) if r.get("allocation_pct") else "",
                "shares":             "",
                "signals":            " | ".join(r["signals"]) if r.get("signals") else "",
                "outcome_price_p":    r.get("outcome_price_p", ""),
                "outcome_hit":        r.get("outcome_hit", ""),
                "outcome_return_pct": r.get("outcome_return_pct", ""),
                "outcome_notes":      r.get("outcome_notes", ""),
                "went_up":            r.get("went_up", ""),
                "profitable":         r.get("profitable", ""),
            })


def _print_backtest_summary(results: list):
    """Print a summary of backtest results."""
    from rich.panel import Panel
    from rich import box

    resolved = [r for r in results if r.get("outcome_hit") in ("YES", "NO")]
    if not resolved:
        console.print("[yellow]No resolved picks to summarise.[/yellow]")
        return

    hits       = sum(1 for r in resolved if r["outcome_hit"] == "YES")
    hit_rate   = hits / len(resolved) * 100
    returns    = [float(r["outcome_return_pct"]) for r in resolved if r.get("outcome_return_pct") != ""]
    avg_ret    = sum(returns) / len(returns) if returns else 0
    went_up    = sum(1 for r in resolved if str(r.get("went_up", "")) == "1")
    profitable = sum(1 for r in resolved if str(r.get("profitable", "")) == "1")
    dir_acc    = went_up / len(resolved) * 100
    prof_rate  = profitable / len(resolved) * 100
    stops      = sum(1 for r in resolved if "Stop" in str(r.get("outcome_notes", "")))

    hc  = "bright_green" if hit_rate >= 50 else "yellow"
    rc  = "bright_green" if avg_ret >= 0 else "red"
    dc  = "bright_green" if dir_acc >= 55 else "yellow"
    pc  = "bright_green" if prof_rate >= 52 else "yellow"

    console.print(Panel(
        f"[bold]Backtest Complete[/bold]\n\n"
        f"  Total picks:          {len(resolved)}\n"
        f"  Avg return:           [{rc}]{avg_ret:+.3f}%[/{rc}] per pick\n"
        f"  Target hit rate:      [{hc}]{hit_rate:.1f}%[/{hc}]  ({hits}/{len(resolved)})\n"
        f"  Directional accuracy: [{dc}]{dir_acc:.1f}%[/{dc}]  (Mon close > Tue open)\n"
        f"  Profitable trades:    [{pc}]{prof_rate:.1f}%[/{pc}]  (exit price > entry)\n"
        f"  Stop-outs:            {stops} ({stops/len(resolved)*100:.1f}%)\n\n"
        f"[dim]Results saved to {BACKTEST_TECHNICAL_CSV}.\n"
        f"The calibration engine will incorporate these on the next run.\n"
        f"Backtest data is phased out automatically once you have\n"
        f"30 or more resolved live picks.[/dim]",
        title="Backtest Summary",
        box=box.ROUNDED,
    ))
