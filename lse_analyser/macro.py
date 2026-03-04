"""
macro.py
--------
Macro and sector-level sentiment analysis.

Responsibilities:
  1. classify_event()     -- detect which type of macro event is driving
                             the market (geopolitical, recession, inflation,
                             currency, or general) from market-wide headlines
  2. fetch_macro_sentiment() -- fetch and score market-wide headlines
  3. fetch_sector_sentiment() -- fetch and score headlines for a single sector
  4. apply_macro_to_pick()   -- apply macro + sector adjustments to a pick
  5. check_macro_warning()   -- return warning level based on macro score

Sector sensitivity:
  Each sector has a defined sensitivity multiplier per event type. During a
  geopolitical event, Energy and Mining get a positive adjustment (safe havens
  and oil price beneficiaries) while Tech and Leisure are penalised. During a
  recession, defensives (ConsStaples, Utilities) hold up while cyclicals suffer.
  This means macro sentiment never applies a flat dampener -- it always
  considers how each sector actually responds to the specific event type.

Fallback:
  If NewsAPI is unavailable all functions return neutral scores so a macro
  fetch failure never prevents the screener from running.
"""

from datetime import datetime, timedelta

import requests

from .config import (
    NEWSAPI_KEY, NEWS_LOOKBACK_DAYS,
    MACRO_MARKET_QUERY, SECTOR_QUERIES,
    EVENT_KEYWORDS, SECTOR_SENSITIVITY,
    MACRO_WARNING_THRESHOLD, MACRO_SKIP_THRESHOLD,
    MACRO_MAX_PROB_SHIFT, SECTOR_REPLACE_THRESHOLD,
)

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _analyser        = SentimentIntensityAnalyzer()
    _VADER_AVAILABLE = True
except ImportError:
    _VADER_AVAILABLE = False


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_macro_sentiment() -> dict:
    """
    Fetch market-wide headlines and return a macro sentiment summary.
    Also classifies the dominant event type driving the market.

    Returns dict with keys:
      score        : weighted sentiment score (-1.0 to +1.0)
      event_type   : detected event type (geopolitical/recession/inflation/
                     currency/general)
      event_label  : human-readable event description
      label        : sentiment label
      headlines    : up to 5 headlines used
      available    : False if fetch failed
    """
    result = _fetch_sentiment(MACRO_MARKET_QUERY, "market-wide")
    if not result["available"]:
        return {**result, "event_type": "general", "event_label": "No data"}

    event_type, event_label = classify_event(result["headlines"])
    return {**result, "event_type": event_type, "event_label": event_label}


def fetch_sector_sentiment(sector: str) -> dict:
    """
    Fetch headlines for a specific sector and return a sentiment summary.
    Returns a neutral result if the sector has no configured query.
    """
    query = SECTOR_QUERIES.get(sector)
    if not query:
        return _no_data(f"No query configured for sector: {sector}")
    return _fetch_sentiment(query, sector)


def classify_event(headlines: list) -> tuple:
    """
    Scan a list of headlines for event-type keywords.
    Returns (event_type, human_readable_label).
    Defaults to "general" if no specific event type is detected.
    """
    text = " ".join(headlines).lower()

    counts = {etype: 0 for etype in EVENT_KEYWORDS}
    for etype, keywords in EVENT_KEYWORDS.items():
        counts[etype] = sum(1 for kw in keywords if kw in text)

    best_type  = max(counts, key=counts.get)
    best_count = counts[best_type]

    if best_count == 0:
        return "general", "General market conditions"

    labels = {
        "geopolitical": "Geopolitical event detected",
        "recession":    "Recession / economic slowdown signals",
        "inflation":    "Inflation / interest rate concerns",
        "currency":     "Currency / FX movement",
    }
    return best_type, labels[best_type]


def apply_macro_to_pick(result: dict, macro: dict, sector_sentiment: dict) -> dict:
    """
    Apply macro and sector sentiment adjustments to a pick in place.

    Logic:
      1. Get the sector sensitivity multiplier for this pick's sector and
         the detected event type
      2. Combine macro score with sector multiplier to get net adjustment
         (negative macro + positive sensitivity = pick is boosted relative
          to the market -- e.g. Mining during geopolitical event)
      3. Apply probability adjustment capped at MACRO_MAX_PROB_SHIFT
      4. Store macro/sector data on result for display
    """
    result["macro"]          = macro
    result["sector_news"]    = sector_sentiment

    if not macro.get("available"):
        return result

    macro_score  = macro["score"]
    event_type   = macro.get("event_type", "general")
    sector       = result["sector"]

    # Sensitivity: how this sector responds to this event type
    sensitivity_map = SECTOR_SENSITIVITY.get(event_type, SECTOR_SENSITIVITY["general"])
    sensitivity     = sensitivity_map.get(sector, 0.0)

    # Net macro effect on this sector:
    #   negative macro + positive sensitivity -> dampened negative (sector benefits)
    #   negative macro + negative sensitivity -> amplified negative
    net_macro = macro_score * (1.0 - sensitivity * 0.5)

    # Sector-specific news adds a further layer
    sector_score = sector_sentiment.get("score", 0.0) if sector_sentiment.get("available") else 0.0

    # Combined adjustment: macro net effect + sector news, weighted 60/40
    combined = (net_macro * 0.6) + (sector_score * 0.4)

    # Scale to probability points, capped at MACRO_MAX_PROB_SHIFT
    prob_delta = combined * MACRO_MAX_PROB_SHIFT
    prob_delta = max(-MACRO_MAX_PROB_SHIFT, min(MACRO_MAX_PROB_SHIFT, prob_delta))

    result["prob"] = round(min(78.0, max(20.0, result["prob"] + prob_delta)), 1)

    # Add macro note to signals
    if macro.get("available"):
        sens_desc = _sensitivity_desc(sensitivity, event_type)
        result["signals"].append(
            f"Macro ({macro['event_label']}): {macro['label']}  "
            f"-- sector response: {sens_desc}"
        )

    return result


def check_macro_warning(macro: dict) -> str:
    """
    Return a warning level string based on the macro sentiment score.
      "skip"    -- recommend skipping the week
      "warning" -- show warning but proceed
      "ok"      -- no warning needed
    """
    if not macro.get("available"):
        return "ok"
    score = macro["score"]
    if score <= MACRO_SKIP_THRESHOLD:
        return "skip"
    if score <= MACRO_WARNING_THRESHOLD:
        return "warning"
    return "ok"


def sector_needs_replacement(sector_sentiment: dict) -> bool:
    """Return True if a sector's sentiment is negative enough to seek a replacement."""
    if not sector_sentiment.get("available"):
        return False
    return sector_sentiment["score"] < SECTOR_REPLACE_THRESHOLD


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_sentiment(query: str, label: str) -> dict:
    """Shared fetch-and-score logic for any query."""
    if not NEWSAPI_KEY:
        return _no_data("No NewsAPI key configured")
    if not _VADER_AVAILABLE:
        return _no_data("vaderSentiment not installed")

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
        return _no_data(f"No recent news for {label}")

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
        return _no_data(f"No scoreable headlines for {label}")

    total_weight  = sum(w for _, w in weighted_scores)
    avg_sentiment = sum(s * w for s, w in weighted_scores) / total_weight
    n             = len(weighted_scores)
    volume_amp    = min(1.5, 1.0 + (n - 1) * 0.03)
    final_score   = max(-1.0, min(1.0, avg_sentiment * volume_amp))
    lbl           = _label(final_score)

    return {
        "score":         round(final_score, 3),
        "article_count": n,
        "headlines":     headlines_used[:5],
        "label":         lbl,
        "available":     True,
    }


def _no_data(reason: str) -> dict:
    return {
        "score": 0.0, "article_count": 0, "headlines": [],
        "label": "No data", "available": False, "reason": reason,
    }


def _label(score: float) -> str:
    if score >=  0.35: return "Very positive"
    if score >=  0.10: return "Positive"
    if score >= -0.10: return "Neutral"
    if score >= -0.35: return "Negative"
    return "Very negative"


def _sensitivity_desc(sensitivity: float, event_type: str) -> str:
    """Human-readable description of how a sector responds to the event type."""
    if sensitivity >= 0.8:   return "strong beneficiary"
    if sensitivity >= 0.3:   return "mild beneficiary"
    if sensitivity >= -0.3:  return "largely neutral"
    if sensitivity >= -0.8:  return "mild headwind"
    return "strong headwind"
