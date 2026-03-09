"""
analyse_score_threshold.py
--------------------------
Analyses whether low-scoring picks drag down overall returns, and whether
there is a natural score threshold below which picks should be skipped.

Uses the existing lse_backtest_technical.csv — no new backtest needed.

Answers:
  1. Do higher-scored picks outperform lower-scored ones?
  2. Is there a score threshold below which avg return turns negative?
  3. What % of picks fall below each candidate threshold?
  4. What would overall avg return be if low-scoring picks were skipped?

Run from LSE_Stock_Analyser folder:
    python analyse_score_threshold.py
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


def load_picks():
    if not os.path.isfile(BACKTEST_TECHNICAL_CSV):
        console.print(f"[red]Backtest CSV not found: {BACKTEST_TECHNICAL_CSV}[/red]")
        console.print("[dim]Run backtest mode first to generate the data.[/dim]")
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
            if not ret or not sc:
                continue
            try:
                picks.append({
                    "score":      int(float(sc)),
                    "return":     float(ret),
                    "went_up":    wu == "1",
                    "profitable": prof == "1",
                    "target_hit": hit == "YES",
                    "stop_out":   "Stop" in note,
                    "week":       row.get("run_date", "")[:10],
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


def main():
    console.rule("[bold cyan]Score Threshold Analysis[/bold cyan]")

    picks = load_picks()
    console.print(f"[dim]Loaded {len(picks)} resolved picks from {BACKTEST_TECHNICAL_CSV}[/dim]\n")

    scores = [p["score"] for p in picks]
    console.print(
        f"[dim]Score range: {min(scores)} to {max(scores)}  |  "
        f"Avg: {sum(scores)/len(scores):.1f}  |  "
        f"Median: {sorted(scores)[len(scores)//2]}[/dim]\n"
    )

    # ── Section 1: Performance by score decile ────────────────────────────────
    console.rule("[bold]1. Performance by Score Decile[/bold]")

    sorted_scores = sorted(set(scores))
    n_deciles     = 10
    step          = (max(scores) - min(scores)) / n_deciles

    decile_table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    decile_table.add_column("Score range",    width=16)
    decile_table.add_column("Picks",          justify="right", width=8)
    decile_table.add_column("Avg return",     justify="right", width=12)
    decile_table.add_column("Profitable",     justify="right", width=12)
    decile_table.add_column("Directional",    justify="right", width=12)
    decile_table.add_column("Stop-out",       justify="right", width=10)
    decile_table.add_column("Std dev",        justify="right", width=10)

    decile_stats = []
    for i in range(n_deciles):
        lo = min(scores) + i * step
        hi = lo + step
        bucket = [p for p in picks if lo <= p["score"] < hi]
        if not bucket and i == n_deciles - 1:
            bucket = [p for p in picks if p["score"] == max(scores)]
        if not bucket:
            continue
        s = stats_for(bucket)
        decile_stats.append((lo, hi, s))
        rc = "bright_green" if s["avg_ret"] >= 0.5 else "yellow" if s["avg_ret"] >= 0 else "red"
        decile_table.add_row(
            f"{int(lo)}–{int(hi)}",
            str(s["n"]),
            f"[{rc}]{s['avg_ret']:+.3f}%[/{rc}]",
            f"{s['profitable']:.1f}%",
            f"{s['dir_acc']:.1f}%",
            f"{s['stop_pct']:.1f}%",
            f"{s['std']:.3f}%",
        )

    console.print(decile_table)

    # ── Section 2: Performance by score quartile ──────────────────────────────
    console.rule("[bold]2. Performance by Score Quartile[/bold]")

    sorted_picks = sorted(picks, key=lambda p: p["score"])
    q_size       = len(sorted_picks) // 4
    quartiles    = [
        ("Q1 (lowest 25%)",  sorted_picks[:q_size]),
        ("Q2",               sorted_picks[q_size:q_size*2]),
        ("Q3",               sorted_picks[q_size*2:q_size*3]),
        ("Q4 (highest 25%)", sorted_picks[q_size*3:]),
    ]

    q_table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    q_table.add_column("Quartile",        width=20)
    q_table.add_column("Score range",     width=14)
    q_table.add_column("Picks",           justify="right", width=8)
    q_table.add_column("Avg return",      justify="right", width=12)
    q_table.add_column("Profitable",      justify="right", width=12)
    q_table.add_column("Directional",     justify="right", width=12)
    q_table.add_column("Stop-out",        justify="right", width=10)

    q_stats = {}
    for label, qpicks in quartiles:
        s  = stats_for(qpicks)
        sc = [p["score"] for p in qpicks]
        q_stats[label] = s
        rc = "bright_green" if s["avg_ret"] >= 0.5 else "yellow" if s["avg_ret"] >= 0 else "red"
        q_table.add_row(
            label,
            f"{min(sc)}–{max(sc)}",
            str(s["n"]),
            f"[{rc}]{s['avg_ret']:+.3f}%[/{rc}]",
            f"{s['profitable']:.1f}%",
            f"{s['dir_acc']:.1f}%",
            f"{s['stop_pct']:.1f}%",
        )

    console.print(q_table)

    # ── Section 3: Candidate threshold analysis ───────────────────────────────
    console.rule("[bold]3. Candidate Score Thresholds[/bold]")
    console.print("[dim]For each threshold: what if we skipped picks below it?[/dim]\n")

    # Find natural candidates: every 5 points across the range
    score_min = min(scores)
    score_max = max(scores)
    candidates = list(range(
        (score_min // 5) * 5,
        (score_max // 5) * 5 + 5,
        5
    ))

    thresh_table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    thresh_table.add_column("Min score",      width=12)
    thresh_table.add_column("Picks kept",     justify="right", width=12)
    thresh_table.add_column("Picks skipped",  justify="right", width=14)
    thresh_table.add_column("% skipped",      justify="right", width=12)
    thresh_table.add_column("Avg return",     justify="right", width=12)
    thresh_table.add_column("vs baseline",    justify="right", width=12)
    thresh_table.add_column("Profitable",     justify="right", width=12)

    baseline = stats_for(picks)
    baseline_ret = baseline["avg_ret"]

    thresh_stats = []
    for thresh in candidates:
        kept    = [p for p in picks if p["score"] >= thresh]
        skipped = [p for p in picks if p["score"] < thresh]
        if len(kept) < 10:
            break
        s       = stats_for(kept)
        diff    = round(s["avg_ret"] - baseline_ret, 3)
        pct_skip = round(len(skipped) / len(picks) * 100, 1)

        thresh_stats.append((thresh, s, diff, pct_skip, len(skipped)))

        rc   = "bright_green" if s["avg_ret"] >= baseline_ret else "yellow" if s["avg_ret"] >= 0 else "red"
        dc   = "bright_green" if diff > 0 else "red" if diff < -0.05 else "dim"

        thresh_table.add_row(
            str(thresh),
            str(len(kept)),
            str(len(skipped)),
            f"{pct_skip:.1f}%",
            f"[{rc}]{s['avg_ret']:+.3f}%[/{rc}]",
            f"[{dc}]{diff:+.3f}%[/{dc}]",
            f"{s['profitable']:.1f}%",
        )

    console.print(thresh_table)

    # ── Section 4: What happens to skipped picks ──────────────────────────────
    console.rule("[bold]4. Profile of Low-Scoring Picks[/bold]")
    console.print("[dim]How do the picks that would be skipped actually perform?[/dim]\n")

    # Find the threshold that maximises return
    if thresh_stats:
        best_thresh = max(thresh_stats, key=lambda x: x[1]["avg_ret"])
        best_t      = best_thresh[0]
        skipped_at_best = [p for p in picks if p["score"] < best_t]

        if skipped_at_best:
            s_skip = stats_for(skipped_at_best)
            s_keep = stats_for([p for p in picks if p["score"] >= best_t])

            console.print(Panel(
                f"  Threshold that maximises avg return: [bold cyan]score >= {best_t}[/bold cyan]\n\n"
                f"  Picks KEPT  (score >= {best_t}):  "
                f"n={s_keep['n']}  "
                f"avg=[bright_green]{s_keep['avg_ret']:+.3f}%[/bright_green]  "
                f"profitable={s_keep['profitable']:.1f}%  "
                f"stop-out={s_keep['stop_pct']:.1f}%\n"
                f"  Picks SKIPPED (score < {best_t}):   "
                f"n={s_skip['n']}  "
                f"avg=[yellow]{s_skip['avg_ret']:+.3f}%[/yellow]  "
                f"profitable={s_skip['profitable']:.1f}%  "
                f"stop-out={s_skip['stop_pct']:.1f}%\n\n"
                f"  Baseline (all picks):              "
                f"n={baseline['n']}  "
                f"avg={baseline_ret:+.3f}%  "
                f"profitable={baseline['profitable']:.1f}%",
                title="[bold]Best Threshold Summary[/bold]",
                box=box.ROUNDED,
            ))

    # ── Section 5: Weekly impact ──────────────────────────────────────────────
    console.rule("[bold]5. Weekly Impact — How Often Would Picks Be Skipped?[/bold]")

    if thresh_stats:
        best_t = best_thresh[0]
        weeks  = defaultdict(lambda: {"all": [], "kept": [], "skipped": []})

        for p in picks:
            w = p["week"]
            weeks[w]["all"].append(p["return"])
            if p["score"] >= best_t:
                weeks[w]["kept"].append(p["return"])
            else:
                weeks[w]["skipped"].append(p["return"])

        weeks_with_skips  = sum(1 for w in weeks.values() if w["skipped"])
        weeks_all_skipped = sum(1 for w in weeks.values() if not w["kept"])
        avg_skipped_pw    = round(
            sum(len(w["skipped"]) for w in weeks.values()) / len(weeks), 2
        )

        console.print(Panel(
            f"  Total weeks simulated:          {len(weeks)}\n"
            f"  Weeks with at least 1 skip:     {weeks_with_skips} "
            f"({weeks_with_skips/len(weeks)*100:.0f}% of weeks)\n"
            f"  Weeks where ALL picks skipped:  {weeks_all_skipped}\n"
            f"  Avg picks skipped per week:     {avg_skipped_pw:.1f}\n\n"
            f"  [dim]In weeks with all picks skipped, the programme would hold cash.\n"
            f"  This is intentional -- no edge that week is better than a forced trade.[/dim]",
            title=f"[bold]Weekly Impact at Threshold {best_t}[/bold]",
            box=box.ROUNDED,
        ))

    # ── Final recommendation ──────────────────────────────────────────────────
    console.rule("[bold]Summary & Recommendation[/bold]")

    console.print(Panel(
        f"  Baseline avg return (all picks):  {baseline_ret:+.3f}%\n"
        f"  Score range in backtest:          {score_min} to {score_max}\n\n"
        f"  [dim]Key question: is there a meaningful return difference between\n"
        f"  high and low scoring picks, or does score not predict weekly return?\n\n"
        f"  If Q1 (lowest quartile) avg return is close to Q4 (highest quartile),\n"
        f"  score is not a useful filter and the threshold adds no value.\n\n"
        f"  If Q1 clearly underperforms, a threshold is justified -- and the\n"
        f"  programme should hold cash rather than force a weak pick.[/dim]",
        title="[bold]How to Interpret These Results[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
