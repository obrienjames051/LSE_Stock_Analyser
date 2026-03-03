"""
tickers.py
----------
FTSE 100/250 ticker universe management.

Each index is cached independently in ftse_tickers.json so that a failed
fetch for one index never overwrites the cached data for the other.

JSON structure:
{
  "ftse100": { "last_updated": "2026-03-03 20:15", "tickers": { "AZN.L": "Pharma", ... } },
  "ftse250": { "last_updated": "2026-03-03 20:15", "tickers": { "3IN.L": "FinServices", ... } }
}

Resolution priority for each index independently:
  1. Wikipedia (live)  -> updates that index's section in JSON
  2. JSON cache        -> uses last successful fetch for that index
  3. Emergency bootstrap -> only if both above fail for FTSE 100
"""

import os
import json
from io import StringIO
from datetime import datetime

import pandas as pd
import requests

from .config import TICKERS_JSON, SECTOR_MAP, EMERGENCY_BOOTSTRAP
from .utils import console

FTSE_URLS = {
    "ftse100": "https://en.wikipedia.org/wiki/FTSE_100_Index",
    "ftse250": "https://en.wikipedia.org/wiki/FTSE_250_Index",
}


def _load_json() -> dict:
    """Load the full JSON cache file. Returns empty dict if unavailable."""
    if not os.path.isfile(TICKERS_JSON):
        return {}
    try:
        with open(TICKERS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(data: dict):
    """Write the full JSON cache file."""
    with open(TICKERS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _fetch_index_from_wikipedia(url: str) -> dict | None:
    """
    Fetch one index's constituent table from Wikipedia.
    Returns a dict of {ticker: sector} on success, or None on failure.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
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

            tickers = {}
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
                return tickers

    except Exception:
        pass

    return None


def get_tickers() -> dict:
    """
    Return the full ticker universe for this run, cached after first call.

    Each index is fetched and cached independently. A failure to fetch one
    index falls back to that index's cached data without touching the other.
    """
    if hasattr(get_tickers, "_cache"):
        return get_tickers._cache

    cache     = _load_json()
    tickers   = {}
    sources   = {}
    now_str   = datetime.now().strftime("%Y-%m-%d %H:%M")
    cache_updated = False

    for key, url in FTSE_URLS.items():
        fresh = _fetch_index_from_wikipedia(url)

        if fresh:
            # Update just this index's section in the cache
            cache[key] = {"last_updated": now_str, "tickers": fresh}
            cache_updated = True
            tickers.update(fresh)
            sources[key] = f"live ({len(fresh)})"
        else:
            # Fall back to cached data for this index
            cached_section = cache.get(key, {})
            cached_tickers = cached_section.get("tickers", {})
            if cached_tickers:
                tickers.update(cached_tickers)
                last_updated = cached_section.get("last_updated", "unknown date")
                sources[key] = f"cache from {last_updated} ({len(cached_tickers)})"
            else:
                sources[key] = "unavailable"

    # Save the updated cache (only writes if at least one index was refreshed)
    if cache_updated:
        _save_json(cache)

    # Build a human-readable source description
    f100_src = sources.get("ftse100", "unavailable")
    f250_src = sources.get("ftse250", "unavailable")

    if "live" in f100_src and "live" in f250_src:
        source_str    = f"Wikipedia (live, {len(tickers)} stocks)"
        source_colour = "green"
    elif "unavailable" not in f100_src and "unavailable" not in f250_src:
        source_str    = f"FTSE 100: {f100_src}  |  FTSE 250: {f250_src}  ({len(tickers)} total)"
        source_colour = "yellow"
    else:
        source_str    = f"FTSE 100: {f100_src}  |  FTSE 250: {f250_src}  ({len(tickers)} total)"
        source_colour = "yellow" if tickers else "red"

    # Fall back to emergency bootstrap if we have nothing at all
    if not tickers:
        console.print(
            "[red]Wikipedia unavailable and no JSON cache found.[/red]\n"
            f"[dim]Using emergency bootstrap ({len(EMERGENCY_BOOTSTRAP)} stocks).[/dim]\n"
        )
        tickers       = EMERGENCY_BOOTSTRAP
        source_str    = f"emergency bootstrap ({len(EMERGENCY_BOOTSTRAP)} stocks) [no internet, no cache]"
        source_colour = "red"

    get_tickers._source        = source_str
    get_tickers._source_colour = source_colour
    get_tickers._cache         = tickers
    return tickers
