"""
market_log.py
-------------
Tracks FTSE 100 (^FTSE) and FTSE 250 (^FTMC) weekly returns for each
live pick week, using the same Tuesday-open to Monday-close window as
the picks themselves.

Data is stored in lse_market_log.csv — one row per week. Rows are written
in two stages across two separate programme runs:

  Stage 1 — Tuesday open available (any live/preview run after market opens
             on Tuesday of pick week):
             ftse100_open and ftse250_open are saved, close fields left blank.

  Stage 2 — Monday close available (any live/preview run the following Tuesday
             morning, same time outcomes are resolved):
             ftse100_close, ftse250_close, and return_pct fields completed.

Alpha (pick return minus market return) is calculated at display time in
history.py and trade_tracker.py — it is not stored in the CSV.

yfinance tickers:
  ^FTSE  — FTSE 100
  ^FTMC  — FTSE 250
"""

import csv
import os
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from .config import CSV_FILE, MARKET_LOG_FILE, MARKET_LOG_HEADERS
from .utils import console


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _load_market_log() -> dict:
    """Load market log keyed by week_date."""
    if not os.path.isfile(MARKET_LOG_FILE):
        return {}
    with open(MARKET_LOG_FILE, "r", newline="", encoding="utf-8") as f:
        return {row["week_date"]: row for row in csv.DictReader(f)}


def _save_market_log(rows: dict):
    """Write all rows back to the market log CSV."""
    with open(MARKET_LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MARKET_LOG_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows.values())


def _load_live_week_dates() -> list:
    """Return sorted list of run_dates from lse_screener_log.csv."""
    if not os.path.isfile(CSV_FILE):
        return []
    dates = set()
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rd = row.get("run_date", "").strip()
            if rd:
                dates.add(rd)
    return sorted(dates)


# ── Price fetching ────────────────────────────────────────────────────────────

def _fetch_price_on_date(ticker: str, target_date: datetime, mode: str) -> float | None:
    """
    Fetch open or close price for an index on or near a target date.

    mode: "open"  — first available open on or after target_date
          "close" — last available close on or before target_date

    Returns None if data is unavailable.
    """
    try:
        start = target_date - timedelta(days=3)
        end   = target_date + timedelta(days=3)
        df    = yf.download(ticker, start=start, end=end, interval="1d",
                            progress=False, auto_adjust=True)
        if df.empty:
            return None

        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.columns = [c.lower() if isinstance(c, str) else c[0].lower()
                      for c in df.columns]
        target_ts = pd.Timestamp(target_date)

        if mode == "open":
            future = df[df.index >= target_ts]
            if future.empty:
                return None
            return float(future["open"].iloc[0])
        else:  # close
            past = df[df.index <= target_ts]
            if past.empty:
                return None
            return float(past["close"].iloc[-1])

    except Exception:
        return None


def _market_open_now() -> bool:
    """
    Rough check: London market is open Mon-Fri 08:00-16:30 UTC.
    Used to avoid trying to fetch Tuesday open before the market has opened.
    """
    now  = datetime.utcnow()
    if now.weekday() >= 5:   # Saturday or Sunday
        return False
    hour = now.hour + now.minute / 60
    return 8.0 <= hour <= 16.5


# ── Core update function ──────────────────────────────────────────────────────

def update_market_log(run_date_str: str):
    """
    Called on every live or preview run. Performs two passes:

    Pass 1 — Complete any rows missing close prices (previous weeks where
             Monday has now passed).

    Pass 2 — Add/update the open price for the current week's row if Tuesday
             open is now available (market has opened today).

    Args:
        run_date_str: The current run's timestamp e.g. "2026-03-18 08:45"
    """
    week_dates  = _load_live_week_dates()
    if not week_dates:
        return

    market_log  = _load_market_log()
    run_dt      = datetime.strptime(run_date_str[:16], "%Y-%m-%d %H:%M")
    today       = run_dt.date()
    changed     = False

    # ── Pass 1: complete rows missing close data ───────────────────────────────
    for week_date in week_dates:
        row = market_log.get(week_date, {})

        # Skip if already fully resolved
        if row.get("ftse100_close") and row.get("ftse250_close"):
            continue

        # Skip if Monday close hasn't happened yet for this week
        # Monday close = run_date (Tuesday) + 6 days
        pick_dt      = datetime.strptime(week_date[:16], "%Y-%m-%d %H:%M")
        monday_close = (pick_dt + timedelta(days=6)).date()
        if today < monday_close:
            continue

        # Fetch Monday close for both indices
        monday_dt = datetime.combine(monday_close, datetime.min.time())
        f100_close = _fetch_price_on_date("^FTSE", monday_dt, "close")
        f250_close = _fetch_price_on_date("^FTMC", monday_dt, "close")

        if f100_close is None and f250_close is None:
            continue

        if week_date not in market_log:
            market_log[week_date] = {k: "" for k in MARKET_LOG_HEADERS}
            market_log[week_date]["week_date"] = week_date

        if f100_close:
            market_log[week_date]["ftse100_close"] = round(f100_close, 2)
        if f250_close:
            market_log[week_date]["ftse250_close"] = round(f250_close, 2)

        # Calculate returns if we have both open and close
        _recalculate_returns(market_log[week_date])
        market_log[week_date]["resolved_date"] = today.strftime("%Y-%m-%d")
        changed = True

    # ── Pass 2: save open price for current week ───────────────────────────────
    if week_dates:
        current_week = week_dates[-1]
        row          = market_log.get(current_week, {})

        # Only fetch open if we don't have it yet
        if not row.get("ftse100_open") and not row.get("ftse250_open"):
            pick_dt    = datetime.strptime(current_week[:16], "%Y-%m-%d %H:%M")
            tuesday_dt = datetime.combine(pick_dt.date(), datetime.min.time())

            # Only attempt if market has likely opened today
            if today >= pick_dt.date() and _market_open_now():
                f100_open = _fetch_price_on_date("^FTSE", tuesday_dt, "open")
                f250_open = _fetch_price_on_date("^FTMC", tuesday_dt, "open")

                if f100_open or f250_open:
                    if current_week not in market_log:
                        market_log[current_week] = {k: "" for k in MARKET_LOG_HEADERS}
                        market_log[current_week]["week_date"] = current_week
                    if f100_open:
                        market_log[current_week]["ftse100_open"] = round(f100_open, 2)
                    if f250_open:
                        market_log[current_week]["ftse250_open"] = round(f250_open, 2)
                    changed = True

    if changed:
        _save_market_log(market_log)


def _recalculate_returns(row: dict):
    """Calculate return_pct fields for a row if both open and close are present."""
    for prefix in ("ftse100", "ftse250"):
        try:
            o = float(row.get(f"{prefix}_open",  "") or 0)
            c = float(row.get(f"{prefix}_close", "") or 0)
            if o > 0 and c > 0:
                row[f"{prefix}_return_pct"] = round((c - o) / o * 100, 4)
        except (ValueError, TypeError):
            pass


# ── Display helper ────────────────────────────────────────────────────────────

def get_market_return(week_date: str) -> dict | None:
    """
    Return market data for a given week_date, or None if not available.

    Returns dict with keys:
      ftse100_return_pct, ftse250_return_pct  (floats or None)
      complete  — True if both open and close are resolved
    """
    log = _load_market_log()
    row = log.get(week_date)
    if not row:
        return None

    def _safe(key):
        try:
            return float(row[key]) if row.get(key) else None
        except (ValueError, TypeError):
            return None

    f100 = _safe("ftse100_return_pct")
    f250 = _safe("ftse250_return_pct")

    return {
        "ftse100_return_pct": f100,
        "ftse250_return_pct": f250,
        "complete": f100 is not None and f250 is not None,
    }


def format_market_summary(week_date: str, avg_pick_return: float | None = None) -> str:
    """
    Return a formatted string for display in history/trade tracker views.

    Example output:
      Market:  FTSE 100 [red]-1.23%[/red]  FTSE 250 [red]-0.87%[/red]
      Alpha:   [bright_green]+2.35pp[/bright_green] vs FTSE 100

    Returns empty string if no data available.
    """
    data = get_market_return(week_date)
    if not data:
        return "  [dim]Market data: not yet available[/dim]"

    def _fmt(val, label):
        if val is None:
            return f"{label} [dim]pending[/dim]"
        c    = "bright_green" if val > 0 else "red" if val < 0 else "white"
        sign = "+" if val >= 0 else ""
        return f"{label} [{c}]{sign}{val:.2f}%[/{c}]"

    f100_str = _fmt(data["ftse100_return_pct"], "FTSE 100")
    f250_str = _fmt(data["ftse250_return_pct"], "FTSE 250")
    out      = f"  Market:  {f100_str}  |  {f250_str}"

    # Alpha vs FTSE 100 (primary benchmark)
    if avg_pick_return is not None and data["ftse100_return_pct"] is not None:
        alpha = avg_pick_return - data["ftse100_return_pct"]
        ac    = "bright_green" if alpha > 0 else "red" if alpha < 0 else "white"
        sign  = "+" if alpha >= 0 else ""
        out  += f"\n  Alpha:   [{ac}]{sign}{alpha:.2f}pp[/{ac}] vs FTSE 100"

    if not data["complete"]:
        out += "\n  [dim](market data partially resolved — close price pending)[/dim]"

    return out
