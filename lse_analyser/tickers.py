"""
tickers.py
----------
FTSE 100/250 ticker universe management.

Resolution priority on each run:
  1. Wikipedia (live)  -> saves to JSON cache -> use fresh list
  2. JSON cache        -> use last successful fetch
  3. Emergency bootstrap -> first-run fallback only
"""

import os
import json
from io import StringIO
from datetime import datetime

import pandas as pd
import requests

from .config import TICKERS_JSON, SECTOR_MAP, EMERGENCY_BOOTSTRAP
from .utils import console


def load_json_tickers():
    """Load tickers from JSON cache. Returns None if unavailable."""
    if not os.path.isfile(TICKERS_JSON):
        return None
    try:
        with open(TICKERS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        tickers = data.get("tickers", {})
        if len(tickers) >= 20:
            return tickers
    except Exception:
        pass
    return None


def save_json_tickers(tickers: dict):
    """Persist a freshly fetched ticker list to the JSON cache file."""
    data = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source":       "Wikipedia",
        "count":        len(tickers),
        "tickers":      tickers,
    }
    with open(TICKERS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def fetch_from_wikipedia():
    """
    Scrape FTSE 100 and FTSE 250 constituents from Wikipedia.
    Returns a dict of ticker->sector on success, or None on failure.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    tickers = {}

    for url in [
        "https://en.wikipedia.org/wiki/FTSE_100_Index",
        "https://en.wikipedia.org/wiki/FTSE_250_Index",
    ]:
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            all_tables = pd.read_html(StringIO(response.text))

            for table in all_tables:
                cols     = [str(c).lower() for c in table.columns]
                col_list = list(table.columns)

                ticker_col = next(
                    (col_list[i] for i, c in enumerate(cols)
                     if any(k in c for k in ["ticker", "symbol", "epic"])), None
                )
                sector_col = next(
                    (col_list[i] for i, c in enumerate(cols)
                     if any(k in c for k in ["sector", "industry", "icb", "benchmark"])), None
                )

                if ticker_col is None:
                    for col in col_list:
                        sample = table[col].dropna().astype(str).head(20)
                        if sample.str.match(r"^[A-Z0-9]{2,5}$").sum() >= 10:
                            ticker_col = col
                            break

                if ticker_col is None or sector_col is None or len(table) < 50:
                    continue

                for _, row in table.iterrows():
                    raw_ticker = str(row[ticker_col]).strip()
                    raw_sector = str(row[sector_col]).strip()
                    if not raw_ticker or raw_ticker.lower() in ("nan", "ticker", "symbol"):
                        continue
                    if len(raw_ticker) > 6 or " " in raw_ticker:
                        continue
                    if not raw_ticker.endswith(".L"):
                        raw_ticker = raw_ticker + ".L"
                    tickers[raw_ticker] = SECTOR_MAP.get(raw_sector, "Other")

                if len(tickers) >= 50:
                    break
        except Exception:
            pass

    return tickers if len(tickers) >= 50 else None


def get_tickers() -> dict:
    """Return the ticker universe for this run, cached after first call."""
    if hasattr(get_tickers, "_cache"):
        return get_tickers._cache

    fresh = fetch_from_wikipedia()
    if fresh:
        save_json_tickers(fresh)
        get_tickers._source = f"Wikipedia (live, {len(fresh)} stocks)"
        get_tickers._cache  = fresh
        return fresh

    cached = load_json_tickers()
    if cached:
        try:
            with open(TICKERS_JSON, "r", encoding="utf-8") as f:
                meta = json.load(f)
            last_updated = meta.get("last_updated", "unknown date")
        except Exception:
            last_updated = "unknown date"
        get_tickers._source = (
            f"JSON cache -- last updated {last_updated} "
            f"({len(cached)} stocks)  [Wikipedia unavailable]"
        )
        get_tickers._cache = cached
        return cached

    console.print(
        "[red]Wikipedia unavailable and no JSON cache found.[/red]\n"
        f"[dim]Using emergency bootstrap ({len(EMERGENCY_BOOTSTRAP)} stocks).[/dim]\n"
    )
    get_tickers._source = (
        f"emergency bootstrap ({len(EMERGENCY_BOOTSTRAP)} stocks) [no internet, no cache]"
    )
    get_tickers._cache = EMERGENCY_BOOTSTRAP
    return EMERGENCY_BOOTSTRAP
