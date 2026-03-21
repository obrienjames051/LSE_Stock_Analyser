"""
csv_log.py
----------
Saving picks to the CSV log file.
"""

import os
import csv

from .config import CSV_FILE, CSV_HEADERS, PREVIEW_LOG_FILE, PREVIEW_LOG_HEADERS
from .utils import console


def save_to_csv(picks: list, run_date: str):
    """Append this week's picks to the CSV log, creating it if needed."""
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        for r in picks:
            writer.writerow({
                "run_date":           run_date,
                "ticker":             r["ticker"],
                "sector":             r["sector"],
                "score":              r["score"],
                "price_p":            r["price"],
                "target_p":           r["target"],
                "stop_p":             r["stop"],
                "upside_pct":         round(r["upside_pct"], 2),
                "downside_pct":       round(r["downside_pct"], 2),
                "prob":               r["prob"],
                "reward_risk":        r["reward_risk"],
                "atr":                r["atr"],
                "allocated_gbp":      r.get("allocated_gbp", ""),
                "allocation_pct":     r.get("allocation_pct", ""),
                "shares":             r.get("shares", ""),
                "signals":            " | ".join(r["signals"]),
                "outcome_price_p":    "",
                "outcome_hit":        "",
                "outcome_return_pct": "",
                "outcome_notes":      "",
                "went_up":            "",  # filled in by calibration on next run
                "profitable":         "",  # filled in by calibration on next run
            })
    console.print(f"[dim]Picks saved to [bold]{CSV_FILE}[/bold][/dim]\n")


def save_preview_to_csv(picks: list, run_date: str):
    """
    Append preview run picks to lse_preview_log.csv.

    Saves every preview run regardless of day, so multiple entries per week
    are expected and intentional. Outcome columns are omitted — preview picks
    are for data tracking and visualisation only, not calibration.
    """
    file_exists = os.path.isfile(PREVIEW_LOG_FILE)
    with open(PREVIEW_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PREVIEW_LOG_HEADERS)
        if not file_exists:
            writer.writeheader()
        for r in picks:
            writer.writerow({
                "run_date":       run_date,
                "ticker":         r["ticker"],
                "sector":         r["sector"],
                "score":          r["score"],
                "price_p":        r["price"],
                "target_p":       r["target"],
                "stop_p":         r["stop"],
                "upside_pct":     round(r["upside_pct"], 2),
                "downside_pct":   round(r["downside_pct"], 2),
                "prob":           r["prob"],
                "reward_risk":    r["reward_risk"],
                "atr":            r["atr"],
                "allocated_gbp":  r.get("allocated_gbp", ""),
                "allocation_pct": r.get("allocation_pct", ""),
                "shares":         r.get("shares", ""),
                "signals":        " | ".join(r["signals"]),
            })
    console.print(f"[dim]Preview picks saved to [bold]{PREVIEW_LOG_FILE}[/bold][/dim]\n")
