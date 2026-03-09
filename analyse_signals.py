"""
analyse_signals.py
------------------
Test 4: Individual signal contribution analysis.

For each of the 7 technical signals, answers:
  1. How often does each signal fire across all picks?
  2. What is the avg return of picks where that signal fired vs did not?
  3. Which signals are most predictive of positive returns?
  4. Which signals dominate high-scoring (90+) picks?
  5. Is the over-extension problem driven by specific signals?
  6. Are certain signal combinations particularly strong or weak?

Signals and their current point values (from screener.py):
  RSI 40-65        +20   (healthy momentum range)
  RSI < 35         +10   (oversold bounce)
  MACD crossover   +25   (bullish crossover — highest weight)
  MACD rising      +15   (rising but not yet crossed)
  BB %B 0.2-0.5    +15   (mid-band, healthy)
  BB near lower    +8    (near lower band)
  EMA full align   +20   (EMA20>EMA50>SMA200 full bull)
  EMA partial      +10   (EMA20>EMA50 only)
  Stochastic       +10
  OBV rising       +10
  Momentum 0.5-4%  +10   (gentle positive momentum)
  Momentum -1-0%   +5    (slight dip — mean reversion)

Run from LSE_Stock_Analyser folder:
    python analyse_signals.py
"""

import sys
import os
import csv
import statistics
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lse_analyser.config import BACKTEST_TECHNICAL_CSV
from rich.table import Table
from rich.panel import Panel
from rich import box
from lse_analyser.utils import console


# ── Signal definitions ────────────────────────────────────────────────────────

# Map keyword → (display name, point value, category)
SIGNAL_MAP = {
    "RSI":              None,  # handled specially — two variants
    "MACD bullish":     ("MACD crossover",  25, "MACD"),
    "MACD rising":      ("MACD rising",     15, "MACD"),
    "BB %B near lower": ("BB near lower",    8, "Bollinger"),
    "BB %B":            ("BB mid-band",     15, "Bollinger"),
    "EMA20>EMA50>SMA200": ("EMA full align", 20, "EMA"),
    "EMA20>EMA50":      ("EMA partial",     10, "EMA"),
    "Stoch":            ("Stochastic",      10, "Stochastic"),
    "OBV":              ("OBV rising",      10, "OBV"),
    "Mom +":            ("Momentum +",      10, "Momentum"),
    "Mom -":            ("Momentum -",       5, "Momentum"),
}

SIGNAL_DISPLAY = {
    "rsi_healthy":    ("RSI healthy (40-65)", 20, "RSI"),
    "rsi_oversold":   ("RSI oversold (<35)",  10, "RSI"),
    "macd_cross":     ("MACD crossover",      25, "MACD"),
    "macd_rising":    ("MACD rising",         15, "MACD"),
    "bb_mid":         ("BB mid-band",         15, "Bollinger"),
    "bb_lower":       ("BB near lower",        8, "Bollinger"),
    "ema_full":       ("EMA full align",      20, "EMA"),
    "ema_partial":    ("EMA partial",         10, "EMA"),
    "stoch":          ("Stochastic",          10, "Stochastic"),
    "obv":            ("OBV rising",          10, "OBV"),
    "mom_pos":        ("Momentum +",          10, "Momentum"),
    "mom_neg":        ("Momentum -",           5, "Momentum"),
}


def parse_signals(sig_str: str) -> dict:
    """
    Parse the signals string from CSV into a dict of signal_key -> bool.
    Handles both list-format ['signal1', 'signal2'] and comma-separated.
    """
    if not sig_str:
        return {k: False for k in SIGNAL_DISPLAY}

    # Clean up list-format strings
    sig_str = sig_str.strip("[]").replace("'", "").replace('"', "")
    parts   = [s.strip() for s in sig_str.split(",")]

    fired = {k: False for k in SIGNAL_DISPLAY}
    for p in parts:
        if "RSI" in p and "oversold" in p:
            fired["rsi_oversold"] = True
        elif "RSI" in p:
            fired["rsi_healthy"] = True
        if "MACD bullish" in p:
            fired["macd_cross"] = True
        elif "MACD rising" in p:
            fired["macd_rising"] = True
        if "BB %B" in p and "near lower" in p:
            fired["bb_lower"] = True
        elif "BB %B" in p:
            fired["bb_mid"] = True
        if "EMA20>EMA50>SMA200" in p:
            fired["ema_full"] = True
        elif "EMA20>EMA50" in p:
            fired["ema_partial"] = True
        if "Stoch" in p:
            fired["stoch"] = True
        if "OBV" in p:
            fired["obv"] = True
        if "Mom +" in p:
            fired["mom_pos"] = True
        if "Mom -" in p:
            fired["mom_neg"] = True

    return fired


# ── Data loading ──────────────────────────────────────────────────────────────

def load_picks():
    if not os.path.isfile(BACKTEST_TECHNICAL_CSV):
        console.print(f"[red]Backtest CSV not found: {BACKTEST_TECHNICAL_CSV}[/red]")
        sys.exit(1)

    picks = []
    with open(BACKTEST_TECHNICAL_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ret  = row.get("outcome_return_pct", "").strip()
            sc   = row.get("score", "").strip()
            sig  = row.get("signals", "").strip()
            wu   = row.get("went_up", "").strip()
            prof = row.get("profitable", "").strip()
            note = row.get("outcome_notes", "").strip()
            if not ret or not sc:
                continue
            try:
                picks.append({
                    "score":      int(float(sc)),
                    "return":     float(ret),
                    "went_up":    wu == "1",
                    "profitable": prof == "1",
                    "stop_out":   "Stop" in note,
                    "signals":    parse_signals(sig),
                    "raw_signals": sig,
                })
            except ValueError:
                continue
    return picks


def stats_for(picks):
    if not picks:
        return None
    returns = [p["return"] for p in picks]
    return {
        "n":          len(picks),
        "avg_ret":    round(sum(returns) / len(returns), 3),
        "profitable": round(sum(1 for p in picks if p["profitable"]) / len(picks) * 100, 1),
        "dir_acc":    round(sum(1 for p in picks if p["went_up"]) / len(picks) * 100, 1),
        "stop_pct":   round(sum(1 for p in picks if p["stop_out"]) / len(picks) * 100, 1),
        "std":        round(statistics.stdev(returns), 3) if len(returns) > 1 else 0,
    }


def colour_ret(val):
    if val >= 1.0:   return "bright_green"
    if val >= 0.3:   return "green"
    if val >= 0.0:   return "yellow"
    return "red"


# ── Section 1: Per-signal fire rate and return ────────────────────────────────

def section1_per_signal(picks):
    console.rule("[bold]1. Per-Signal Fire Rate and Avg Return[/bold]")
    console.print(
        "[dim]For each signal: how often does it fire, and what do those picks return?\n"
        "Sorted by avg return of picks where signal fired.[/dim]\n"
    )

    rows = []
    for sig_key, (display, points, category) in SIGNAL_DISPLAY.items():
        fired     = [p for p in picks if p["signals"][sig_key]]
        not_fired = [p for p in picks if not p["signals"][sig_key]]
        if len(fired) < 3:
            continue
        sf = stats_for(fired)
        sn = stats_for(not_fired)
        fire_rate = round(len(fired) / len(picks) * 100, 1)
        diff      = round(sf["avg_ret"] - sn["avg_ret"], 3) if sn else 0
        rows.append((sig_key, display, points, category, fired, sf, sn, fire_rate, diff))

    rows.sort(key=lambda x: x[5]["avg_ret"], reverse=True)

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Signal",          width=24)
    table.add_column("Pts",  justify="right", width=5)
    table.add_column("Fire rate",       justify="right", width=10)
    table.add_column("n fired",         justify="right", width=8)
    table.add_column("Avg ret (fired)", justify="right", width=16)
    table.add_column("Avg ret (not)",   justify="right", width=16)
    table.add_column("Difference",      justify="right", width=12)
    table.add_column("Profitable",      justify="right", width=11)

    for sig_key, display, points, category, fired, sf, sn, fire_rate, diff in rows:
        rc   = colour_ret(sf["avg_ret"])
        dc   = "bright_green" if diff > 0.1 else "red" if diff < -0.1 else "dim"
        table.add_row(
            display,
            str(points),
            f"{fire_rate:.0f}%",
            str(sf["n"]),
            f"[{rc}]{sf['avg_ret']:+.3f}%[/{rc}]",
            f"{sn['avg_ret']:+.3f}%" if sn else "N/A",
            f"[{dc}]{diff:+.3f}%[/{dc}]",
            f"{sf['profitable']:.1f}%",
        )

    console.print(table)
    return rows


# ── Section 2: Signal presence in high vs low scoring picks ───────────────────

def section2_high_vs_low(picks):
    console.rule("[bold]2. Signal Breakdown — High Scoring (90+) vs Sweet Spot (75–89)[/bold]")
    console.print(
        "[dim]Which signals dominate over-extended (90+) picks compared to the sweet spot?\n"
        "A signal that fires much more in 90+ picks may be driving the over-extension.[/dim]\n"
    )

    high  = [p for p in picks if p["score"] >= 90]
    sweet = [p for p in picks if 75 <= p["score"] < 90]
    low   = [p for p in picks if p["score"] < 75]

    console.print(
        f"[dim]High (90+): {len(high)} picks  avg ret {stats_for(high)['avg_ret']:+.3f}%[/dim]"
    )
    console.print(
        f"[dim]Sweet (75-89): {len(sweet)} picks  avg ret {stats_for(sweet)['avg_ret']:+.3f}%[/dim]"
    )
    if low:
        console.print(
            f"[dim]Low (<75): {len(low)} picks  avg ret {stats_for(low)['avg_ret']:+.3f}%[/dim]"
        )
    console.print()

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Signal",            width=24)
    table.add_column("Pts", justify="right", width=5)
    table.add_column("High 90+ %",        justify="right", width=12)
    table.add_column("Sweet 75-89 %",     justify="right", width=14)
    table.add_column("Difference",        justify="right", width=12)
    table.add_column("Signal drives 90+?", justify="center", width=18)

    signal_diffs = []
    for sig_key, (display, points, category) in SIGNAL_DISPLAY.items():
        h_rate = sum(1 for p in high  if p["signals"][sig_key]) / len(high)  * 100 if high  else 0
        s_rate = sum(1 for p in sweet if p["signals"][sig_key]) / len(sweet) * 100 if sweet else 0
        diff   = round(h_rate - s_rate, 1)
        signal_diffs.append((sig_key, display, points, h_rate, s_rate, diff))

    signal_diffs.sort(key=lambda x: x[5], reverse=True)

    for sig_key, display, points, h_rate, s_rate, diff in signal_diffs:
        dc      = "bright_green" if diff > 15 else "yellow" if diff > 5 else "dim"
        driver  = "[bold red]Yes — much more common[/bold red]" if diff > 20 else \
                  "[yellow]Somewhat[/yellow]" if diff > 10 else \
                  "[dim]No[/dim]"
        table.add_row(
            display,
            str(points),
            f"{h_rate:.0f}%",
            f"{s_rate:.0f}%",
            f"[{dc}]{diff:+.1f}pp[/{dc}]",
            driver,
        )

    console.print(table)
    return signal_diffs


# ── Section 3: Return by signal count ────────────────────────────────────────

def section3_signal_count(picks):
    console.rule("[bold]3. Return by Number of Signals Fired[/bold]")
    console.print(
        "[dim]Does firing more signals simultaneously improve or hurt returns?\n"
        "High signal count = high score — this shows how score maps to signal count.[/dim]\n"
    )

    by_count = defaultdict(list)
    for p in picks:
        n_signals = sum(p["signals"].values())
        by_count[n_signals].append(p)

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Signals fired", justify="right", width=14)
    table.add_column("Picks",         justify="right", width=8)
    table.add_column("Avg score",     justify="right", width=10)
    table.add_column("Avg return",    justify="right", width=12)
    table.add_column("Profitable",    justify="right", width=11)
    table.add_column("Dir acc",       justify="right", width=10)
    table.add_column("Stop-out",      justify="right", width=10)

    for n in sorted(by_count.keys()):
        bucket = by_count[n]
        if len(bucket) < 2:
            continue
        s        = stats_for(bucket)
        avg_sc   = round(sum(p["score"] for p in bucket) / len(bucket), 1)
        rc       = colour_ret(s["avg_ret"])
        table.add_row(
            str(n),
            str(s["n"]),
            str(avg_sc),
            f"[{rc}]{s['avg_ret']:+.3f}%[/{rc}]",
            f"{s['profitable']:.1f}%",
            f"{s['dir_acc']:.1f}%",
            f"{s['stop_pct']:.1f}%",
        )

    console.print(table)


# ── Section 4: Best and worst signal combinations ────────────────────────────

def section4_combinations(picks):
    console.rule("[bold]4. Signal Pair Analysis — Best and Worst Combinations[/bold]")
    console.print(
        "[dim]Which pairs of signals together produce the best/worst outcomes?\n"
        "Only pairs with >= 8 picks shown. Sorted by avg return.[/dim]\n"
    )

    sig_keys = list(SIGNAL_DISPLAY.keys())
    pair_stats = []

    for s1, s2 in combinations(sig_keys, 2):
        both = [p for p in picks if p["signals"][s1] and p["signals"][s2]]
        if len(both) < 8:
            continue
        s = stats_for(both)
        n1 = SIGNAL_DISPLAY[s1][0]
        n2 = SIGNAL_DISPLAY[s2][0]
        pair_stats.append((n1, n2, s))

    pair_stats.sort(key=lambda x: x[2]["avg_ret"], reverse=True)

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Signal 1",      width=22)
    table.add_column("Signal 2",      width=22)
    table.add_column("Picks",         justify="right", width=7)
    table.add_column("Avg return",    justify="right", width=12)
    table.add_column("Profitable",    justify="right", width=11)
    table.add_column("Dir acc",       justify="right", width=10)

    # Top 8 and bottom 8
    top    = pair_stats[:8]
    bottom = pair_stats[-8:]
    shown  = set()

    console.print("[bold dim]── Top 8 pairs ──[/bold dim]")
    for n1, n2, s in top:
        rc = colour_ret(s["avg_ret"])
        table.add_row(n1, n2, str(s["n"]),
                      f"[{rc}]{s['avg_ret']:+.3f}%[/{rc}]",
                      f"{s['profitable']:.1f}%", f"{s['dir_acc']:.1f}%")
        shown.add((n1, n2))

    table.add_row("─" * 20, "─" * 20, "─" * 5, "─" * 10, "─" * 9, "─" * 8)
    console.print("[bold dim]── Bottom 8 pairs ──[/bold dim]")

    for n1, n2, s in bottom:
        if (n1, n2) in shown:
            continue
        rc = colour_ret(s["avg_ret"])
        table.add_row(n1, n2, str(s["n"]),
                      f"[{rc}]{s['avg_ret']:+.3f}%[/{rc}]",
                      f"{s['profitable']:.1f}%", f"{s['dir_acc']:.1f}%")

    console.print(table)


# ── Section 5: High-scorer signal autopsy ─────────────────────────────────────

def section5_autopsy(picks):
    console.rule("[bold]5. High-Scorer Autopsy — What Are the 90+ Picks Actually Doing?[/bold]")
    console.print(
        "[dim]Detailed profile of over-extended picks (score 90+).\n"
        "Shows the most common signal combinations found in these picks.[/dim]\n"
    )

    high = [p for p in picks if p["score"] >= 90]
    if not high:
        console.print("[yellow]No picks with score >= 90 in dataset.[/yellow]")
        return

    s = stats_for(high)
    console.print(Panel(
        f"  Count:           {s['n']} picks ({s['n']/len(picks)*100:.0f}% of all picks)\n"
        f"  Avg return:      [red]{s['avg_ret']:+.3f}%[/red]\n"
        f"  Profitable:      {s['profitable']:.1f}%\n"
        f"  Directional acc: {s['dir_acc']:.1f}%\n"
        f"  Stop-out rate:   {s['stop_pct']:.1f}%\n"
        f"  Std dev:         {s['std']:.3f}%",
        title="[bold]Profile: Score 90+ Picks[/bold]",
        box=box.ROUNDED,
    ))

    # Most common signal patterns in 90+ picks
    pattern_counts = defaultdict(lambda: {"count": 0, "returns": []})
    for p in high:
        fired = tuple(sorted(k for k, v in p["signals"].items() if v))
        pattern_counts[fired]["count"] += 1
        pattern_counts[fired]["returns"].append(p["return"])

    sorted_patterns = sorted(
        pattern_counts.items(), key=lambda x: x[1]["count"], reverse=True
    )[:10]

    console.print("\n[bold dim]Most common signal combinations in 90+ picks:[/bold dim]\n")
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Signals fired",  width=60)
    table.add_column("Count",          justify="right", width=7)
    table.add_column("Avg return",     justify="right", width=12)

    for pattern, data in sorted_patterns:
        names    = [SIGNAL_DISPLAY[k][0] for k in pattern if k in SIGNAL_DISPLAY]
        avg_ret  = sum(data["returns"]) / len(data["returns"])
        rc       = colour_ret(avg_ret)
        table.add_row(
            ", ".join(names),
            str(data["count"]),
            f"[{rc}]{avg_ret:+.3f}%[/{rc}]",
        )

    console.print(table)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold cyan]Signal Contribution Analysis (Test 4)[/bold cyan]")

    picks = load_picks()
    s_all = stats_for(picks)
    console.print(
        f"[dim]Loaded {len(picks)} picks  |  "
        f"Score range: {min(p['score'] for p in picks)}–{max(p['score'] for p in picks)}  |  "
        f"Baseline avg return: {s_all['avg_ret']:+.3f}%[/dim]\n"
    )

    sig_rows  = section1_per_signal(picks)
    console.print()
    sig_diffs = section2_high_vs_low(picks)
    console.print()
    section3_signal_count(picks)
    console.print()
    section4_combinations(picks)
    console.print()
    section5_autopsy(picks)

    # ── Summary ────────────────────────────────────────────────────────────────
    console.rule("[bold]Summary[/bold]")

    # Best signals by return when fired
    best_sigs  = sorted(sig_rows, key=lambda x: x[5]["avg_ret"], reverse=True)[:3]
    worst_sigs = sorted(sig_rows, key=lambda x: x[5]["avg_ret"])[:3]

    # Biggest drivers of over-extension
    top_drivers = sorted(sig_diffs, key=lambda x: x[5], reverse=True)[:3]

    console.print(Panel(
        "  [bold]Best performing signals (avg return when fired):[/bold]\n"
        + "".join(
            f"    {r[1]:30s} {r[5]['avg_ret']:+.3f}%\n"
            for r in best_sigs
        )
        + "\n  [bold]Worst performing signals (avg return when fired):[/bold]\n"
        + "".join(
            f"    {r[1]:30s} {r[5]['avg_ret']:+.3f}%\n"
            for r in worst_sigs
        )
        + "\n  [bold]Signals most responsible for 90+ over-extension:[/bold]\n"
        + "".join(
            f"    {r[1]:30s} +{r[5]:.1f}pp more common in 90+ picks\n"
            for r in top_drivers
        )
        + "\n  [dim]Use these findings to decide whether specific signal weights\n"
        f"  should be adjusted, or whether the Strategy B cap at 90 is\n"
        f"  the cleanest solution.[/dim]",
        title="[bold]Key Findings[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
