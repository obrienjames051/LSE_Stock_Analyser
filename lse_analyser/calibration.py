"""
calibration.py
--------------
Automatic outcome back-filling and self-calibration engine.

Reads from three sources with different weights:
  - lse_screener_log.csv       (live picks, weight 1.0)
  - lse_backtest_technical.csv (technical backtest, weight 0.6)
  - lse_backtest_news.csv      (news backtest, weight 0.3)

Once CALIBRATION_LIVE_THRESHOLD live picks are resolved, backtest data
is phased out and only live picks are used for calibration.
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
    BACKTEST_TECHNICAL_CSV, BACKTEST_NEWS_CSV,
    CALIBRATION_WEIGHT_LIVE, CALIBRATION_WEIGHT_TECHNICAL, CALIBRATION_WEIGHT_NEWS,
    CALIBRATION_LIVE_THRESHOLD,
)
from .utils import console, silent


def resolve_pending_outcomes() -> int:
    """
    Find live picks from 7+ days ago with no outcome recorded, fetch their
    actual closing prices, and fill in the CSV.
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

                df.columns = [
                    c[0].lower() if isinstance(c, tuple) else c.lower()
                    for c in df.columns
                ]
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
    Read historical outcomes from all three sources and compute a
    weighted probability adjustment.

    Returns a calibration dict with adjustment value and status message.
    """
    neutral = {
        "n_resolved": 0, "actual_hit_rate": None, "avg_predicted_prob": None,
        "calibration_bias": 0.0, "prob_adjustment": 0.0, "avg_return_pct": None,
        "kelly_avg_return": None,
        "calibrated": False, "live_count": 0, "backtest_used": False,
        "status": f"Not yet calibrated (need {MIN_OUTCOMES_TO_CALIBRATE} resolved picks)",
    }

    # Load live picks
    live_rows = _load_resolved(CSV_FILE)
    live_count = len(live_rows)

    # Decide whether to include backtest data
    use_backtest = live_count < CALIBRATION_LIVE_THRESHOLD

    # Build weighted pool
    weighted_pool = []

    for row in live_rows[-CALIBRATION_WINDOW:]:
        weighted_pool.append((row, CALIBRATION_WEIGHT_LIVE))

    if use_backtest:
        tech_rows = _load_resolved(BACKTEST_TECHNICAL_CSV)
        for row in tech_rows[-CALIBRATION_WINDOW:]:
            weighted_pool.append((row, CALIBRATION_WEIGHT_TECHNICAL))

        news_rows = _load_resolved(BACKTEST_NEWS_CSV)
        for row in news_rows[-CALIBRATION_WINDOW:]:
            weighted_pool.append((row, CALIBRATION_WEIGHT_NEWS))

    if not weighted_pool:
        neutral["status"] = (
            f"Not yet calibrated -- no resolved picks found. "
            f"Run backtest mode (B) to bootstrap calibration."
        )
        return neutral

    # Compute weighted directional accuracy (stock ended week above entry)
    # and predicted prob -- now represents "probability of rising"
    total_weight     = sum(w for _, w in weighted_pool)

    # Directional hit: outcome_return_pct > 0 means stock rose
    weighted_dir_hits = sum(
        w for row, w in weighted_pool
        if _is_directional_hit(row)
    )
    weighted_probs   = []
    weighted_returns = []

    for row, w in weighted_pool:
        try:
            weighted_probs.append((float(row["prob"]), w))
        except (ValueError, KeyError):
            pass
        try:
            weighted_returns.append((float(row["outcome_return_pct"]), w))
        except (ValueError, KeyError):
            pass

    n_resolved      = len(weighted_pool)
    actual_hit_rate = (weighted_dir_hits / total_weight) * 100
    avg_predicted   = (
        sum(p * w for p, w in weighted_probs) / sum(w for _, w in weighted_probs)
        if weighted_probs else 50.0
    )
    avg_return = (
        sum(r * w for r, w in weighted_returns) / sum(w for _, w in weighted_returns)
        if weighted_returns else 0.0
    )

    # Kelly-weighted return: weight each pick's return by its allocation_pct
    # This reflects what you'd actually earn under realistic position sizing
    kelly_weighted_returns = []
    for row, w in weighted_pool:
        try:
            ret      = float(row["outcome_return_pct"])
            alloc    = float(row["allocation_pct"])
            kelly_weighted_returns.append((ret, alloc, w))
        except (ValueError, KeyError):
            pass

    if kelly_weighted_returns:
        # Within each calibration-weighted pick, weight by allocation_pct
        total_kw = sum(alloc * w for _, alloc, w in kelly_weighted_returns)
        kelly_avg_return = (
            sum(ret * alloc * w for ret, alloc, w in kelly_weighted_returns) / total_kw
            if total_kw > 0 else 0.0
        )
    else:
        kelly_avg_return = None

    if n_resolved < MIN_OUTCOMES_TO_CALIBRATE:
        neutral["n_resolved"] = n_resolved
        neutral["live_count"] = live_count
        neutral["status"]     = (
            f"Not yet calibrated -- {n_resolved} weighted picks so far, "
            f"need {MIN_OUTCOMES_TO_CALIBRATE}"
        )
        return neutral

    bias       = avg_predicted - actual_hit_rate
    adjustment = max(-MAX_CALIBRATION_SHIFT, min(MAX_CALIBRATION_SHIFT, bias))

    if abs(bias) < 3.0:
        status = f"Well-calibrated (bias: {bias:+.1f}pp)"
    elif bias > 0:
        status = (
            f"Over-confident by {bias:.1f}pp -- reducing probabilities "
            f"by {adjustment:.1f}pp"
        )
    else:
        status = (
            f"Under-confident by {abs(bias):.1f}pp -- increasing probabilities "
            f"by {abs(adjustment):.1f}pp"
        )

    if use_backtest and live_count < CALIBRATION_LIVE_THRESHOLD:
        remaining = CALIBRATION_LIVE_THRESHOLD - live_count
        status += (
            f"\n  [dim]Using backtest data to supplement "
            f"({live_count} live picks -- backtest phases out after "
            f"{remaining} more live picks)[/dim]"
        )

    return {
        "n_resolved":        n_resolved,
        "actual_hit_rate":   round(actual_hit_rate, 1),
        "avg_predicted_prob": round(avg_predicted, 1),
        "calibration_bias":  round(bias, 2),
        "prob_adjustment":   round(adjustment, 2),
        "avg_return_pct":    round(avg_return, 2),
        "kelly_avg_return":  round(kelly_avg_return, 2) if kelly_avg_return is not None else None,
        "calibrated":        True,
        "live_count":        live_count,
        "backtest_used":     use_backtest,
        "status":            status,
    }


def print_performance_report(cal: dict):
    """Print the historical performance and calibration panel at startup."""
    if cal["n_resolved"] == 0:
        console.print(
            "[dim]No resolved outcomes yet -- run Backtest mode (B) to "
            "bootstrap calibration immediately, or wait for live picks "
            "to accumulate.[/dim]\n"
        )
        return

    lines = [
        f"[bold]Historical Performance[/bold]  "
        f"[dim]({cal['n_resolved']} weighted pick(s)  |  "
        f"{cal['live_count']} live)[/dim]\n"
    ]

    if cal["actual_hit_rate"] is not None:
        hc = (
            "bright_green" if cal["actual_hit_rate"] >= 55
            else "yellow" if cal["actual_hit_rate"] >= 48 else "red"
        )
        lines.append(
            f"  Directional accuracy: [{hc}]{cal['actual_hit_rate']:.1f}%[/{hc}]  "
            f"[dim](model predicted avg {cal['avg_predicted_prob']:.1f}% probability of rising)[/dim]"
        )

    if cal["avg_return_pct"] is not None:
        rc   = "bright_green" if cal["avg_return_pct"] >= 0 else "red"
        lines.append(f"  Avg return (simple):         [{rc}]{cal['avg_return_pct']:+.2f}%[/{rc}]  [dim]per pick, unweighted[/dim]")

    if cal.get("kelly_avg_return") is not None:
        kc   = "bright_green" if cal["kelly_avg_return"] >= 0 else "red"
        lines.append(f"  Avg return (Kelly-weighted): [{kc}]{cal['kelly_avg_return']:+.2f}%[/{kc}]  [dim]weighted by position size[/dim]")

    if cal.get("backtest_used"):
        lines.append(f"\n  [dim]Calibration includes backtest data (weighted).[/dim]")

    lines.append(f"\n  Calibration: [italic]{cal['status']}[/italic]")

    if cal["calibrated"] and abs(cal["prob_adjustment"]) >= 1.0:
        d = "reduced" if cal["prob_adjustment"] > 0 else "increased"
        lines.append(
            f"  [dim]Today's probabilities {d} by "
            f"{abs(cal['prob_adjustment']):.1f}pp.[/dim]"
        )

    console.print(Panel("\n".join(lines), title="Track Record", box=box.ROUNDED))
    console.print()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_resolved(filepath: str) -> list:
    """Load resolved picks from a CSV file. Returns empty list if unavailable."""
    if not os.path.isfile(filepath):
        return []
    rows = []
    try:
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("outcome_hit", "").strip() in ("YES", "NO"):
                    rows.append(row)
    except Exception:
        pass
    return rows


def _is_directional_hit(row: dict) -> bool:
    """
    Returns True if the stock ended the week above its entry price.
    This is the directional accuracy measure -- did the model correctly
    predict the stock would rise, regardless of whether it hit the target.
    """
    try:
        return float(row["outcome_return_pct"]) > 0
    except (ValueError, KeyError):
        # Fall back to target hit if return not available
        return row.get("outcome_hit") == "YES"
