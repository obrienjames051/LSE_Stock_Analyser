"""
news_log.py
-----------
Logs the news and sentiment data used each time the programme runs in live mode.

One row per pick per run. Records the raw sentiment scores, headlines, and
score adjustment applied -- enough to analyse how news influenced decisions
and to build a historical news dataset over time.

The log is append-only. It is never read by the main programme; it exists
purely as a research and audit trail.
"""

import csv
import os
from datetime import datetime

from .config import NEWS_LOG_FILE, NEWS_LOG_HEADERS


def log_news(picks: list, run_date: str, macro: dict, sector_cache: dict):
    """
    Append news data for the current run's picks to the news log CSV.

    Args:
        picks:        Final list of picked stocks (after news adjustment applied)
        run_date:     Run timestamp string e.g. "2026-03-11 08:30"
        macro:        Macro sentiment dict from fetch_macro_sentiment()
        sector_cache: Dict of {sector: sentiment_dict} from sector news fetches
    """
    file_exists = os.path.isfile(NEWS_LOG_FILE)

    with open(NEWS_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NEWS_LOG_HEADERS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()

        for r in picks:
            news     = r.get("news", {})
            sec_news = sector_cache.get(r.get("sector", ""), {})

            # Score adjustment: difference between pre-news score and final score
            # The screener stores the original technical score; news adjustments
            # are additive. We record the net change from news + macro layers.
            base_score    = r.get("base_score", r.get("score", ""))
            final_score   = r.get("score", "")
            try:
                adjustment = int(final_score) - int(base_score)
            except (TypeError, ValueError):
                adjustment = ""

            writer.writerow({
                "run_date":               run_date,
                "ticker":                 r.get("ticker", ""),
                "sector":                 r.get("sector", ""),
                "company_news_score":     round(news.get("score", 0), 4) if news.get("available") else "",
                "company_news_available": news.get("available", False),
                "company_headlines":      " | ".join(news.get("headlines", [])[:5]),
                "sector_news_score":      round(sec_news.get("score", 0), 4) if sec_news.get("available") else "",
                "sector_news_available":  sec_news.get("available", False),
                "sector_headlines":       " | ".join(sec_news.get("headlines", [])[:3]),
                "macro_score":            round(macro.get("score", 0), 4) if macro.get("available") else "",
                "macro_event":            macro.get("event_label", ""),
                "macro_available":        macro.get("available", False),
                "score_adjustment":       adjustment,
            })
