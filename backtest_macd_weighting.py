"""
backtest_macd_weighting.py
--------------------------
Compares four MACD crossover weighting strategies over the same 52-week
backtest window, following the signal analysis findings in Test 4.

Finding from Test 4:
  - MACD crossover fires in 94% of 90+ picks vs 29% of sweet spot picks
  - MACD crossover avg return when fired: -0.020% (worst of all signals)
  - MACD crossover gets 25 points — the highest weight of any signal
  - It is a lagging signal: by the time it fires alongside 5 other signals,
    the move has already happened

Strategies tested:
  A) Current        — MACD crossover = +25 always (baseline)
  B) Reduced        — MACD crossover = +8 always (weak confirmation only)
  C) Capped score   — MACD crossover = +25 but total score hard-capped at 88
                      (Strategy B from Test 3, reimplemented here for direct
                      comparison on same scoring basis)
  D) Context-aware  — MACD crossover reward scales inversely with signal count:
                        <= 2 other signals firing: +25 (early, valuable)
                           3 other signals firing: +15
                           4 other signals firing:  +5
                        >= 5 other signals firing: -15 (late, penalise)

All other signal weights unchanged. Sector diversification applied.

Run from LSE_Stock_Analyser folder:
    python backtest_macd_weighting.py
"""

import sys
import os
import csv
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lse_analyser.config import (
    TOP_N, BACKTEST_WEEKS_TECHNICAL, BACKTEST_CAPITAL,
    ATR_MULTIPLIER, STOP_MULTIPLIER, BACKTEST_END_DATE,
)
from lse_analyser.tickers import get_tickers
from lse_analyser.sizing import calculate_allocations
from lse_analyser.utils import console
from lse_analyser.backtest import (
    _download_all_prices,
    _simulate_trade,
)
from lse_analyser.screener import diversify as sector_diversify

from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator, SMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator

from rich.table import Table
from rich.panel import Panel
from rich import box


# ── Scoring functions ─────────────────────────────────────────────────────────

def _base_signals(hist):
    """
    Compute all indicator values and non-MACD signals.
    Returns (indicator_values, base_score, base_signals, other_signal_count).
    """
    from lse_analyser.config import MIN_AVG_VOLUME_GBP

    close, high, low, vol = hist["close"], hist["high"], hist["low"], hist["volume"]

    recent        = hist.tail(20)
    avg_value_gbp = (recent["close"] * recent["volume"] / 100).mean()
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

    c       = float(close.iloc[-1])
    r_val   = float(rsi.iloc[-1])
    mh      = float(macd_hist.iloc[-1])
    mh_p    = float(macd_hist.iloc[-2])
    bp      = float(bb_pct.iloc[-1])
    e20     = float(ema20.iloc[-1])
    e50     = float(ema50.iloc[-1])
    s200    = float(sma200.iloc[-1])
    atr_v   = float(atr.iloc[-1])
    sk      = float(stoch_k.iloc[-1])
    sd      = float(stoch_d.iloc[-1])
    obv_slope = (float(obv.iloc[-1]) - float(obv.iloc[-10])) / (abs(float(obv.iloc[-10])) + 1)
    mom5    = (c - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

    score, signals = 0, []

    # RSI
    if 40 <= r_val <= 65:
        score += 20; signals.append(f"RSI {r_val:.0f}")
    elif r_val < 35:
        score += 10; signals.append(f"RSI {r_val:.0f} oversold")

    # MACD — recorded but NOT added to score here; handled per-strategy
    macd_crossover = mh > 0 and mh_p < 0
    macd_rising    = mh > mh_p and mh > 0 and not macd_crossover

    if macd_rising:
        score += 15; signals.append("MACD rising")

    # Bollinger
    if 0.2 <= bp <= 0.5:
        score += 15; signals.append(f"BB %B {bp:.2f}")
    elif bp < 0.2:
        score += 8;  signals.append(f"BB %B {bp:.2f} near lower band")

    # EMA
    if e20 > e50 > s200:
        score += 20; signals.append("EMA20>EMA50>SMA200")
    elif e20 > e50:
        score += 10; signals.append("EMA20>EMA50")

    # Stochastic
    if sk > sd and 20 < sk < 80:
        score += 10; signals.append(f"Stoch {sk:.0f}/{sd:.0f}")

    # OBV
    if obv_slope > 0.01:
        score += 10; signals.append("OBV rising")

    # Momentum
    if 0.5 <= mom5 <= 4.0:
        score += 10; signals.append(f"Mom +{mom5:.1f}%")
    elif -1.0 <= mom5 < 0:
        score += 5;  signals.append(f"Mom {mom5:.1f}%")

    return {
        "c":              c,
        "atr_v":          atr_v,
        "base_score":     score,
        "base_signals":   signals,
        "macd_crossover": macd_crossover,
        "other_count":    len(signals),  # signals fired excluding MACD crossover
    }


def score_strategy_a(vals):
    """A: Current — MACD crossover = +25 always."""
    score   = vals["base_score"]
    signals = list(vals["base_signals"])
    if vals["macd_crossover"]:
        score += 25
        signals.append("MACD bullish crossover")
    return score, signals


def score_strategy_b(vals):
    """B: Reduced — MACD crossover = +8 always."""
    score   = vals["base_score"]
    signals = list(vals["base_signals"])
    if vals["macd_crossover"]:
        score += 8
        signals.append("MACD bullish crossover")
    return score, signals


def score_strategy_c(vals):
    """C: Capped — MACD crossover = +25 but total score capped at 88."""
    score   = vals["base_score"]
    signals = list(vals["base_signals"])
    if vals["macd_crossover"]:
        score += 25
        signals.append("MACD bullish crossover")
    score = min(score, 88)
    return score, signals


def score_strategy_d(vals):
    """
    D: Context-aware — MACD crossover reward scales inversely with
    number of other signals already firing.
      0–2 others: +25
      3 others:   +15
      4 others:   +5
      5+ others:  -15
    """
    score   = vals["base_score"]
    signals = list(vals["base_signals"])
    if vals["macd_crossover"]:
        n = vals["other_count"]
        if n <= 2:
            macd_pts = 25
        elif n == 3:
            macd_pts = 15
        elif n == 4:
            macd_pts = 5
        else:
            macd_pts = -15
        score += macd_pts
        signals.append(f"MACD bullish crossover ({macd_pts:+d}pts)")
    return score, signals


SCORING_FNS = {
    "A: Current":      score_strategy_a,
    "B: Reduced +8":   score_strategy_b,
    "C: Capped 88":    score_strategy_c,
    "D: Context-aware": score_strategy_d,
}


# ── Ticker scoring with a given strategy ─────────────────────────────────────

def score_ticker_with_strategy(ticker, sector, hist, scoring_fn):
    """Score a ticker using the given MACD weighting strategy."""
    vals = _base_signals(hist)
    if vals is None:
        return None

    score, signals = scoring_fn(vals)
    if score <= 0:
        return None

    c     = vals["c"]
    atr_v = vals["atr_v"]

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


# ── Core simulation ───────────────────────────────────────────────────────────

def run_strategy(tickers, price_data, name, scoring_fn, n_weeks):
    all_results = []
    end_monday  = datetime.strptime(BACKTEST_END_DATE, "%Y-%m-%d")

    for week_offset in range(n_weeks, 0, -1):
        sim_monday    = end_monday - timedelta(weeks=week_offset)
        entry_tuesday = sim_monday + timedelta(days=1)
        exit_monday   = sim_monday + timedelta(days=8)

        if exit_monday > end_monday:
            continue

        sim_monday_pd    = pd.Timestamp(sim_monday)
        entry_tuesday_pd = pd.Timestamp(entry_tuesday)
        exit_monday_pd   = pd.Timestamp(exit_monday)

        week_scores = []
        for ticker, sector in tickers.items():
            df = price_data.get(ticker)
            if df is None or len(df) < 30:
                continue
            try:
                hist = df[df.index <= sim_monday_pd].copy()
                if len(hist) < 30:
                    continue
                r = score_ticker_with_strategy(ticker, sector, hist, scoring_fn)
                if r:
                    week_scores.append(r)
            except Exception:
                continue

        if not week_scores:
            continue

        week_scores.sort(key=lambda x: x["score"], reverse=True)
        top = sector_diversify(week_scores, TOP_N)

        week_resolved = []
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
                    r["run_date"]   = sim_monday.strftime("%Y-%m-%d")
                    r["week_label"] = sim_monday.strftime("%Y-%m-%d")
                    week_resolved.append(r)
            except Exception:
                continue

        if week_resolved:
            calculate_allocations(week_resolved, BACKTEST_CAPITAL)
            all_results.extend(week_resolved)

    return all_results


# ── Statistics ────────────────────────────────────────────────────────────────

def compute_stats(picks):
    if not picks:
        return {}

    resolved   = [p for p in picks if p.get("outcome_return_pct") != ""]
    returns    = [float(p["outcome_return_pct"]) for p in resolved]
    went_up    = [p for p in resolved if str(p.get("went_up", "")) == "1"]
    profitable = [p for p in resolved if str(p.get("profitable", "")) == "1"]
    target_hits = [p for p in resolved if p.get("outcome_hit") == "YES"]
    stops       = [p for p in resolved if "Stop" in str(p.get("outcome_notes", ""))]

    weeks = defaultdict(list)
    for p in resolved:
        weeks[p["week_label"]].append(float(p["outcome_return_pct"]))
    weekly_avgs = [sum(v) / len(v) for v in weeks.values()]

    # Score distribution in bands of 5
    score_dist = defaultdict(int)
    for p in picks:
        band = (p["score"] // 5) * 5
        score_dist[f"{band}–{band+5}"] += 1

    # How many picks had MACD crossover
    macd_cross_count = sum(
        1 for p in picks
        if any("MACD bullish" in str(s) for s in p.get("signals", []))
    )

    return {
        "n_picks":         len(resolved),
        "n_weeks":         len(weeks),
        "avg_return":      round(sum(returns) / len(returns), 3) if returns else 0,
        "std_return":      round(statistics.stdev(returns), 3) if len(returns) > 1 else 0,
        "profitable_pct":  round(len(profitable) / len(resolved) * 100, 1) if resolved else 0,
        "dir_acc":         round(len(went_up) / len(resolved) * 100, 1) if resolved else 0,
        "target_hit_pct":  round(len(target_hits) / len(resolved) * 100, 1) if resolved else 0,
        "stop_pct":        round(len(stops) / len(resolved) * 100, 1) if resolved else 0,
        "best_week":       round(max(weekly_avgs), 3) if weekly_avgs else 0,
        "worst_week":      round(min(weekly_avgs), 3) if weekly_avgs else 0,
        "std_weekly":      round(statistics.stdev(weekly_avgs), 3) if len(weekly_avgs) > 1 else 0,
        "avg_score":       round(sum(p["score"] for p in picks) / len(picks), 1),
        "macd_cross_pct":  round(macd_cross_count / len(picks) * 100, 1) if picks else 0,
        "score_dist":      dict(sorted(score_dist.items())),
    }


# ── Display ───────────────────────────────────────────────────────────────────

def colour_best(vals, higher_is_better=True):
    best = max(vals) if higher_is_better else min(vals)
    return [
        f"[bold bright_green]{v}[/bold bright_green]" if v == best else f"[dim]{v}[/dim]"
        for v in vals
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold cyan]MACD Weighting Backtest[/bold cyan]")
    console.print(
        "\n[dim]Comparing four MACD crossover weighting strategies.\n\n"
        "  A) Current:       MACD crossover = +25 always\n"
        "  B) Reduced:       MACD crossover = +8 always\n"
        "  C) Capped 88:     MACD crossover = +25 but total score capped at 88\n"
        "  D) Context-aware: MACD crossover reward depends on how many other\n"
        "                    signals are already firing (flips to -15 at 5+)\n\n"
        "  All other signal weights unchanged.\n"
        "  Sector diversification (1 per sector) applied in all strategies.\n"
        "  Shared price data download — ~10-15 minutes.[/dim]\n"
    )

    confirm = input("  Press Enter to start, or type 'skip' to cancel: ").strip().lower()
    if confirm == "skip":
        console.print("[yellow]Cancelled.[/yellow]\n")
        return

    tickers = get_tickers()
    console.print(f"[dim]Ticker universe: {len(tickers)} stocks[/dim]\n")

    console.print("[dim]Downloading price data...[/dim]")
    price_data = _download_all_prices(tickers)
    console.print(f"[dim]Price data ready for {len(price_data)} tickers.[/dim]\n")

    all_stats = {}

    for name, scoring_fn in SCORING_FNS.items():
        console.print(f"[bold]Running strategy {name}...[/bold]")
        picks = run_strategy(
            tickers, price_data, name, scoring_fn, BACKTEST_WEEKS_TECHNICAL
        )
        stats = compute_stats(picks)
        all_stats[name] = stats
        console.print(
            f"  [green]Done.[/green] {stats['n_picks']} picks, "
            f"avg score {stats['avg_score']:.1f}, "
            f"MACD crossover in {stats['macd_cross_pct']:.0f}% of picks, "
            f"avg return [cyan]{stats['avg_return']:+.3f}%[/cyan]\n"
        )

    # ── Individual panels ──────────────────────────────────────────────────────
    for name, stats in all_stats.items():
        rc  = "bright_green" if stats["avg_return"] >= 0 else "red"
        wc  = "bright_green" if stats["worst_week"] >= -1.5 else "red"
        dist_str = "  ".join(
            f"{band}: {count}" for band, count in stats["score_dist"].items()
        )
        console.print(Panel(
            f"  Picks:              {stats['n_picks']} across {stats['n_weeks']} weeks\n"
            f"  Avg score:          {stats['avg_score']:.1f}\n"
            f"  MACD crossover in:  {stats['macd_cross_pct']:.0f}% of picks\n"
            f"  Avg return:         [{rc}]{stats['avg_return']:+.3f}%[/{rc}] per pick\n"
            f"  Std dev (picks):    {stats['std_return']:.3f}%\n"
            f"  Profitable:         {stats['profitable_pct']:.1f}%\n"
            f"  Directional acc:    {stats['dir_acc']:.1f}%\n"
            f"  Target hit rate:    {stats['target_hit_pct']:.1f}%\n"
            f"  Stop-out rate:      {stats['stop_pct']:.1f}%\n"
            f"  Best week:          [bright_green]{stats['best_week']:+.3f}%[/bright_green]\n"
            f"  Worst week:         [{wc}]{stats['worst_week']:+.3f}%[/{wc}]\n"
            f"  Std dev (weekly):   {stats['std_weekly']:.3f}%\n\n"
            f"  Score distribution:\n  {dist_str}",
            title=f"[bold]Strategy {name}[/bold]",
            box=box.ROUNDED,
        ))

    # ── Comparison table ───────────────────────────────────────────────────────
    console.rule("[bold]Final Comparison Table[/bold]")

    s     = list(all_stats.values())
    names = list(all_stats.keys())

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    table.add_column("Metric",           style="bold", width=24)
    for name in names:
        table.add_column(name, justify="right", width=18)
    table.add_column("Better is...", justify="left", width=12, style="dim")

    def row(label, key, fmt, higher_is_better=True, suffix=""):
        raw_vals = [s[i][key] for i in range(len(s))]
        coloured = colour_best(raw_vals, higher_is_better)
        coloured = [c + suffix for c in coloured]
        table.add_row(label, *coloured, "Higher" if higher_is_better else "Lower")

    row("Avg score",           "avg_score",      "{:.1f}",  higher_is_better=False)
    row("MACD crossover %",    "macd_cross_pct", "{:.0f}",  higher_is_better=False, suffix="%")
    row("Avg return / pick",   "avg_return",     "{:+.3f}", suffix="%")
    row("Profitable trades",   "profitable_pct", "{:.1f}",  suffix="%")
    row("Directional accuracy","dir_acc",        "{:.1f}",  suffix="%")
    row("Target hit rate",     "target_hit_pct", "{:.1f}",  suffix="%")
    row("Stop-out rate",       "stop_pct",       "{:.1f}",  higher_is_better=False, suffix="%")
    row("Best week",           "best_week",       "{:+.3f}", suffix="%")
    row("Worst week",          "worst_week",      "{:+.3f}", suffix="%")
    row("Std dev (picks)",     "std_return",      "{:.3f}",  higher_is_better=False, suffix="%")
    row("Std dev (weekly)",    "std_weekly",      "{:.3f}",  higher_is_better=False, suffix="%")

    console.print(table)

    # ── Interpretation ─────────────────────────────────────────────────────────
    best_return      = max(all_stats, key=lambda k: all_stats[k]["avg_return"])
    best_consistency = min(all_stats, key=lambda k: all_stats[k]["std_weekly"])
    safest           = max(all_stats, key=lambda k: all_stats[k]["worst_week"])

    a_ret = all_stats["A: Current"]["avg_return"]
    best_ret = all_stats[best_return]["avg_return"]
    improvement = round(best_ret - a_ret, 3)

    console.print(Panel(
        f"  Best avg return:      [bold cyan]{best_return}[/bold cyan]  "
        f"({best_ret:+.3f}%  vs baseline A: {a_ret:+.3f}%  improvement: {improvement:+.3f}pp)\n"
        f"  Most consistent:      [bold cyan]{best_consistency}[/bold cyan]  "
        f"(lowest weekly std dev)\n"
        f"  Best worst-week:      [bold cyan]{safest}[/bold cyan]  "
        f"(least downside in bad weeks)\n\n"
        f"  [dim]Key question: does reducing/contextualising MACD crossover\n"
        f"  improve returns while also improving or maintaining worst-week?\n"
        f"  If so, it is a better solution than the blunt cap in Test 3 Strategy B,\n"
        f"  which improved return but worsened worst-week.[/dim]",
        title="[bold]Interpretation[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
