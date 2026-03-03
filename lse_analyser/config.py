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

SECTOR_MAP = {
    "Automobiles & Parts":                  "ConsDis",
    "Banks":                                "Banking",
    "Basic Resources":                      "Mining",
    "Chemicals":                            "Chemicals",
    "Construction & Materials":             "Materials",
    "Consumer Products & Services":         "ConsDis",
    "Energy":                               "Energy",
    "Financial Services":                   "FinServices",
    "Food, Beverage & Tobacco":             "ConsStaples",
    "Health Care":                          "Pharma",
    "Industrial Goods & Services":          "Industrials",
    "Insurance":                            "Insurance",
    "Investment Trusts":                    "FinServices",
    "Media":                                "Media",
    "Personal Care, Drug & Grocery Stores": "Retail",
    "Real Estate":                          "RealEstate",
    "Retail":                               "Retail",
    "Technology":                           "Tech",
    "Telecommunications":                   "Telecoms",
    "Travel & Leisure":                     "Leisure",
    "Utilities":                            "Utilities",
}

EMERGENCY_BOOTSTRAP = {
    "AZN.L":  "Pharma",      "SHEL.L": "Energy",      "HSBA.L": "Banking",
    "ULVR.L": "ConsStaples", "BP.L":   "Energy",       "RIO.L":  "Mining",
    "GSK.L":  "Pharma",      "LSEG.L": "FinServices",  "NG.L":   "Utilities",
    "VOD.L":  "Telecoms",    "BARC.L": "Banking",      "LLOY.L": "Banking",
    "NWG.L":  "Banking",     "BATS.L": "ConsStaples",  "DGE.L":  "ConsStaples",
    "PRU.L":  "Insurance",   "RR.L":   "Industrials",  "IAG.L":  "Leisure",
    "SSE.L":  "Utilities",   "REL.L":  "Tech",
}
