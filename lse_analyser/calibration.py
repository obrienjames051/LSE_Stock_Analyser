"""
calibration.py
--------------
Automatic outcome back-filling and self-calibration engine.

Reads from two sources with different weights:
  - lse_screener_log.csv       (live picks, weight 1.0)
  - lse_backtest_technical.csv (technical backtest, weight 0.6)

Once CALIBRATION_LIVE_THRESHOLD live picks are resolved, backtest data
is phased out and only live picks are used for calibration.

Outcome resolution uses the confirmed strategy window:
  - Picks are entered at Tuesday open
  - Outcomes resolve at Monday close (6 days after run_date)
  - Resolution is triggered on Tuesday morning runs, checking for picks
    from the previous week (5+ days ago) with no outcome recorded
  - outcome_hit: did the stock reach the predicted target by Monday close?
  - went_up: did Monday close > Tuesday open? (directional accuracy metric)
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
    BACKTEST_TECHNICAL_CSV,
    CALIBRATION_WEIGHT_LIVE, CALIBRATION_WEIGHT_TECHNICAL,
    CALIBRATION_LIVE_THRESHOLD,
)
from .utils import console, silent


def resolve_pending_outcomes() -> int:
    """
    Find live picks from 5+ days ago with no outcome recorded, fetch their
    Monday close price, and fill in the CSV.
    5-day threshold: run on Tuesday morning -> the following Monday close
    (6 days after entry) is now available, so picks from last Tuesday resolve.
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
            # Resolve if 5+ days have passed (Tuesday run -> last Monday close available)
            if (now - run_dt).days < 5:
                continue

            # Outcome date = following Monday close = run_date + 6 days
            # e.g. entered Tuesday 11 Mar -> exits Monday 17 Mar
            outcome_date = run_dt + timedelta(days=6)
            status.update(f"[bold green]Resolving {row['ticker']}...")

            try:
                start = outcome_date - timedelta(days=2)
                end   = outcome_date + timedelta(days=2)

                ticker_l = row["ticker"] + ".L"
                with silent():
                    df = yf.download(
                        ticker_l,
                        start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"),
                        interval="1d", progress=False, auto_adjust=True,
                    )

                if df is None or df.empty:
                    # Try without .L
                    with silent():
                        df = yf.download(
                            row["ticker"],
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

                # Use the closest available close to outcome_date
                outcome_df = df[df.index <= pd_ts(outcome_date)]
                if outcome_df.empty:
                    outcome_df = df.head(1)

                outcome_price = float(outcome_df["close"].iloc[-1])
                entry_price   = float(row["price_p"])
                target_price  = float(row["target_p"])
                return_pct    = (outcome_price - entry_price) / entry_price * 100

                # outcome_hit: did stock reach predicted target by Monday close?
                hit = "YES" if outcome_price >= target_price else "NO"

                # went_up: did Monday close exceed Tuesday open (entry price)?
                went_up = "1" if outcome_price > entry_price else "0"

                if hit == "YES":
                    note = f"Target reached. {return_pct:+.1f}%"
                elif return_pct >= 0:
                    note = f"Rose but missed target. {return_pct:+.1f}%"
                elif float(row.get("stop_p", 0) or 0) > 0 and outcome_price <= float(row.get("stop_p", 0)):
                    note = f"Stop-loss triggered. {return_pct:.1f}%"
                else:
                    note = f"Below entry, above stop. {return_pct:.1f}%"

                row["outcome_price_p"]    = round(outcome_price, 2)
                row["outcome_hit"]        = hit
                row["outcome_return_pct"] = round(return_pct, 2)
                row["outcome_notes"]      = note
                row["went_up"]            = went_up

                # profitable: was the actual exit price > entry?
                # For live picks we use outcome_price (Monday close) as exit
                # since we don't track whether a stop was hit during the week
                row["profitable"] = "1" if outcome_price > entry_price else "0"

                updated += 1

            except Exception:
                continue

    if updated:
        _rewrite_csv(rows)
        console.print(f"[green]{updated} outcome(s) resolved.[/green]\n")
    else:
        console.print("[dim]No new outcomes to resolve.[/dim]\n")

    return updated


def pd_ts(dt):
    """Convert datetime to pandas Timestamp."""
    import pandas as pd
    return pd.Timestamp(dt)


def compute_calibration() -> dict:
    """
    Compute the probability adjustment and performance metrics from
    resolved live picks and backtest data.

    Calibration compares the model's average P(rise) prediction against the
    actual directional accuracy (% of picks where stock rose Tue->Mon).
    This is correct because P(rise) means "will the stock go up at all",
    not "will it hit the target price".

    Returns a dict with:
      prob_adjustment     -- pp to add/subtract from raw model probabilities
      calibrated          -- bool, whether enough data exists
      n_live              -- number of resolved live picks
      n_backtest          -- number of resolved backtest picks used
      directional_acc     -- % of live picks where stock rose (None if insufficient)
      target_hit_rate     -- % of live picks that reached predicted target
      avg_return          -- average return per live pick in %
      bt_directional_acc  -- backtest directional accuracy (for reference)
      bt_avg_return       -- backtest average return per pick (for reference)
    """
    live_rows     = _load_resolved(CSV_FILE, min_days=5)
    backtest_rows = _load_resolved(BACKTEST_TECHNICAL_CSV, min_days=0)

    n_live = len(live_rows)

    # Phase out backtest data once enough live picks accumulate
    use_backtest = [] if n_live >= CALIBRATION_LIVE_THRESHOLD else backtest_rows
    n_backtest   = len(use_backtest)
    all_rows     = live_rows + use_backtest

    # Backtest reference stats (always computed regardless of live threshold)
    bt_dir_acc, bt_avg_ret = _backtest_stats(backtest_rows)

    _none = {
        "prob_adjustment":    0.0,
        "calibrated":         False,
        "n_live":             n_live,
        "n_backtest":         n_backtest,
        "directional_acc":    None,
        "profitable_rate":    None,
        "target_hit_rate":    None,
        "avg_return":         None,
        "bt_directional_acc": bt_dir_acc,
        "bt_avg_return":      bt_avg_ret,
    }

    if len(all_rows) < MIN_OUTCOMES_TO_CALIBRATE:
        return _none

    # Use most recent CALIBRATION_WINDOW picks
    window      = all_rows[-CALIBRATION_WINDOW:]
    live_window = [r for r in window if r["_source"] == "live"]
    bt_window   = [r for r in window if r["_source"] == "backtest"]

    # Calibration uses "profitable" (exit price > entry price) vs predicted probability.
    # This is the most honest signal: did the trade actually make money given the
    # full strategy including stops? Unlike raw directional accuracy, it correctly
    # penalises stop-outs even if the stock recovered by Monday.
    def _weighted_profitable(rows, weight):
        prof  = sum(weight for r in rows if str(r.get("profitable", "")) == "1")
        total = sum(weight for r in rows if str(r.get("profitable", "")) in ("0", "1"))
        return prof, total

    live_prof, live_total = _weighted_profitable(live_window, CALIBRATION_WEIGHT_LIVE)
    bt_prof,   bt_total   = _weighted_profitable(bt_window,   CALIBRATION_WEIGHT_TECHNICAL)

    total_weight = live_total + bt_total
    if total_weight == 0:
        return _none

    weighted_dir_rate = (live_prof + bt_prof) / total_weight * 100

    # Average predicted probability in the window
    probs = [float(r["prob"]) / 100 if float(r["prob"]) > 1 else float(r["prob"])
             for r in window if r.get("prob")]
    avg_predicted_prob = (sum(probs) / len(probs) * 100) if probs else 50.0

    raw_adjustment  = weighted_dir_rate - avg_predicted_prob
    prob_adjustment = max(-MAX_CALIBRATION_SHIFT,
                          min(MAX_CALIBRATION_SHIFT, raw_adjustment))

    # Live-only metrics
    live_resolved = [r for r in live_rows if r.get("outcome_hit") in ("YES", "NO")]
    min_n = MIN_OUTCOMES_TO_CALIBRATE

    # Directional accuracy: Mon close > Tue open (raw model skill, display only)
    live_with_dir = [r for r in live_rows if str(r.get("went_up", "")) in ("0", "1")]
    directional_acc = (
        round(sum(1 for r in live_with_dir if str(r.get("went_up")) == "1") / len(live_with_dir) * 100, 1)
        if len(live_with_dir) >= min_n else None
    )

    # Profitable rate: exit price > entry (used for calibration, also shown)
    live_with_prof = [r for r in live_rows if str(r.get("profitable", "")) in ("0", "1")]
    profitable_rate = (
        round(sum(1 for r in live_with_prof if str(r.get("profitable")) == "1") / len(live_with_prof) * 100, 1)
        if len(live_with_prof) >= min_n else None
    )

    # Target hit rate
    target_hit_rate = (
        round(sum(1 for r in live_resolved if r["outcome_hit"] == "YES") / len(live_resolved) * 100, 1)
        if live_resolved else None
    )

    # Average return
    live_returns = [float(r["outcome_return_pct"]) for r in live_resolved
                    if r.get("outcome_return_pct", "").strip()]
    avg_return = round(sum(live_returns) / len(live_returns), 3) if live_returns else None

    return {
        "prob_adjustment":    round(prob_adjustment, 1),
        "calibrated":         True,
        "n_live":             n_live,
        "n_backtest":         n_backtest,
        "directional_acc":    directional_acc,
        "profitable_rate":    profitable_rate,
        "target_hit_rate":    target_hit_rate,
        "avg_return":         avg_return,
        "bt_directional_acc": bt_dir_acc,
        "bt_avg_return":      bt_avg_ret,
    }


def _backtest_stats(backtest_rows: list) -> tuple:
    """Compute directional accuracy and avg return from backtest data."""
    with_dir = [r for r in backtest_rows if str(r.get("went_up", "")) in ("0", "1")]
    bt_dir   = (
        round(sum(1 for r in with_dir if str(r.get("went_up")) == "1") / len(with_dir) * 100, 1)
        if with_dir else None
    )
    returns  = [float(r["outcome_return_pct"]) for r in backtest_rows
                if r.get("outcome_return_pct", "").strip()]
    bt_ret   = round(sum(returns) / len(returns), 3) if returns else None
    return bt_dir, bt_ret


def print_performance_report(cal: dict):
    """Print the calibration and performance summary panel."""
    n_live     = cal["n_live"]
    n_backtest = cal["n_backtest"]
    adj        = cal["prob_adjustment"]
    dir_acc    = cal.get("directional_acc")
    prof_rate  = cal.get("profitable_rate")
    tgt_rate   = cal.get("target_hit_rate")
    avg_ret    = cal.get("avg_return")
    bt_dir     = cal.get("bt_directional_acc")
    bt_ret     = cal.get("bt_avg_return")
    needed     = max(0, MIN_OUTCOMES_TO_CALIBRATE - n_live)

    # Data source line
    if n_live >= CALIBRATION_LIVE_THRESHOLD:
        source_line = f"[green]{n_live} live picks[/green]  [dim](backtest phased out)[/dim]"
    elif n_live > 0:
        source_line = (
            f"[green]{n_live} live picks[/green] + "
            f"[dim]{n_backtest} backtest picks[/dim]"
        )
    else:
        source_line = f"[dim]{n_backtest} backtest picks only (no live data yet)[/dim]"

    # Calibration adjustment line
    if not cal["calibrated"]:
        adj_line = "[dim]Insufficient data -- no adjustment applied[/dim]"
    elif abs(adj) < 1.0:
        adj_line = "[dim]Probabilities uncorrected (model well-calibrated)[/dim]"
    else:
        direction = "down" if adj < 0 else "up"
        colour    = "yellow" if abs(adj) >= 5 else "dim"
        reason    = "over-predicting" if adj < 0 else "under-predicting"
        adj_line  = f"[{colour}]Probabilities adjusted {direction} {abs(adj):.1f}pp[/{colour}]  [dim](model is {reason})[/dim]"

    def _live_bt(live_val, bt_val, fmt, live_colour_fn, bt_colour_fn, label, pending_msg):
        """Helper to format a live / backtest reference line."""
        if live_val is not None:
            lc   = live_colour_fn(live_val)
            line = f"{label}  [{lc}]{fmt(live_val)}[/{lc}]  [dim](live)[/dim]"
        else:
            line = f"{label}  [dim]{pending_msg}[/dim]"
        if bt_val is not None:
            bc   = bt_colour_fn(bt_val)
            line += f"  /  [{bc}]{fmt(bt_val)}[/{bc}]  [dim](backtest)[/dim]"
        return line

    dir_line  = _live_bt(
        dir_acc, bt_dir,
        lambda v: f"{v:.1f}%",
        lambda v: "bright_green" if v >= 56 else "yellow" if v >= 52 else "white",
        lambda v: "bright_green" if v >= 56 else "yellow" if v >= 52 else "white",
        "Directional accuracy: ", f"need {needed} more live pick(s)"
    )
    prof_line = _live_bt(
        prof_rate, None,
        lambda v: f"{v:.1f}%",
        lambda v: "bright_green" if v >= 52 else "yellow",
        lambda v: "white",
        "Profitable trades:    ", f"need {needed} more live pick(s)"
    )
    tgt_line  = _live_bt(
        tgt_rate, None,
        lambda v: f"{v:.1f}%",
        lambda v: "bright_green" if v >= 35 else "yellow",
        lambda v: "white",
        "Target hit rate:      ", "pending live data"
    )
    ret_line  = _live_bt(
        avg_ret, bt_ret,
        lambda v: f"{v:+.3f}%",
        lambda v: "bright_green" if v >= 0 else "red",
        lambda v: "bright_green" if v >= 0 else "red",
        "Avg return per pick:  ", "pending live data"
    )
    console.print(Panel(
        f"  Data:   {source_line}\n\n"
        f"  {adj_line}\n\n"
        f"  {dir_line}\n"
        f"  {prof_line}\n"
        f"  {tgt_line}\n"
        f"  {ret_line}",
        title="[bold]Calibration & Performance[/bold]",
        box=box.ROUNDED,
    ))
    console.print()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_resolved(filepath: str, min_days: int) -> list:
    """Load resolved rows from a CSV file."""
    if not os.path.isfile(filepath):
        return []

    source = "live" if filepath == CSV_FILE else "backtest"
    rows   = []
    now    = datetime.now()

    with open(filepath, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("outcome_hit", "").strip() not in ("YES", "NO"):
                continue
            if min_days > 0:
                try:
                    run_dt = datetime.strptime(row["run_date"], "%Y-%m-%d %H:%M")
                    if (now - run_dt).days < min_days:
                        continue
                except ValueError:
                    continue
            row["_source"] = source
            rows.append(row)

    return rows


def _rewrite_csv(rows: list):
    """Rewrite the live CSV with updated outcome data."""
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
