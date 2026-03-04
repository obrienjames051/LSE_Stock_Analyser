"""
news.py
-------
News sentiment analysis for shortlisted stock candidates.

Fetches recent headlines from NewsAPI and scores them using VADER sentiment
analysis. The resulting score adjusts both the technical score and
probability estimate of each candidate.

Scoring approach:
  - Each headline+description is scored individually (-1.0 to +1.0)
  - Scores are weighted by recency (today = 1.0, oldest = 0.3)
  - Volume has a mild amplifying effect but a single extreme headline
    still carries meaningful weight on its own
  - Final adjustments are capped so news never overwhelms technical signals

Fallback:
  - If NewsAPI is unavailable or returns nothing the ticker passes through
    unchanged so a NewsAPI outage never breaks the screener
"""

from datetime import datetime, timedelta

import requests

from .config import NEWSAPI_KEY, NEWS_LOOKBACK_DAYS, NEWS_MAX_SCORE_ADJ

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _analyser        = SentimentIntensityAnalyzer()
    _VADER_AVAILABLE = True
except ImportError:
    _VADER_AVAILABLE = False


def fetch_news_sentiment(ticker: str, company_name: str = "") -> dict:
    """
    Fetch recent headlines for a stock and return a sentiment summary dict:
      score         : weighted sentiment score (-1.0 to +1.0)
      article_count : number of articles analysed
      headlines     : up to 5 headlines used (for display)
      label         : human-readable label
      note          : short string for signal breakdown
      available     : False if no data could be fetched
    """
    if not NEWSAPI_KEY:
        return _no_data("No NewsAPI key configured")
    if not _VADER_AVAILABLE:
        return _no_data("vaderSentiment not installed")

    query     = company_name if company_name else ticker.replace(".L", "")
    from_date = (datetime.now() - timedelta(days=NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    try:
        response = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q":        query,
                "from":     from_date,
                "language": "en",
                "sortBy":   "publishedAt",
                "pageSize": 20,
                "apiKey":   NEWSAPI_KEY,
            },
            timeout=10,
        )
        response.raise_for_status()
        articles = response.json().get("articles", [])
    except Exception as e:
        return _no_data(f"NewsAPI error: {e}")

    if not articles:
        return _no_data("No recent news found")

    now             = datetime.now()
    weighted_scores = []
    headlines_used  = []

    for article in articles:
        title       = (article.get("title") or "").strip()
        description = (article.get("description") or "").strip()
        published   = (article.get("publishedAt") or "")[:10]

        text = f"{title}. {description}".strip(". ")
        if not text:
            continue

        # Recency weight: today = 1.0, oldest = 0.3
        try:
            age_days       = max(0, (now - datetime.strptime(published, "%Y-%m-%d")).days)
            recency_weight = max(0.3, 1.0 - (age_days / NEWS_LOOKBACK_DAYS) * 0.7)
        except Exception:
            recency_weight = 0.5

        vader = _analyser.polarity_scores(text)["compound"]
        weighted_scores.append((vader, recency_weight))
        if title:
            headlines_used.append(title)

    if not weighted_scores:
        return _no_data("No scoreable headlines found")

    # Weighted average sentiment
    total_weight  = sum(w for _, w in weighted_scores)
    avg_sentiment = sum(s * w for s, w in weighted_scores) / total_weight

    # Mild volume amplifier -- capped at 1.5x so it never dominates
    n           = len(weighted_scores)
    volume_amp  = min(1.5, 1.0 + (n - 1) * 0.03)
    final_score = max(-1.0, min(1.0, avg_sentiment * volume_amp))
    label       = _label(final_score)

    return {
        "score":         round(final_score, 3),
        "article_count": n,
        "headlines":     headlines_used[:5],
        "label":         label,
        "note":          (f"News sentiment: {label} "
                         f"({n} article{'s' if n != 1 else ''}, "
                         f"score {final_score:+.2f})"),
        "available":     True,
    }


def apply_news_adjustment(result: dict, sentiment: dict) -> dict:
    """
    Apply a news sentiment adjustment to a scored ticker result in place.
    Modifies score, prob, and signals list. Always stores sentiment on result.
    """
    result["news"] = sentiment

    if not sentiment.get("available"):
        return result

    s = sentiment["score"]

    # Score adjustment: scale sentiment to +/- NEWS_MAX_SCORE_ADJ points
    result["score"] = max(0, result["score"] + round(s * NEWS_MAX_SCORE_ADJ))

    # Probability adjustment: +/- up to 10pp
    result["prob"] = round(min(78.0, max(20.0, result["prob"] + round(s * 10, 1))), 1)

    # Append to signals list for display
    result["signals"].append(sentiment["note"])

    return result


def _no_data(reason: str) -> dict:
    return {
        "score": 0.0, "article_count": 0, "headlines": [],
        "label": "No data", "note": f"News: {reason}", "available": False,
    }


def _label(score: float) -> str:
    if score >=  0.35: return "Very positive"
    if score >=  0.10: return "Positive"
    if score >= -0.10: return "Neutral"
    if score >= -0.35: return "Negative"
    return "Very negative"
