"""
calibration.py
--------------
Automatic outcome back-filling and self-calibration engine.
"""

import os
import csv
from datetime import datetime, timedelta

import yfinance as yf
from rich.panel import Panel
from rich import box

from .config import (
    CSV_FILE, CSV_HEADERS,
    MIN_OUTCOMES_TO_CALIBRATE, CALIBRATION_WINDOW, MAX_CALIBRATION_SHIFT,
)
from .utils import console, silent


def resolve_pending_outcomes() -> int:
    """
    Find picks from 7+ days ago with no outcome recorded, fetch their
    actual closing prices from Yahoo Finance, and fill in the CSV.
    Returns the number of rows updated.
    """
    if not os.path.isfile(CSV_FILE):
        return 0

    rows = []
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    if not rows:
        return 0

    now, updated = datetime.now(), 0

    with console.status("[bold green]Checking for pending outcomes to resolve...") as status:
        for row in rows:
            if row.get("outcome_price_p", "").strip():
                continue
            try:
                run_dt = datetime.strptime(row["run_date"], "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if (now - run_dt).days < 7:
                continue

            target_date = run_dt + timedelta(days=7)
            status.update(f"[bold green]Resolving {row['ticker']}...")
            try:
                start = target_date - timedelta(days=3)
                end   = target_date + timedelta(days=3)
                with silent():
                    df = yf.download(
                        row["ticker"] + ".L",
                        start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"),
                        interval="1d", progress=False, auto_adjust=True,
                    )
                if df is None or df.empty:
                    row["outcome_notes"] = "Could not fetch outcome data"
                    continue

                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
                outcome_price = float(df["close"].iloc[-1])
                entry_price   = float(row["price_p"])
                target_price  = float(row["target_p"])
                return_pct    = (outcome_price - entry_price) / entry_price * 100
                hit           = "YES" if outcome_price >= target_price else "NO"

                if hit == "YES":
                    note = f"Target reached. +{return_pct:.1f}%"
                elif return_pct >= 0:
                    note = f"Rose but missed target. +{return_pct:.1f}%"
                elif float(row["stop_p"]) > 0 and outcome_price <= float(row["stop_p"]):
                    note = f"Stop-loss triggered. {return_pct:.1f}%"
                else:
                    note = f"Below entry, above stop. {return_pct:.1f}%"

                row["outcome_price_p"]    = round(outcome_price, 2)
                row["outcome_hit"]        = hit
                row["outcome_return_pct"] = round(return_pct, 2)
                row["outcome_notes"]      = note
                updated += 1
            except Exception as e:
                row["outcome_notes"] = f"Fetch error: {e}"

    if updated > 0:
        for row in rows:
            for h in CSV_HEADERS:
                if h not in row:
                    row[h] = ""
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        console.print(f"[green]Resolved {updated} pending outcome(s).[/green]")

    return updated


def compute_calibration() -> dict:
    """
    Read historical outcomes and compute probability adjustment.
    Returns a calibration dict with adjustment value and status message.
    """
    neutral = {
        "n_resolved": 0, "actual_hit_rate": None, "avg_predicted_prob": None,
        "calibration_bias": 0.0, "prob_adjustment": 0.0, "avg_return_pct": None,
        "calibrated": False,
        "status": f"Not yet calibrated (need {MIN_OUTCOMES_TO_CALIBRATE} resolved picks)",
    }
    if not os.path.isfile(CSV_FILE):
        return neutral

    resolved = []
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("outcome_hit", "").strip() in ("YES", "NO"):
                resolved.append(row)

    if len(resolved) < MIN_OUTCOMES_TO_CALIBRATE:
        neutral["n_resolved"] = len(resolved)
        neutral["status"] = (f"Not yet calibrated -- {len(resolved)} resolved so far, "
                             f"need {MIN_OUTCOMES_TO_CALIBRATE}")
        return neutral

    resolved        = resolved[-CALIBRATION_WINDOW:]
    hits            = [r for r in resolved if r["outcome_hit"] == "YES"]
    actual_hit_rate = len(hits) / len(resolved) * 100
    predicted_probs, returns = [], []

    for r in resolved:
        try: predicted_probs.append(float(r["prob"]))
        except (ValueError, KeyError): pass
        try: returns.append(float(r["outcome_return_pct"]))
        except (ValueError, KeyError): pass

    avg_predicted = sum(predicted_probs) / len(predicted_probs) if predicted_probs else 50.0
    avg_return    = sum(returns) / len(returns) if returns else 0.0
    bias          = avg_predicted - actual_hit_rate
    adjustment    = max(-MAX_CALIBRATION_SHIFT, min(MAX_CALIBRATION_SHIFT, bias))

    if abs(bias) < 3.0:
        status = f"Well-calibrated (bias: {bias:+.1f}pp across {len(resolved)} picks)"
    elif bias > 0:
        status = (f"Over-confident by {bias:.1f}pp -- reducing probabilities by "
                  f"{adjustment:.1f}pp to compensate")
    else:
        status = (f"Under-confident by {abs(bias):.1f}pp -- increasing probabilities by "
                  f"{abs(adjustment):.1f}pp to compensate")

    return {
        "n_resolved": len(resolved), "actual_hit_rate": round(actual_hit_rate, 1),
        "avg_predicted_prob": round(avg_predicted, 1), "calibration_bias": round(bias, 2),
        "prob_adjustment": round(adjustment, 2), "avg_return_pct": round(avg_return, 2),
        "calibrated": True, "status": status,
    }


def print_performance_report(cal: dict):
    """Print the historical performance and calibration panel at startup."""
    if cal["n_resolved"] == 0:
        console.print("[dim]No resolved outcomes yet -- report appears after first 7-day window.[/dim]\n")
        return

    lines = [f"[bold]Historical Performance[/bold]  [dim]({cal['n_resolved']} resolved pick(s))[/dim]\n"]

    if cal["actual_hit_rate"] is not None:
        hc = ("bright_green" if cal["actual_hit_rate"] >= 50
              else "yellow" if cal["actual_hit_rate"] >= 35 else "red")
        lines.append(f"  Hit rate:   [{hc}]{cal['actual_hit_rate']:.1f}%[/{hc}]  "
                     f"[dim](model predicted avg {cal['avg_predicted_prob']:.1f}%)[/dim]")

    if cal["avg_return_pct"] is not None:
        rc = "bright_green" if cal["avg_return_pct"] >= 0 else "red"
        lines.append(f"  Avg return: [{rc}]{cal['avg_return_pct']:+.2f}%[/{rc}]")

    lines.append(f"\n  Calibration: [italic]{cal['status']}[/italic]")

    if cal["calibrated"] and abs(cal["prob_adjustment"]) >= 1.0:
        d = "reduced" if cal["prob_adjustment"] > 0 else "increased"
        lines.append(f"  [dim]Today's probabilities {d} by {abs(cal['prob_adjustment']):.1f}pp.[/dim]")

    console.print(Panel("\n".join(lines), title="Track Record", box=box.ROUNDED))
    console.print()
