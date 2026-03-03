"""
config.py
---------
All tunable parameters and constants for the LSE Analyser.
Edit values here to adjust the model without touching any other module.
"""

CSV_FILE     = "lse_screener_log.csv"
TICKERS_JSON = "ftse_tickers.json"

LOOKBACK_DAYS      = 120
ATR_MULTIPLIER     = 1.5
STOP_MULTIPLIER    = 1.0
LIMIT_BUFFER       = 0.995
TOP_N              = 5
MIN_AVG_VOLUME_GBP = 500_000

PROB_FLOOR     = 40.0
KELLY_FRACTION = 0.25

MIN_OUTCOMES_TO_CALIBRATE = 10
CALIBRATION_WINDOW        = 50
MAX_CALIBRATION_SHIFT     = 15.0

CSV_HEADERS = [
    "run_date", "ticker", "sector", "score", "price_p", "target_p",
    "stop_p", "limit_p", "upside_pct", "downside_pct", "prob",
    "reward_risk", "atr", "allocated_gbp", "shares",
    "signals", "outcome_price_p", "outcome_hit", "outcome_return_pct",
    "outcome_notes",
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
    ("FinServices",  ["financial service", "investment trust", "collective invest",
                      "equity invest", "general financial", "investment banking",
                      "hedge fund"]),
    ("Pharma",       ["pharma", "biotechnology", "health care", "health care equip",
                      "medical"]),
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
    ("RealEstate",   ["real estate", "reit", "real estate invest"]),
    ("Utilities",    ["util", "water", "multiutil", "gas, water"]),
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
