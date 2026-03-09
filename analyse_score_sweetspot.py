"""
analyse_score_sweetspot.py
--------------------------
Tests 1 & 2: Find the optimal score range and check whether it is
stable across time.

Test 1 — Fine-grained return vs score:
  - Breaks scores into narrow bands (width 5) for a cleaner picture
    than the decile analysis
  - Fits a simple curve to identify the peak return score
  - Shows where the sweet spot sits and how sharp the drop-off is

Test 2 — Temporal stability:
  - Splits the 52-week backtest into two halves
  - Checks whether the sweet spot is consistent across both halves
  - If the sweet spot shifts significantly, a fixed target is unreliable

Run from LSE_Stock_Analyser folder:
    python analyse_score_sweetspot.py
"""

import sys
import os
import csv
import statistics
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lse_analyser.config import BACKTEST_TECHNICAL_CSV
from rich.table import Table
from rich.panel import Panel
from rich import box
from lse_analyser.utils import console


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
            wu   = row.get("went_up", "").strip()
            prof = row.get("profitable", "").strip()
            hit  = row.get("outcome_hit", "").strip()
            note = row.get("outcome_notes", "").strip()
            sig  = row.get("signals", "").strip()
            date = row.get("run_date", "").strip()[:10]
            if not ret or not sc or not date:
                continue
            try:
                picks.append({
                    "score":      int(float(sc)),
                    "return":     float(ret),
                    "went_up":    wu == "1",
                    "profitable": prof == "1",
                    "target_hit": hit == "YES",
                    "stop_out":   "Stop" in note,
                    "signals":    sig,
                    "week":       date,
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
        "median_ret": round(statistics.median(returns), 3),
    }


def colour_ret(val):
    if val >= 1.0:
        return "bright_green"
    elif val >= 0.5:
        return "green"
    elif val >= 0.0:
        return "yellow"
    else:
        return "red"


def bar(val, scale=5.0, width=20):
    """Simple ASCII bar chart scaled to width characters."""
    filled = int(min(abs(val) / scale * width, width))
    if val >= 0:
        return "[green]" + "█" * filled + "[/green]" + "░" * (width - filled)
    else:
        return "[red]" + "█" * filled + "[/red]" + "░" * (width - filled)


# ── Test 1: Fine-grained score bands ─────────────────────────────────────────

def test1_fine_grained(picks):
    console.rule("[bold]Test 1 — Fine-Grained Return vs Score (bands of 5)[/bold]")
    console.print(
        "[dim]Each row = picks whose score falls within a 5-point band.\n"
        "Minimum 3 picks required to show a band. Look for where avg return peaks.[/dim]\n"
    )

    score_min = min(p["score"] for p in picks)
    score_max = max(p["score"] for p in picks)

    # Align to nearest 5
    lo_start = (score_min // 5) * 5
    hi_end   = ((score_max // 5) + 1) * 5

    bands = []
    for lo in range(lo_start, hi_end, 5):
        hi     = lo + 5
        bucket = [p for p in picks if lo <= p["score"] < hi]
        if len(bucket) < 3:
            continue
        s = stats_for(bucket)
        bands.append((lo, hi, s))

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Score band",   width=12)
    table.add_column("Picks",        justify="right", width=7)
    table.add_column("Avg return",   justify="right", width=12)
    table.add_column("Median ret",   justify="right", width=12)
    table.add_column("Profitable",   justify="right", width=11)
    table.add_column("Dir acc",      justify="right", width=10)
    table.add_column("Stop-out",     justify="right", width=10)
    table.add_column("Return bar",   width=24)

    peak_ret   = max(s["avg_ret"] for _, _, s in bands)
    peak_band  = [(lo, hi) for lo, hi, s in bands if s["avg_ret"] == peak_ret][0]

    for lo, hi, s in bands:
        rc      = colour_ret(s["avg_ret"])
        is_peak = (lo, hi) == peak_band
        label   = f"[bold]{lo}–{hi}[/bold]" if is_peak else f"{lo}–{hi}"
        marker  = " ◄ peak" if is_peak else ""
        table.add_row(
            label + marker,
            str(s["n"]),
            f"[{rc}]{s['avg_ret']:+.3f}%[/{rc}]",
            f"{s['median_ret']:+.3f}%",
            f"{s['profitable']:.1f}%",
            f"{s['dir_acc']:.1f}%",
            f"{s['stop_pct']:.1f}%",
            bar(s["avg_ret"]),
        )

    console.print(table)

    # Identify sweet spot: contiguous bands where avg_ret >= 0.5 * peak_ret
    sweet_bands = [(lo, hi, s) for lo, hi, s in bands if s["avg_ret"] >= 0.5 * peak_ret]
    if sweet_bands:
        sweet_lo = sweet_bands[0][0]
        sweet_hi = sweet_bands[-1][1]
        sweet_picks = [p for p in picks if sweet_lo <= p["score"] < sweet_hi]
        s_sweet = stats_for(sweet_picks)
        s_all   = stats_for(picks)

        console.print(Panel(
            f"  Peak return band:    [bold cyan]{peak_band[0]}–{peak_band[1]}[/bold cyan]  "
            f"({peak_ret:+.3f}%)\n"
            f"  Sweet spot range:    [bold cyan]{sweet_lo}–{sweet_hi}[/bold cyan]  "
            f"(bands returning >= 50% of peak)\n\n"
            f"  Picks in sweet spot: {s_sweet['n']} of {len(picks)} "
            f"({s_sweet['n']/len(picks)*100:.0f}%)\n"
            f"  Sweet spot avg ret:  [bright_green]{s_sweet['avg_ret']:+.3f}%[/bright_green]  "
            f"vs baseline [dim]{s_all['avg_ret']:+.3f}%[/dim]\n"
            f"  Outside sweet spot:  "
            f"{stats_for([p for p in picks if not (sweet_lo <= p['score'] < sweet_hi)])['avg_ret']:+.3f}%",
            title="[bold]Sweet Spot Summary[/bold]",
            box=box.ROUNDED,
        ))

    return bands, sweet_lo if sweet_bands else None, sweet_hi if sweet_bands else None


# ── Test 2: Temporal stability ────────────────────────────────────────────────

def test2_temporal_stability(picks, bands):
    console.rule("[bold]Test 2 — Temporal Stability of Sweet Spot[/bold]")
    console.print(
        "[dim]Splits the backtest into two equal halves by date.\n"
        "If the sweet spot is consistent, a fixed target is reliable.\n"
        "If it shifts significantly, a dynamic target would be needed.[/dim]\n"
    )

    weeks_sorted = sorted(set(p["week"] for p in picks))
    mid_idx      = len(weeks_sorted) // 2
    mid_date     = weeks_sorted[mid_idx]

    first_half  = [p for p in picks if p["week"] < mid_date]
    second_half = [p for p in picks if p["week"] >= mid_date]

    console.print(
        f"[dim]First half:  {weeks_sorted[0]} to {weeks_sorted[mid_idx-1]} "
        f"({len(first_half)} picks)[/dim]"
    )
    console.print(
        f"[dim]Second half: {mid_date} to {weeks_sorted[-1]} "
        f"({len(second_half)} picks)[/dim]\n"
    )

    def half_table(half_picks, label):
        score_min = min(p["score"] for p in half_picks)
        score_max = max(p["score"] for p in half_picks)
        lo_start  = (score_min // 5) * 5
        hi_end    = ((score_max // 5) + 1) * 5

        half_bands = []
        for lo in range(lo_start, hi_end, 5):
            hi     = lo + 5
            bucket = [p for p in half_picks if lo <= p["score"] < hi]
            if len(bucket) < 2:
                continue
            s = stats_for(bucket)
            half_bands.append((lo, hi, s))

        if not half_bands:
            return None, None

        peak_ret  = max(s["avg_ret"] for _, _, s in half_bands)
        peak_band = [(lo, hi) for lo, hi, s in half_bands if s["avg_ret"] == peak_ret][0]

        t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan",
                  title=f"[bold]{label}[/bold]")
        t.add_column("Score band",  width=12)
        t.add_column("Picks",       justify="right", width=7)
        t.add_column("Avg return",  justify="right", width=12)
        t.add_column("Profitable",  justify="right", width=11)
        t.add_column("Dir acc",     justify="right", width=10)
        t.add_column("Return bar",  width=22)

        for lo, hi, s in half_bands:
            rc      = colour_ret(s["avg_ret"])
            is_peak = (lo, hi) == peak_band
            label_s = f"[bold]{lo}–{hi}[/bold]" if is_peak else f"{lo}–{hi}"
            marker  = " ◄" if is_peak else ""
            t.add_row(
                label_s + marker,
                str(s["n"]),
                f"[{rc}]{s['avg_ret']:+.3f}%[/{rc}]",
                f"{s['profitable']:.1f}%",
                f"{s['dir_acc']:.1f}%",
                bar(s["avg_ret"]),
            )

        console.print(t)
        return peak_band, peak_ret

    peak1, ret1 = half_table(first_half,  "First Half")
    console.print()
    peak2, ret2 = half_table(second_half, "Second Half")
    console.print()

    if peak1 and peak2:
        shift    = abs(peak1[0] - peak2[0])
        stable   = shift <= 10  # within 2 bands = stable

        console.print(Panel(
            f"  First half peak:    [cyan]{peak1[0]}–{peak1[1]}[/cyan]  "
            f"(avg ret {ret1:+.3f}%)\n"
            f"  Second half peak:   [cyan]{peak2[0]}–{peak2[1]}[/cyan]  "
            f"(avg ret {ret2:+.3f}%)\n"
            f"  Peak shift:         {shift} score points\n\n"
            + (
                f"  [bright_green]Stable[/bright_green] — sweet spot is consistent across both halves.\n"
                f"  A fixed ideal score target is likely reliable."
                if stable else
                f"  [yellow]Unstable[/yellow] — sweet spot shifted by {shift} points between halves.\n"
                f"  A fixed target may not be reliable; consider a dynamic range\n"
                f"  or wider sweet spot tolerance."
            ),
            title="[bold]Stability Assessment[/bold]",
            box=box.ROUNDED,
        ))

        # Also show overall trend: did returns improve or decline in second half?
        s1 = stats_for(first_half)
        s2 = stats_for(second_half)
        trend_dir = "improved" if s2["avg_ret"] > s1["avg_ret"] else "declined"
        console.print(Panel(
            f"  First half overall avg return:   {s1['avg_ret']:+.3f}%\n"
            f"  Second half overall avg return:  {s2['avg_ret']:+.3f}%\n"
            f"  Trend: returns [{'bright_green' if trend_dir == 'improved' else 'yellow'}]"
            f"{trend_dir}[/{'bright_green' if trend_dir == 'improved' else 'yellow'}] "
            f"in the second half\n\n"
            f"  [dim]A declining trend could reflect market regime change or\n"
            f"  the model performing better in trending vs ranging markets.[/dim]",
            title="[bold]Overall Return Trend[/bold]",
            box=box.ROUNDED,
        ))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    console.rule("[bold cyan]Score Sweet Spot Analysis[/bold cyan]")

    picks = load_picks()
    console.print(
        f"[dim]Loaded {len(picks)} resolved picks  |  "
        f"Score range: {min(p['score'] for p in picks)}–"
        f"{max(p['score'] for p in picks)}  |  "
        f"Weeks: {len(set(p['week'] for p in picks))}[/dim]\n"
    )

    bands, sweet_lo, sweet_hi = test1_fine_grained(picks)
    console.print()
    test2_temporal_stability(picks, bands)

    console.rule("[bold]Next Steps[/bold]")
    console.print(Panel(
        "  These results feed into Tests 3 and 4:\n\n"
        "  [bold]Test 3[/bold] — Backtest comparing ranking methods:\n"
        "    • Current:  rank by score descending (highest = best)\n"
        "    • Ideal:    rank by proximity to sweet spot centre\n"
        "    • Hybrid:   rank descending but cap/discard above sweet spot\n\n"
        "  [bold]Test 4[/bold] — Individual signal analysis:\n"
        "    • Which signals fire most in high-scoring (over-extended) picks?\n"
        "    • Which signals are most predictive of positive returns?\n"
        "    • Is the over-extension problem driven by specific signals?",
        title="[bold]What Comes Next[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
