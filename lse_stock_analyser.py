"""
LSE Stock Analyser  v3.1
========================
Screens London Stock Exchange (FTSE 100/250) tickers using technical
indicators and momentum signals to identify the 5 stocks most likely to
rise over the next 7 calendar days (approximately one trading week).

New in v3.0:
  ✔  Automatic outcome tracking  — on each run, any picks from 7+ days ago
                                    that are still blank get their outcome
                                    price fetched automatically from Yahoo Finance
  ✔  Self-calibration            — the model reads past outcomes and measures
                                    how accurate its probability estimates have
                                    been. If it's been over-confident, it adjusts
                                    its base probability down. If under-confident,
                                    it adjusts up. The correction is applied live
                                    to the current run's probability outputs.
  ✔  Performance report          — printed at startup showing historical
                                    hit rate, average return, and calibration status

Previous features (v2.0):
  ✔  Sector diversification
  ✔  Volume filter
  ✔  Event filter (earnings / ex-dividend)
  ✔  Kelly-influenced position sizing
  ✔  CSV logging

Dependencies:
    pip install yfinance pandas numpy ta rich

Usage:
    python lse_stock_analyser.py

Notes:
  * Data is from Yahoo Finance (free, ~15-min delayed for LSE).
  * Prices are in PENCE. Divide by 100 for pounds (e.g. 2340p = £23.40).
  * Probability figures are model estimates — NOT financial advice.
  * Always do your own research before trading.
"""

import warnings
warnings.filterwarnings("ignore")

import os
import sys
import csv
import math
import contextlib
import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator, SMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from datetime import datetime, timedelta

console = Console()

# Suppress yfinance's own HTTP error messages which print directly to stderr
@contextlib.contextmanager
def silent():
    """Redirect stderr to /dev/null during yfinance calls to suppress its error spam."""
    with open(os.devnull, "w") as devnull:
        old_stderr = sys.stderr
        sys.stderr  = devnull
        try:
            yield
        finally:
            sys.stderr = old_stderr

# ─────────────────────────────────────────────────────────────────────────────
# TICKER UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────
TICKERS = {
    "SHEL.L": "Energy",       "BP.L":   "Energy",       "MRO.L":  "Energy",
    "RIO.L":  "Mining",       "AAL.L":  "Mining",       "GLEN.L": "Mining",    "ANTO.L": "Mining",
    "HSBA.L": "Banking",      "BARC.L": "Banking",      "LLOY.L": "Banking",
    "NWG.L":  "Banking",      "STAN.L": "Banking",
    "AV.L":   "Insurance",    "MNG.L":  "Insurance",    "PRU.L":  "Insurance",
    "LSEG.L": "FinServices",  "FLTR.L": "FinServices",
    "AZN.L":  "Pharma",       "GSK.L":  "Pharma",
    "ULVR.L": "ConsStaples",  "BATS.L": "ConsStaples",  "IMB.L":  "ConsStaples",
    "DGE.L":  "ConsStaples",  "ABF.L":  "ConsStaples",
    "JD.L":   "ConsDis",      "MKS.L":  "ConsDis",      "BRBY.L": "ConsDis",
    "AUTO.L": "ConsDis",      "SBRY.L": "ConsDis",      "TSCO.L": "ConsDis",
    "BA.L":   "Industrials",  "RR.L":   "Industrials",  "WEIR.L": "Industrials",
    "SMT.L":  "Industrials",  "BNZL.L": "Industrials",  "EXPN.L": "Industrials",
    "REL.L":  "Tech",         "SGE.L":  "Tech",         "RMV.L":  "Tech",
    "WPP.L":  "Media",        "PSON.L": "Media",
    "VOD.L":  "Telecoms",     "BT-A.L": "Telecoms",
    "NG.L":   "Utilities",    "SSE.L":  "Utilities",
    "LAND.L": "RealEstate",   "SGRO.L": "RealEstate",   "BLND.L": "RealEstate",
    "BKG.L":  "RealEstate",   "PSN.L":  "RealEstate",
    "IHG.L":  "Leisure",      "IAG.L":  "Leisure",      "EZJ.L":  "Leisure",
    "CRH.L":  "Materials",    "MNDI.L": "Materials",    "DPLM.L": "Materials",
    "HLMA.L": "CapGoods",     "KGF.L":  "Retail",       "OCDO.L": "Retail",
    "CPG.L":  "ConsDis",      "CNA.L":  "Industrials",  "WTB.L":  "ConsDis",
    "CRDA.L": "Chemicals",    "INF.L":  "Industrials",
}

# ─────────────────────────────────────────────────────────────────────────────
# PARAMETERS  — these are tuned automatically by the calibration engine
# ─────────────────────────────────────────────────────────────────────────────
LOOKBACK_DAYS       = 120
ATR_MULTIPLIER      = 1.5
STOP_MULTIPLIER     = 1.0
LIMIT_BUFFER        = 0.995
TOP_N               = 5
MIN_AVG_VOLUME_GBP  = 500_000
PROB_FLOOR          = 40.0
KELLY_FRACTION      = 0.25
CSV_FILE            = "lse_screener_log.csv"

# Calibration settings
MIN_OUTCOMES_TO_CALIBRATE = 10    # Need at least this many resolved picks before adjusting
CALIBRATION_WINDOW        = 50    # Use the most recent N resolved picks for calibration
MAX_CALIBRATION_SHIFT     = 15.0  # Cap the probability adjustment at ±15 percentage points


# ─────────────────────────────────────────────────────────────────────────────
# CSV SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
CSV_HEADERS = [
    "run_date", "ticker", "sector", "score", "price_p", "target_p",
    "stop_p", "limit_p", "upside_pct", "downside_pct", "prob",
    "reward_risk", "atr", "allocated_gbp", "shares",
    "signals", "outcome_price_p", "outcome_hit", "outcome_return_pct",
    "outcome_notes",
]


# ─────────────────────────────────────────────────────────────────────────────
# ① AUTOMATIC OUTCOME BACK-FILLING
# ─────────────────────────────────────────────────────────────────────────────
def resolve_pending_outcomes():
    """
    On every run, read the CSV and find any rows where:
      - outcome_price_p is blank (not yet resolved)
      - run_date is 7+ calendar days ago (the prediction window has closed)

    For each such row, fetch the closing price from Yahoo Finance and fill in:
      - outcome_price_p  : actual closing price on day 7 (pence)
      - outcome_hit      : "YES" if price reached or exceeded target, else "NO"
      - outcome_return_pct: actual % change from entry to outcome price
      - outcome_notes    : brief auto-generated note

    Returns the number of rows updated.
    """
    if not os.path.isfile(CSV_FILE):
        return 0

    rows = []
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return 0

    now     = datetime.now()
    updated = 0

    with console.status("[bold green]Checking for pending outcomes to resolve…") as status:
        for row in rows:
            # Skip if already resolved
            if row.get("outcome_price_p", "").strip():
                continue

            # Check if 7 days have passed
            try:
                run_dt = datetime.strptime(row["run_date"], "%Y-%m-%d %H:%M")
            except ValueError:
                continue

            if (now - run_dt).days < 7:
                continue  # Prediction window not yet closed

            # Fetch the closing price from 7 days after the run date
            ticker_yf = row["ticker"] + ".L"
            target_date = run_dt + timedelta(days=7)
            status.update(f"[bold green]Resolving outcome for {row['ticker']} "
                          f"(run {row['run_date']})…")

            try:
                # Download a small window around the target date
                start = target_date - timedelta(days=3)
                end   = target_date + timedelta(days=3)
                with silent():
                    df = yf.download(ticker_yf, start=start.strftime("%Y-%m-%d"),
                                     end=end.strftime("%Y-%m-%d"),
                                     interval="1d", progress=False, auto_adjust=True)

                if df is None or df.empty:
                    row["outcome_notes"] = "Could not fetch outcome data"
                    continue

                # Flatten columns if needed
                df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                              for c in df.columns]

                # Use the closing price of the nearest available trading day
                outcome_price = float(df["close"].iloc[-1])
                entry_price   = float(row["price_p"])
                target_price  = float(row["target_p"])

                return_pct = (outcome_price - entry_price) / entry_price * 100
                hit        = "YES" if outcome_price >= target_price else "NO"

                if hit == "YES":
                    note = f"Target reached. +{return_pct:.1f}%"
                elif return_pct >= 0:
                    note = f"Rose but missed target. +{return_pct:.1f}%"
                elif float(row["stop_p"]) > 0 and outcome_price <= float(row["stop_p"]):
                    note = f"Stop-loss triggered. {return_pct:.1f}%"
                else:
                    note = f"Below entry, above stop. {return_pct:.1f}%"

                row["outcome_price_p"]   = round(outcome_price, 2)
                row["outcome_hit"]       = hit
                row["outcome_return_pct"] = round(return_pct, 2)
                row["outcome_notes"]     = note
                updated += 1

            except Exception as e:
                row["outcome_notes"] = f"Fetch error: {e}"

    # Rewrite the CSV with updated rows
    if updated > 0:
        # Ensure all rows have all headers (handle older rows missing new columns)
        for row in rows:
            for h in CSV_HEADERS:
                if h not in row:
                    row[h] = ""

        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        console.print(f"[green]✔ Resolved {updated} pending outcome(s) automatically.[/green]")

    return updated


# ─────────────────────────────────────────────────────────────────────────────
# ② SELF-CALIBRATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def compute_calibration() -> dict:
    """
    Reads the CSV, finds all resolved rows (outcome_hit is "YES" or "NO"),
    and computes:

      - actual_hit_rate   : % of predictions where target was actually reached
      - avg_predicted_prob: average probability the model assigned to those picks
      - calibration_bias  : how much the model over/under-estimates (+ = over-confident)
      - prob_adjustment   : value to subtract from future probabilities to correct bias
      - avg_return_pct    : average actual return across all resolved picks
      - n_resolved        : number of resolved picks used

    If there aren't enough data points yet, returns a neutral calibration.
    """
    neutral = {
        "n_resolved":        0,
        "actual_hit_rate":   None,
        "avg_predicted_prob": None,
        "calibration_bias":  0.0,
        "prob_adjustment":   0.0,
        "avg_return_pct":    None,
        "calibrated":        False,
        "status":            f"Not yet calibrated (need {MIN_OUTCOMES_TO_CALIBRATE} resolved picks)",
    }

    if not os.path.isfile(CSV_FILE):
        return neutral

    resolved = []
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("outcome_hit", "").strip() in ("YES", "NO"):
                resolved.append(row)

    if len(resolved) < MIN_OUTCOMES_TO_CALIBRATE:
        neutral["n_resolved"] = len(resolved)
        neutral["status"] = (f"Not yet calibrated — {len(resolved)} resolved pick(s) so far, "
                             f"need {MIN_OUTCOMES_TO_CALIBRATE}")
        return neutral

    # Use only the most recent CALIBRATION_WINDOW picks
    resolved = resolved[-CALIBRATION_WINDOW:]

    hits            = [r for r in resolved if r["outcome_hit"] == "YES"]
    actual_hit_rate = len(hits) / len(resolved) * 100

    predicted_probs = []
    returns         = []
    for r in resolved:
        try:
            predicted_probs.append(float(r["prob"]))
        except (ValueError, KeyError):
            pass
        try:
            returns.append(float(r["outcome_return_pct"]))
        except (ValueError, KeyError):
            pass

    avg_predicted = sum(predicted_probs) / len(predicted_probs) if predicted_probs else 50.0
    avg_return    = sum(returns) / len(returns) if returns else 0.0

    # Bias: positive means the model predicted higher prob than reality
    bias = avg_predicted - actual_hit_rate

    # Clamp the correction to avoid wild swings
    adjustment = max(-MAX_CALIBRATION_SHIFT, min(MAX_CALIBRATION_SHIFT, bias))

    if abs(bias) < 3.0:
        status = f"Well-calibrated (bias: {bias:+.1f}pp across {len(resolved)} picks)"
    elif bias > 0:
        status = (f"Over-confident by {bias:.1f}pp — reducing probabilities by "
                  f"{adjustment:.1f}pp to compensate")
    else:
        status = (f"Under-confident by {abs(bias):.1f}pp — increasing probabilities by "
                  f"{abs(adjustment):.1f}pp to compensate")

    return {
        "n_resolved":         len(resolved),
        "actual_hit_rate":    round(actual_hit_rate, 1),
        "avg_predicted_prob": round(avg_predicted, 1),
        "calibration_bias":   round(bias, 2),
        "prob_adjustment":    round(adjustment, 2),
        "avg_return_pct":     round(avg_return, 2),
        "calibrated":         True,
        "status":             status,
    }


def print_performance_report(cal: dict):
    """Print a historical performance summary panel at startup."""
    if cal["n_resolved"] == 0:
        console.print(
            "[dim]📊 No resolved outcomes yet — performance report will appear "
            "after your first picks pass the 7-day window.[/dim]\n"
        )
        return

    lines = [f"[bold]Historical Performance Report[/bold]  "
             f"[dim]({cal['n_resolved']} resolved pick(s))[/dim]\n"]

    if cal["actual_hit_rate"] is not None:
        hit_col = ("bright_green" if cal["actual_hit_rate"] >= 50
                   else "yellow" if cal["actual_hit_rate"] >= 35 else "red")
        lines.append(f"  Hit rate (target reached):  "
                     f"[{hit_col}]{cal['actual_hit_rate']:.1f}%[/{hit_col}]  "
                     f"[dim](model predicted avg {cal['avg_predicted_prob']:.1f}%)[/dim]")

    if cal["avg_return_pct"] is not None:
        ret_col = "bright_green" if cal["avg_return_pct"] >= 0 else "red"
        lines.append(f"  Average actual return:      "
                     f"[{ret_col}]{cal['avg_return_pct']:+.2f}%[/{ret_col}]")

    lines.append(f"\n  Calibration:  [italic]{cal['status']}[/italic]")

    if cal["calibrated"] and abs(cal["prob_adjustment"]) >= 1.0:
        direction = "reduced" if cal["prob_adjustment"] > 0 else "increased"
        lines.append(f"  [dim]→ Today's probability outputs have been {direction} by "
                     f"{abs(cal['prob_adjustment']):.1f}pp to correct for historical bias.[/dim]")

    console.print(Panel("\n".join(lines), title="📊 Track Record", box=box.ROUNDED))
    console.print()


# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────────────────────
def fetch_data(ticker: str) -> pd.DataFrame | None:
    try:
        with silent():
            df = yf.download(ticker, period=f"{LOOKBACK_DAYS}d", interval="1d",
                             progress=False, auto_adjust=True)
        if df is None or len(df) < 30:
            return None
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                      for c in df.columns]
        df.dropna(inplace=True)
        return df
    except Exception:
        return None


def fetch_ticker_info(ticker: str) -> dict:
    try:
        t   = yf.Ticker(ticker)
        cal = t.calendar
        earnings_date = None
        exdiv_date    = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                earnings_date = pd.to_datetime(ed[0] if isinstance(ed, list) else ed)
            dd = cal.get("Ex-Dividend Date")
            if dd:
                exdiv_date = pd.to_datetime(dd)
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.index:
                earnings_date = pd.to_datetime(cal.loc["Earnings Date"].iloc[0])
            if "Ex-Dividend Date" in cal.index:
                exdiv_date = pd.to_datetime(cal.loc["Ex-Dividend Date"].iloc[0])
        return {"earnings_date": earnings_date, "exdiv_date": exdiv_date}
    except Exception:
        return {"earnings_date": None, "exdiv_date": None}


# ─────────────────────────────────────────────────────────────────────────────
# FILTERS
# ─────────────────────────────────────────────────────────────────────────────
def has_event_in_window(ticker: str, days: int = 7) -> tuple[bool, str]:
    info   = fetch_ticker_info(ticker)
    now    = datetime.now()
    cutoff = now + timedelta(days=days)
    for key, label in [("earnings_date", "earnings"), ("exdiv_date", "ex-dividend")]:
        date = info.get(key)
        if date is not None:
            try:
                dt = date.to_pydatetime().replace(tzinfo=None)
                if now <= dt <= cutoff:
                    return True, f"{label} on {dt.strftime('%d %b')}"
            except Exception:
                pass
    return False, ""


def passes_volume_filter(df: pd.DataFrame) -> bool:
    recent        = df.tail(20)
    avg_value_gbp = (recent["close"] * recent["volume"] / 100).mean()
    return avg_value_gbp >= MIN_AVG_VOLUME_GBP


# ─────────────────────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────────────────────
def score_ticker(ticker: str, sector: str, prob_adjustment: float = 0.0) -> dict | None:
    """
    Score a ticker and compute trade levels.
    prob_adjustment: subtracted from the raw probability to correct for
    historical over/under-confidence.
    """
    df = fetch_data(ticker)
    if df is None or len(df) < 30:
        return None
    if not passes_volume_filter(df):
        return None

    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    try:
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

        c     = float(close.iloc[-1])
        r     = float(rsi.iloc[-1])
        mh    = float(macd_hist.iloc[-1])
        mh_p  = float(macd_hist.iloc[-2])
        bp    = float(bb_pct.iloc[-1])
        e20   = float(ema20.iloc[-1])
        e50   = float(ema50.iloc[-1])
        s200  = float(sma200.iloc[-1])
        atr_v = float(atr.iloc[-1])
        sk    = float(stoch_k.iloc[-1])
        sd    = float(stoch_d.iloc[-1])

        obv_slope = ((float(obv.iloc[-1]) - float(obv.iloc[-10]))
                     / (abs(float(obv.iloc[-10])) + 1))
        mom5 = (c - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

        score   = 0
        signals = []

        if 40 <= r <= 65:
            score += 20;  signals.append(f"RSI {r:.0f} (bullish range)")
        elif r < 35:
            score += 10;  signals.append(f"RSI {r:.0f} (oversold — potential reversal)")

        if mh > 0 and mh_p < 0:
            score += 25;  signals.append("MACD histogram bullish crossover")
        elif mh > mh_p and mh > 0:
            score += 15;  signals.append("MACD histogram rising")

        if 0.2 <= bp <= 0.5:
            score += 15;  signals.append(f"BB %B {bp:.2f} (below mid, room to rise)")
        elif bp < 0.2:
            score += 8;   signals.append(f"BB %B {bp:.2f} (near lower band, bounce candidate)")

        if e20 > e50 > s200:
            score += 20;  signals.append("EMA20 > EMA50 > SMA200 (fully aligned bullish)")
        elif e20 > e50:
            score += 10;  signals.append("EMA20 > EMA50 (short-term bullish)")

        if sk > sd and 20 < sk < 80:
            score += 10;  signals.append(f"Stochastic bullish ({sk:.0f}/{sd:.0f})")

        if obv_slope > 0.01:
            score += 10;  signals.append("OBV rising (institutional accumulation)")

        if 0.5 <= mom5 <= 4.0:
            score += 10;  signals.append(f"5-day momentum +{mom5:.1f}%")
        elif -1.0 <= mom5 < 0:
            score += 5;   signals.append(f"Slight dip {mom5:.1f}% — potential entry point")

        target       = round(c + ATR_MULTIPLIER * atr_v, 2)
        stop         = round(c - STOP_MULTIPLIER * atr_v, 2)
        limit        = round(target * LIMIT_BUFFER, 2)
        upside_pct   = (target - c) / c * 100
        downside_pct = (c - stop) / c * 100

        # Raw probability, then apply calibration correction
        raw_prob = 35.0 + (score / 110) * 40
        prob     = round(min(78.0, max(20.0, raw_prob - prob_adjustment)), 1)

        return {
            "ticker":       ticker.replace(".L", ""),
            "sector":       sector,
            "score":        score,
            "price":        c,
            "target":       target,
            "stop":         stop,
            "limit":        limit,
            "upside_pct":   upside_pct,
            "downside_pct": downside_pct,
            "prob":         prob,
            "signals":      signals,
            "atr":          round(atr_v, 4),
            "reward_risk":  round(upside_pct / downside_pct, 2) if downside_pct > 0 else 0,
        }

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SECTOR DIVERSIFICATION
# ─────────────────────────────────────────────────────────────────────────────
def diversify(results: list[dict], n: int = TOP_N) -> list[dict]:
    seen_sectors = set()
    picks        = []
    for r in results:
        if r["sector"] not in seen_sectors:
            picks.append(r)
            seen_sectors.add(r["sector"])
        if len(picks) == n:
            return picks
    for r in results:
        if r not in picks:
            picks.append(r)
        if len(picks) == n:
            return picks
    return picks


# ─────────────────────────────────────────────────────────────────────────────
# POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────
def calculate_allocations(picks: list[dict], total_capital: float):
    kelly_weights = []
    for r in picks:
        prob = r["prob"] / 100.0
        rr   = r["reward_risk"]
        if rr > 0 and prob > (PROB_FLOOR / 100):
            raw_kelly = prob - (1 - prob) / rr
            kelly     = max(0.0, raw_kelly * KELLY_FRACTION)
        else:
            kelly = 0.0
        kelly_weights.append(kelly)

    total_kelly = sum(kelly_weights)

    for i, r in enumerate(picks):
        if total_kelly > 0 and kelly_weights[i] > 0:
            alloc_pct   = kelly_weights[i] / total_kelly * 100
            alloc_gbp   = total_capital * (kelly_weights[i] / total_kelly)
            price_gbp   = r["price"] / 100
            shares      = int(alloc_gbp / price_gbp) if price_gbp > 0 else 0
            actual_cost = round(shares * price_gbp, 2)
        else:
            alloc_pct   = 0.0
            alloc_gbp   = 0.0
            shares      = 0
            actual_cost = 0.0

        picks[i]["kelly_weight"]   = round(kelly_weights[i], 4)
        picks[i]["allocation_pct"] = round(alloc_pct, 1)
        picks[i]["allocated_gbp"]  = round(alloc_gbp, 2)
        picks[i]["shares"]         = shares
        picks[i]["actual_cost"]    = actual_cost

    deployed = sum(p["actual_cost"] for p in picks)
    reserve  = round(total_capital - deployed, 2)
    return picks, deployed, reserve


# ─────────────────────────────────────────────────────────────────────────────
# CSV  — save new picks
# ─────────────────────────────────────────────────────────────────────────────
def save_to_csv(picks: list[dict], run_date: str):
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        for r in picks:
            writer.writerow({
                "run_date":          run_date,
                "ticker":            r["ticker"],
                "sector":            r["sector"],
                "score":             r["score"],
                "price_p":           r["price"],
                "target_p":          r["target"],
                "stop_p":            r["stop"],
                "limit_p":           r["limit"],
                "upside_pct":        round(r["upside_pct"], 2),
                "downside_pct":      round(r["downside_pct"], 2),
                "prob":              r["prob"],
                "reward_risk":       r["reward_risk"],
                "atr":               r["atr"],
                "allocated_gbp":     r.get("allocated_gbp", ""),
                "shares":            r.get("shares", ""),
                "signals":           " | ".join(r["signals"]),
                "outcome_price_p":   "",
                "outcome_hit":       "",
                "outcome_return_pct": "",
                "outcome_notes":     "",
            })
    console.print(f"[dim]📄 Picks saved to [bold]{CSV_FILE}[/bold][/dim]\n")


# ─────────────────────────────────────────────────────────────────────────────
# CAPITAL INPUT
# ─────────────────────────────────────────────────────────────────────────────
def ask_for_capital() -> float:
    console.print(
        "\n[bold cyan]Position Sizing[/bold cyan]\n"
        "[dim]Enter the total capital (£) you are willing to invest across all picks.\n"
        "Low-probability picks receive less or no allocation; surplus is kept as reserve.[/dim]\n"
    )
    while True:
        try:
            raw = input("  Total capital to invest (£): ").strip().replace("£","").replace(",","")
            val = float(raw)
            if val <= 0:
                raise ValueError
            return val
        except ValueError:
            console.print("  [red]Please enter a valid positive number (e.g. 2000)[/red]")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def ask_for_mode() -> str:
    """
    Ask which mode to run in.
    Returns "live", "preview", or "history".
    """
    console.print(
        "\n[bold cyan]Run mode[/bold cyan]\n"
        "[dim]  [L] Live mode    — results are saved to the CSV log (use once a week)\n"
        "  [P] Preview mode — results are shown but nothing is saved\n"
        "  [H] History      — view a previous week\'s predictions and outcomes[/dim]\n"
    )
    while True:
        raw = input("  Choose mode (L / P / H): ").strip().upper()
        if raw in ("L", "LIVE"):
            return "live"
        elif raw in ("P", "PREVIEW"):
            return "preview"
        elif raw in ("H", "HISTORY"):
            return "history"
        else:
            console.print("  [red]Please enter L, P, or H[/red]")


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY VIEWER
# ─────────────────────────────────────────────────────────────────────────────
def load_history() -> dict:
    """
    Read the CSV and group rows by run_date.
    Returns an ordered dict: {run_date_str: [row, ...]}
    """
    if not os.path.isfile(CSV_FILE):
        return {}

    runs = {}
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rd = row.get("run_date", "").strip()
            if rd:
                runs.setdefault(rd, []).append(row)
    return runs


def show_history():
    """
    Let the user pick a past run from a numbered list, then display
    that run\'s picks in a table matching the original output format,
    with three extra outcome columns appended on the right.
    """
    runs = load_history()

    if not runs:
        console.print(
            Panel(
                "[yellow]No history found.[/yellow]\n"
                "[dim]The CSV log is empty or does not exist yet.\n"
                "Run the script in Live mode first to start building a history.[/dim]",
                title="History",
                box=box.ROUNDED,
            )
        )
        return

    # ── Print numbered list of available runs ────────────────────────────────
    run_dates = list(runs.keys())

    console.print("\n[bold cyan]Previous runs[/bold cyan]\n")
    for i, rd in enumerate(run_dates, 1):
        rows      = runs[rd]
        n_picks   = len(rows)
        resolved  = [r for r in rows if r.get("outcome_hit", "").strip() in ("YES", "NO")]
        n_resolved = len(resolved)
        hits       = sum(1 for r in resolved if r.get("outcome_hit") == "YES")

        if n_resolved == 0:
            status_str = "[dim]all pending[/dim]"
        elif n_resolved < n_picks:
            status_str = f"[dim]{n_resolved} resolved[/dim]"
        else:
            hit_col = "bright_green" if hits >= n_picks / 2 else "yellow"
            status_str = f"[{hit_col}]{hits}/{n_picks} targets hit[/{hit_col}]"

        console.print(f"  [bold white]{i:>2}.[/bold white]  "
                      f"[yellow]{rd}[/yellow]  —  "
                      f"{n_picks} picks  —  {status_str}")

    console.print()

    # ── Ask which run to display ─────────────────────────────────────────────
    while True:
        try:
            raw = input(f"  Enter number (1–{len(run_dates)}): ").strip()
            choice = int(raw)
            if 1 <= choice <= len(run_dates):
                break
            raise ValueError
        except ValueError:
            console.print(f"  [red]Please enter a number between 1 and {len(run_dates)}[/red]")

    selected_date = run_dates[choice - 1]
    rows          = runs[selected_date]

    # ── Build the history table ───────────────────────────────────────────────
    console.print()
    table = Table(
        title=f"[bold]Predictions for {selected_date}[/bold]",
        box=box.ROUNDED,
        show_lines=True,
        style="cyan",
        header_style="bold magenta",
    )

    # Original prediction columns
    table.add_column("Ticker",        justify="center", style="bold yellow",  no_wrap=True)
    table.add_column("Sector",        justify="left",   style="dim white",    no_wrap=True)
    table.add_column("Price (p)",     justify="right",  style="white")
    table.add_column("Target (p)",    justify="right",  style="green")
    table.add_column("Pred. Upside",  justify="right",  style="bright_green")
    table.add_column("Prob.",         justify="right",  style="bright_cyan")
    table.add_column("Stop (p)",      justify="right",  style="red")
    table.add_column("Limit (p)",     justify="right",  style="bright_yellow")
    table.add_column("R:R",           justify="center", style="magenta")
    table.add_column("Score",         justify="center", style="dim magenta")
    # Outcome columns
    table.add_column("Actual (p)",    justify="right",  style="white")
    table.add_column("Actual Return", justify="right",  style="white")
    table.add_column("Hit?",          justify="center", style="bold white")

    for r in rows:
        # ── Outcome columns ──────────────────────────────────────────────────
        hit       = r.get("outcome_hit", "").strip()
        out_price = r.get("outcome_price_p", "").strip()
        out_ret   = r.get("outcome_return_pct", "").strip()

        if hit == "YES":
            hit_str     = "[bright_green]YES ✔[/bright_green]"
            ret_str     = f"[bright_green]+{float(out_ret):.1f}%[/bright_green]" if out_ret else "—"
            price_str   = f"{float(out_price):,.2f}" if out_price else "—"
        elif hit == "NO":
            ret_val     = float(out_ret) if out_ret else 0
            ret_col     = "red" if ret_val < 0 else "yellow"
            sign        = "+" if ret_val >= 0 else ""
            hit_str     = "[red]NO ✘[/red]"
            ret_str     = f"[{ret_col}]{sign}{ret_val:.1f}%[/{ret_col}]" if out_ret else "—"
            price_str   = f"{float(out_price):,.2f}" if out_price else "—"
        else:
            hit_str   = "[dim]Pending[/dim]"
            ret_str   = "[dim]—[/dim]"
            price_str = "[dim]—[/dim]"

        # ── Probability colour (same logic as live output) ───────────────────
        try:
            prob_val = float(r.get("prob", 0))
            prob_col = ("bright_green" if prob_val >= 60
                        else "yellow" if prob_val >= 45 else "red")
            prob_str = f"[{prob_col}]{prob_val:.0f}%[/{prob_col}]"
        except ValueError:
            prob_str = r.get("prob", "—")

        table.add_row(
            r.get("ticker", ""),
            r.get("sector", ""),
            r.get("price_p", ""),
            r.get("target_p", ""),
            f"+{float(r["upside_pct"]):.1f}%" if r.get("upside_pct") else "—",
            prob_str,
            r.get("stop_p", ""),
            r.get("limit_p", ""),
            r.get("reward_risk", ""),
            r.get("score", ""),
            price_str,
            ret_str,
            hit_str,
        )

    console.print(table)

    # ── Summary line ─────────────────────────────────────────────────────────
    resolved = [r for r in rows if r.get("outcome_hit", "").strip() in ("YES", "NO")]
    if resolved:
        hits    = sum(1 for r in resolved if r["outcome_hit"] == "YES")
        returns = [float(r["outcome_return_pct"]) for r in resolved
                   if r.get("outcome_return_pct", "").strip()]
        avg_ret = sum(returns) / len(returns) if returns else 0
        ret_col = "bright_green" if avg_ret >= 0 else "red"
        sign    = "+" if avg_ret >= 0 else ""
        console.print(
            f"\n  [bold]Run summary:[/bold]  "
            f"Targets hit: [bright_green]{hits}/{len(resolved)}[/bright_green]  |  "
            f"Avg return: [{ret_col}]{sign}{avg_ret:.2f}%[/{ret_col}]\n"
        )
    else:
        console.print("\n  [dim]No outcomes resolved yet for this run.[/dim]\n")


def main():
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Mode selection — must be the very first prompt ────────────────────────
    mode = ask_for_mode()

    # History mode: show past runs and exit — no screening needed
    if mode == "history":
        show_history()
        return

    live_mode  = (mode == "live")
    mode_label = "[green]LIVE[/green]" if live_mode else "[yellow]PREVIEW[/yellow]"

    console.print(Panel(
        f"[bold cyan]LSE Stock Screener  v3.1[/bold cyan]\n"
        f"[dim]Run at {run_date}  •  Screening {len(TICKERS)} tickers[/dim]  •  "
        f"Mode: {mode_label}\n\n"
        "[dim]Features:  Volume ✔   Sectors ✔   Events ✔   "
        "Auto-outcomes ✔   Self-calibration ✔[/dim]",
        box=box.ROUNDED,
    ))

    # ── Step 1: resolve pending outcomes (Live mode only) ────────────────────
    if live_mode:
        resolve_pending_outcomes()
    else:
        console.print("[dim]⏭  Preview mode — outcome resolution skipped.[/dim]\n")

    # ── Step 2: compute calibration from historical outcomes ─────────────────
    cal = compute_calibration()
    print_performance_report(cal)

    # ── Step 3: screen tickers, applying calibration correction to probs ─────
    all_results    = []
    skipped_events = []

    with console.status("[bold green]Fetching data & scoring tickers…") as status:
        for i, (ticker, sector) in enumerate(TICKERS.items()):
            status.update(f"[bold green]Analysing {ticker} ({i+1}/{len(TICKERS)})…")

            has_event, event_reason = has_event_in_window(ticker)
            if has_event:
                skipped_events.append(f"{ticker.replace('.L','')} ({event_reason})")
                continue

            r = score_ticker(ticker, sector, prob_adjustment=cal["prob_adjustment"])
            if r:
                all_results.append(r)

    if not all_results:
        console.print("[red]No data retrieved. Check your internet connection.[/red]")
        return

    all_results.sort(key=lambda x: x["score"], reverse=True)
    top = diversify(all_results, TOP_N)

    # ── Step 4: capital input and position sizing ─────────────────────────────
    total_capital = ask_for_capital()
    top, deployed, reserve = calculate_allocations(top, total_capital)

    # ── Step 5: main results table ────────────────────────────────────────────
    cal_note = ""
    if cal["calibrated"] and abs(cal["prob_adjustment"]) >= 1.0:
        direction = "adjusted down" if cal["prob_adjustment"] > 0 else "adjusted up"
        cal_note  = f"  [dim italic](probabilities {direction} {abs(cal['prob_adjustment']):.1f}pp by calibration)[/dim italic]"

    table = Table(
        title=f"\n[bold]Top {TOP_N} LSE Stocks — 7-Day Outlook[/bold]{cal_note}",
        box=box.ROUNDED,
        show_lines=True,
        style="cyan",
        header_style="bold magenta",
    )
    table.add_column("Rank",       justify="center", style="bold white",   no_wrap=True)
    table.add_column("Ticker",     justify="center", style="bold yellow",  no_wrap=True)
    table.add_column("Sector",     justify="left",   style="dim white",    no_wrap=True)
    table.add_column("Price (p)",  justify="right",  style="white")
    table.add_column("Target (p)", justify="right",  style="green")
    table.add_column("Upside",     justify="right",  style="bright_green")
    table.add_column("Prob.",      justify="right",  style="bright_cyan")
    table.add_column("Stop (p)",   justify="right",  style="red")
    table.add_column("Limit (p)",  justify="right",  style="bright_yellow")
    table.add_column("R:R",        justify="center", style="magenta")
    table.add_column("Score",      justify="center", style="dim magenta")

    for i, r in enumerate(top, 1):
        prob_col = ("bright_green" if r["prob"] >= 60
                    else "yellow" if r["prob"] >= 45 else "red")
        table.add_row(
            str(i), r["ticker"], r["sector"],
            f"{r['price']:,.2f}", f"{r['target']:,.2f}",
            f"+{r['upside_pct']:.1f}%",
            f"[{prob_col}]{r['prob']:.0f}%[/{prob_col}]",
            f"{r['stop']:,.2f}", f"{r['limit']:,.2f}",
            f"{r['reward_risk']:.2f}", str(r["score"]),
        )

    console.print(table)

    # ── Step 6: position sizing table ─────────────────────────────────────────
    size_table = Table(
        title="[bold]Suggested Position Sizing[/bold]",
        box=box.SIMPLE_HEAVY,
        style="cyan",
        header_style="bold magenta",
    )
    size_table.add_column("Ticker",        justify="center", style="bold yellow")
    size_table.add_column("Probability",   justify="right",  style="bright_cyan")
    size_table.add_column("Allocation %",  justify="right",  style="green")
    size_table.add_column("Invest (£)",    justify="right",  style="bright_green")
    size_table.add_column("Price (£)",     justify="right",  style="white")
    size_table.add_column("~Shares",       justify="right",  style="dim white")
    size_table.add_column("Note",          style="dim white")

    for r in top:
        price_gbp = r["price"] / 100  # convert pence to £
        approx_shares = r["shares"]   # already calculated, may be 0

        if r["allocated_gbp"] == 0:
            # Signal quality too low to warrant any capital
            note         = "⚠ Below confidence threshold — skip"
            shares_str   = "—"
            invest_str   = "£0.00"
        else:
            invest_str = f"£{r['allocated_gbp']:,.2f}"

            # Signal quality label — independent of share price
            if r["prob"] >= 60:
                signal_note = "★ Strong signal — favoured"
            elif r["prob"] >= 50:
                signal_note = "Moderate signal"
            else:
                signal_note = "Weak signal — small stake only"

            if approx_shares == 0:
                # Allocation is less than one full share — note it informatively
                # but don't imply the pick is poor (fractional shares are fine)
                shares_str = f"<1  (1 share = £{price_gbp:,.2f})"
                note       = f"{signal_note}  ·  fractional share"
            else:
                shares_str = f"~{approx_shares}"
                note       = signal_note

        size_table.add_row(
            r["ticker"],
            f"{r['prob']:.0f}%",
            f"{r['allocation_pct']:.1f}%",
            invest_str,
            f"£{price_gbp:,.2f}",
            shares_str,
            note,
        )

    console.print(size_table)

    total_suggested = sum(r["allocated_gbp"] for r in top)
    reserve_shown   = round(total_capital - total_suggested, 2)
    console.print(
        f"\n  [bold]Capital summary:[/bold]  "
        f"Total entered: [cyan]£{total_capital:,.2f}[/cyan]  |  "
        f"Suggested to deploy: [green]£{total_suggested:,.2f}[/green]  |  "
        f"Keep in reserve: [yellow]£{reserve_shown:,.2f}[/yellow]\n"
    )

    # ── Step 7: detailed signal breakdown ─────────────────────────────────────
    console.print("[bold]Detailed signal breakdown:[/bold]\n")
    for i, r in enumerate(top, 1):
        console.print(f"  [bold yellow]{i}. {r['ticker']}[/bold yellow]  "
                      f"[dim]({r['sector']})[/dim]")
        for sig in r["signals"]:
            console.print(f"     [dim]•[/dim] {sig}")
        console.print(
            f"     [dim]ATR(14) = {r['atr']:.2f}p  |  "
            f"Stop is {r['downside_pct']:.1f}% below entry  |  "
            f"Reward:Risk = {r['reward_risk']:.2f}[/dim]\n"
        )

    if skipped_events:
        console.print(
            f"[dim]⏭  Skipped (event within 7 days): "
            f"{', '.join(skipped_events)}[/dim]\n"
        )

    # ── Step 8: save new picks to CSV (Live mode only) ──────────────────────────
    if live_mode:
        save_to_csv(top, run_date)
    else:
        console.print(
            "[yellow]⏭  Preview mode — results not saved to CSV.[/yellow]\n"
            "[dim]Run in Live mode (L) when you want this week's picks logged.[/dim]\n"
        )

    # ── Disclaimer ────────────────────────────────────────────────────────────
    console.print(Panel(
        "[dim]"
        "[bold]How the self-calibration works:[/bold]\n"
        "  Each time you run the script, it first looks back at picks from 7+ days ago,\n"
        "  fetches their actual closing prices, and records whether the target was hit.\n"
        "  Once 10+ outcomes exist, it compares the model's predicted probabilities\n"
        "  against the real hit rate and adjusts today's outputs accordingly.\n"
        "  Example: if the model has been predicting 60% but only hitting 45%, it\n"
        "  will automatically reduce today's probabilities by ~15pp to correct for this.\n\n"
        "[bold]Column guide:[/bold]\n"
        "  Price / Target / Stop / Limit  All in PENCE (÷ 100 = £)\n"
        "  R:R        Reward:Risk ratio — how much you gain vs. risk per £ (aim ≥ 1.5)\n"
        "  Upside     % gain if the target price is reached\n"
        "  Score      Internal signal quality score out of ~110\n"
        "  Invest (£) Suggested amount to put into this stock — the primary sizing guide\n"
        "  ~Shares    Approximate number of whole shares for that amount (advisory only)\n"
        "             If shown as <1, the allocation is smaller than one share's price;\n"
        "             you may wish to round up to 1 share or skip this pick.\n"
        "  Prob.      Calibration-adjusted probability estimate\n\n"
        "[bold red]⚠ Disclaimer:[/bold red] Quantitative screening tool only — NOT financial\n"
        "advice. All probabilities are model estimates. Past patterns do not\n"
        "guarantee future results. Consult a regulated adviser before trading.[/dim]",
        title="[bold]How It Works & Disclaimer[/bold]",
        box=box.ROUNDED,
    ))


if __name__ == "__main__":
    main()
