"""
config.py
---------
All tunable parameters and constants for the LSE Analyser.
Edit values here to adjust the model without touching any other module.
"""

CSV_FILE     = "lse_screener_log.csv"
TICKERS_JSON = "ftse_tickers.json"

LOOKBACK_DAYS      = 120
ATR_MULTIPLIER     = 1.0   # Lowered from 1.5 -- more achievable 7-day target
STOP_MULTIPLIER    = 1.0
LIMIT_BUFFER       = 0.995
TOP_N              = 5
MIN_AVG_VOLUME_GBP = 500_000
SCORE_CAP          = 85    # Picks scoring >= this are excluded (confirmed Strategy E, RESEARCH.md §15)
                           # Scores only exist at multiples of 5; cap at 85 excludes scores 85, 90, 95, 100+
                           # Effective score range for selected picks: 55–84

PROB_FLOOR     = 50.0   # Below random baseline -- model has no edge, skip
KELLY_FRACTION = 0.35

# Signal strength thresholds relative to backtest directional accuracy of 52.2%.
# Calibration adjustment shifts the absolute values, but the thresholds are set
# relative to the actual output range (~48-58% with current -10.5pp adjustment).
# Review these if the calibration adjustment shifts significantly.
PROB_STRONG    = 55.0   # Strong signal -- well above baseline
PROB_MODERATE  = 52.0   # Moderate signal -- above baseline
PROB_CAUTIOUS  = 50.0   # Cautious signal -- at or near baseline

# Tiered probability thresholds for display
# Shows probability of rising at each return level based on backtest distribution
PROB_TIERS = [
    (0.0,  "rises at all"),
    (1.0,  "rises > 1%"),
    (2.0,  "rises > 2%"),
    (3.0,  "rises > 3%"),
]

MIN_OUTCOMES_TO_CALIBRATE = 10
CALIBRATION_WINDOW        = 50
MAX_CALIBRATION_SHIFT     = 15.0

CSV_HEADERS = [
    "run_date", "ticker", "sector", "score", "price_p", "target_p",
    "stop_p", "upside_pct", "downside_pct", "prob",
    "reward_risk", "atr", "allocated_gbp", "allocation_pct", "shares",
    "signals", "outcome_price_p", "outcome_hit", "outcome_return_pct",
    "outcome_notes",
    "went_up",      # 1 if monday close > tuesday open (raw directional accuracy)
    "profitable",   # 1 if actual exit price > entry price (used for calibration)
]

NEWS_LOG_FILE = "lse_news_log.csv"
NEWS_LOG_HEADERS = [
    "run_date", "ticker", "sector",
    "company_news_score", "company_news_available", "company_headlines",
    "sector_news_score", "sector_news_available", "sector_headlines",
    "macro_score", "macro_event", "macro_available",
    "score_adjustment",
]

# ---------------------------------------------------------------------------
# SECTOR NORMALISATION
# ---------------------------------------------------------------------------
# Wikipedia uses inconsistent naming across FTSE 100 and FTSE 250 pages —
# different capitalisation, "and" vs "&", old vs new ICB names, and highly
# specific subcategory names. Rather than maintaining an exhaustive exact-
# match dictionary, we use keyword-based fuzzy matching: the raw sector
# string is lowercased and checked for the presence of each keyword list.
# Rules are evaluated in order; the first match wins.
# ---------------------------------------------------------------------------
SECTOR_KEYWORDS = [
    # (short label,  keywords that must appear in the lowercased sector string)
    ("Banking",      ["bank"]),
    ("Insurance",    ["insurance"]),
    ("RealEstate",   ["real estate", "reit"]),
    ("FinServices",  ["financial service", "investment trust", "collective invest",
                      "equity invest", "general financial", "investment banking",
                      "hedge fund"]),
    ("Pharma",       ["pharma", "biotechnology", "health care", "health care equip",
                      "medical"]),
    ("Utilities",    ["multiutil", "gas, water", "water util", "util"]),
    ("Energy",       ["oil", "gas", "energy", "coal", "alternative energy",
                      "electrical util", "electricity"]),
    ("Mining",       ["mining", "metal", "precious metal", "basic resource"]),
    ("Chemicals",    ["chemical"]),
    ("Materials",    ["construction & material", "construction and material",
                      "engineering and construction", "homebuilding",
                      "home construction", "household good", "container",
                      "packaging", "construction"]),
    ("Industrials",  ["aerospace", "defence", "defense", "industrial engineer",
                      "general industrial", "industrial good", "industrial support",
                      "industrial transport", "support service", "electronic",
                      "industrial metal", "industrials"]),
    ("Tech",         ["software", "computer service", "technology",
                      "consumer digital", "electronic equip"]),
    ("ConsStaples",  ["beverage", "food", "tobacco", "personal good",
                      "personal product", "consumer staple", "household",
                      "food and drink"]),
    ("Retail",       ["retail", "general retailer", "drug retail"]),
    ("ConsDis",      ["automobile", "leisure good", "personal good"]),
    ("Telecoms",     ["telecom", "mobile telecom"]),
    ("Media",        ["media"]),
    ("Leisure",      ["travel", "leisure", "hospitality"]),
]


def normalise_sector(raw: str) -> str:
    """
    Map a raw Wikipedia sector string to a short internal label.
    Uses keyword matching so minor naming variations are handled gracefully.
    Returns "Other" if no rule matches.
    """
    lowered = raw.lower().strip()
    for label, keywords in SECTOR_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return label
    return "Other"


EMERGENCY_BOOTSTRAP = {
    "AZN.L":  "Pharma",      "SHEL.L": "Energy",      "HSBA.L": "Banking",
    "ULVR.L": "ConsStaples", "BP.L":   "Energy",       "RIO.L":  "Mining",
    "GSK.L":  "Pharma",      "LSEG.L": "FinServices",  "NG.L":   "Utilities",
    "VOD.L":  "Telecoms",    "BARC.L": "Banking",      "LLOY.L": "Banking",
    "NWG.L":  "Banking",     "BATS.L": "ConsStaples",  "DGE.L":  "ConsStaples",
    "PRU.L":  "Insurance",   "RR.L":   "Industrials",  "IAG.L":  "Leisure",
    "SSE.L":  "Utilities",   "REL.L":  "Tech",
}

# ---------------------------------------------------------------------------
# NEWS SENTIMENT
# ---------------------------------------------------------------------------
# NEWSAPI_KEY is loaded from the .env file in the project root folder.
# Never hardcode the key here — .env is listed in .gitignore.
# ---------------------------------------------------------------------------
import os as _os

def _load_env():
    """Load key=value pairs from .env file into environment if present."""
    env_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), ".env")
    if not _os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                _os.environ.setdefault(key.strip(), value.strip())

_load_env()

NEWSAPI_KEY            = _os.environ.get("NEWSAPI_KEY", "")
NEWS_LOOKBACK_DAYS     = 7      # How many days of articles to fetch
NEWS_MAX_SCORE_ADJ     = 15     # Max points added/subtracted from technical score
NEWS_CANDIDATE_COUNT   = 20     # How many top candidates to run news analysis on
NEWS_FALLBACK_BATCH    = 10     # How many extra candidates to check if <5 pass sentiment

# ---------------------------------------------------------------------------
# MACRO & SECTOR SENTIMENT
# ---------------------------------------------------------------------------

# Thresholds for market-wide sentiment warnings
MACRO_WARNING_THRESHOLD = -0.4   # Show warning panel, reduce probabilities
MACRO_SKIP_THRESHOLD    = -0.6   # Recommend skipping the week

# How much macro sentiment can shift probabilities (pp) at maximum negative
MACRO_MAX_PROB_SHIFT    = 15.0

# Sector sentiment threshold below which a pick is flagged for replacement
SECTOR_REPLACE_THRESHOLD = -0.25

# NewsAPI search queries for market-wide and each sector
MACRO_MARKET_QUERY = "FTSE stock market UK economy global markets"

SECTOR_QUERIES = {
    "Banking":      "UK banking sector banks",
    "Insurance":    "UK insurance sector",
    "FinServices":  "UK financial services sector",
    "Pharma":       "UK pharmaceutical sector healthcare",
    "Energy":       "UK energy sector oil gas",
    "Mining":       "UK mining sector metals commodities",
    "Chemicals":    "UK chemicals sector",
    "Materials":    "UK construction materials sector",
    "Industrials":  "UK industrials aerospace defence sector",
    "Tech":         "UK technology sector",
    "ConsStaples":  "UK consumer staples food beverage sector",
    "Retail":       "UK retail sector",
    "ConsDis":      "UK consumer discretionary sector",
    "Telecoms":     "UK telecommunications sector",
    "Media":        "UK media sector",
    "Leisure":      "UK travel leisure hospitality sector",
    "RealEstate":   "UK real estate property sector",
    "Utilities":    "UK utilities water electricity sector",
}

# Event classification keywords
EVENT_KEYWORDS = {
    "geopolitical": [
        "war", "conflict", "attack", "military", "sanctions", "invasion",
        "missile", "airstrike", "troops", "nato", "terror", "geopolit",
    ],
    "recession": [
        "recession", "gdp", "contraction", "slowdown", "unemployment",
        "layoffs", "downturn", "depression", "shrink", "negative growth",
    ],
    "inflation": [
        "inflation", "cpi", "rpi", "rate rise", "interest rate", "rate hike",
        "hawkish", "price rise", "cost of living", "stagflation",
    ],
    "currency": [
        "pound", "sterling", "gbp", "exchange rate", "currency", "dollar",
        "forex", "devaluation", "weak pound", "strong dollar",
    ],
}

# Sector sensitivity maps per event type.
# Values: +1.0 = strong positive, 0.0 = neutral, -1.0 = strong negative.
# These are multiplied by the macro sentiment score to get the sector adjustment.
SECTOR_SENSITIVITY = {
    "geopolitical": {
        "Energy":      1.5,   # Oil price spike benefits producers
        "Mining":      1.5,   # Gold/precious metals safe haven
        "Utilities":   0.5,   # Defensive
        "ConsStaples": 0.5,   # Defensive
        "Pharma":      0.3,   # Slightly defensive
        "Telecoms":    0.0,   # Neutral
        "Media":       0.0,   # Neutral
        "Chemicals":  -0.5,
        "Materials":  -0.5,
        "Industrials": -0.8,  # Defence subsector up but broad industrials down
        "Banking":    -0.8,
        "FinServices": -0.8,
        "Tech":        -1.0,
        "Leisure":     -1.0,  # Travel hit hard
        "Retail":      -0.8,
        "ConsDis":     -0.8,
        "RealEstate":  -0.8,
        "Insurance":   -0.5,
    },
    "recession": {
        "ConsStaples": 0.8,   # Defensive rotation
        "Utilities":   0.8,   # Defensive rotation
        "Pharma":      0.5,   # Defensive
        "Mining":      0.3,   # Gold holds up
        "Telecoms":    0.2,   # Fairly defensive
        "Media":       0.0,
        "Insurance":  -0.2,
        "Energy":     -0.3,   # Lower demand
        "RealEstate":  -0.5,
        "Banking":    -0.8,   # Credit risk rises
        "FinServices": -0.8,
        "Chemicals":  -0.8,
        "Materials":  -0.8,
        "Industrials": -0.8,
        "Tech":        -0.8,
        "Retail":      -0.8,
        "ConsDis":     -1.0,
        "Leisure":     -1.0,
    },
    "inflation": {
        "Energy":      1.0,   # Higher commodity prices
        "Mining":      1.0,   # Commodities hedge
        "Materials":   0.5,
        "ConsStaples": 0.3,   # Can pass on price rises
        "Industrials": 0.0,
        "Chemicals":   0.0,
        "Insurance":   0.5,   # Investment returns improve
        "Banking":     0.3,   # Net interest margin can improve
        "Pharma":      0.0,
        "Media":       0.0,
        "Utilities":  -0.3,   # Cost pressures
        "Leisure":    -0.5,   # Consumer spending squeezed
        "Retail":     -0.5,
        "ConsDis":    -0.5,
        "Tech":        -0.8,  # Growth stocks devalued by rate rises
        "RealEstate":  -1.0,  # Rate sensitive
        "Telecoms":   -0.8,
        "FinServices": -0.3,
    },
    "currency": {
        # GBP weakness: exporters/multinationals benefit (most large FTSE cos)
        "Energy":      0.8,   # Dollar-denominated revenues
        "Mining":      0.8,   # Dollar-denominated revenues
        "Pharma":      0.5,   # Global revenues
        "Tech":        0.5,   # Global revenues
        "ConsStaples": 0.3,   # Some global exposure
        "Industrials": 0.3,
        "Telecoms":    0.0,   # Mostly domestic
        "Media":       0.0,
        "Utilities":  -0.3,   # Domestic, import costs rise
        "Materials":  -0.3,
        "Chemicals":  -0.3,
        "Banking":    -0.2,
        "FinServices": 0.2,   # Mixed
        "Insurance":   0.0,
        "Retail":     -0.5,   # Import costs rise
        "ConsDis":    -0.3,
        "Leisure":     0.2,   # Inbound tourism benefits
        "RealEstate":  -0.2,
    },
}

# Fallback sensitivity used when no specific event type is detected
SECTOR_SENSITIVITY["general"] = {
    "ConsStaples": 0.5, "Utilities": 0.5, "Pharma": 0.3, "Mining": 0.3,
    "Telecoms": 0.1, "Media": 0.0, "Insurance": 0.0, "Energy": 0.0,
    "Banking": -0.3, "FinServices": -0.3, "Tech": -0.5, "Industrials": -0.3,
    "Chemicals": -0.3, "Materials": -0.3, "Retail": -0.4, "ConsDis": -0.5,
    "Leisure": -0.5, "RealEstate": -0.4,
}

# ---------------------------------------------------------------------------
# BACKTESTING
# ---------------------------------------------------------------------------

BACKTEST_TECHNICAL_CSV   = "lse_backtest_technical.csv"

BACKTEST_WEEKS_TECHNICAL = 52      # How many weeks of technical-only history to test
BACKTEST_CAPITAL         = 1000.0  # Arbitrary capital for Kelly sizing in backtest
                                   # Actual amount irrelevant -- only pct return used

# Baseline return confirmed from backtesting research (see RESEARCH.md)
# Window: Tuesday open -> Monday close, stops only, no limits, 51 weeks
BACKTEST_BASELINE_RETURN = 1.1188   # % per pick per week -- auto-updated by backtest

# Calibration weights for each data source
# Live picks are always 1.0 (the reference point)
CALIBRATION_WEIGHT_LIVE      = 1.0
CALIBRATION_WEIGHT_TECHNICAL = 0.6   # Historical but news-blind

# Once this many live picks are resolved, backtest data is phased out entirely
CALIBRATION_LIVE_THRESHOLD   = 30

# Fixed end date for all backtests -- ensures every script uses the same
# 52-week window regardless of what day it is run. Set to the Monday of
# the week the original Phase 1 backtest was completed.
# To extend the backtest window, update this date to the current Monday.
BACKTEST_END_DATE = "2026-03-02"  # Monday 2 March 2026
