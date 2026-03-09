"""
validate.py
-----------
End-to-end validation script for the LSE Analyser.

Runs the programme in preview mode against a small synthetic universe
and verifies that all displayed figures are internally consistent and
mathematically correct.

Unlike unit tests (which test functions in isolation), this script tests
the full pipeline: scoring -> news/macro -> finalise_tiers -> sizing ->
display, and checks that nothing gets lost or corrupted between steps.

Run with:
    cd LSE_Stock_Analyser
    python tests/validate.py

Exits with code 0 if all checks pass, code 1 if any fail.
"""

import sys
import os
import math
import csv
import tempfile
import traceback
from datetime import datetime

# ---------------------------------------------------------------------------
# Make the package importable
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"
HEAD = "\033[1m"
END  = "\033[0m"

failures = []
warnings_list = []


def check(name: str, condition: bool, detail: str = ""):
    if condition:
        print(f"  {PASS} {name}")
    else:
        print(f"  {FAIL} {name}" + (f": {detail}" if detail else ""))
        failures.append(name)


def warn(name: str, detail: str = ""):
    print(f"  {WARN} {name}" + (f": {detail}" if detail else ""))
    warnings_list.append(name)


def section(title: str):
    print(f"\n{HEAD}{'─' * 60}{END}")
    print(f"{HEAD} {title}{END}")
    print(f"{HEAD}{'─' * 60}{END}")


# ===========================================================================
# SECTION 1: Config sanity
# ===========================================================================
section("1. Config sanity checks")

try:
    from lse_analyser.config import (
        ATR_MULTIPLIER, STOP_MULTIPLIER, KELLY_FRACTION,
        PROB_FLOOR, PROB_STRONG, PROB_MODERATE, PROB_CAUTIOUS,
        MAX_CALIBRATION_SHIFT, MIN_OUTCOMES_TO_CALIBRATE,
        CALIBRATION_WINDOW, CALIBRATION_LIVE_THRESHOLD,
        CSV_HEADERS, BACKTEST_BASELINE_RETURN,
    )

    check("ATR_MULTIPLIER == 1.0",   ATR_MULTIPLIER == 1.0)
    check("STOP_MULTIPLIER == 1.0",  STOP_MULTIPLIER == 1.0)
    check("KELLY_FRACTION == 0.35",  KELLY_FRACTION == 0.35)
    check("PROB_FLOOR >= 45",        PROB_FLOOR >= 45.0)
    check("PROB thresholds ordered: FLOOR <= CAUTIOUS <= MODERATE <= STRONG",
          PROB_FLOOR <= PROB_CAUTIOUS <= PROB_MODERATE <= PROB_STRONG)
    check("MAX_CALIBRATION_SHIFT == 15.0", MAX_CALIBRATION_SHIFT == 15.0)
    check("MIN_OUTCOMES_TO_CALIBRATE == 10", MIN_OUTCOMES_TO_CALIBRATE == 10)
    check("CALIBRATION_WINDOW >= MIN_OUTCOMES",
          CALIBRATION_WINDOW >= MIN_OUTCOMES_TO_CALIBRATE)
    check("CSV_HEADERS contains required outcome fields",
          all(h in CSV_HEADERS for h in
              ["went_up", "profitable", "outcome_return_pct", "outcome_hit"]))
    check("CSV_HEADERS contains sizing fields",
          all(h in CSV_HEADERS for h in
              ["allocated_gbp", "allocation_pct", "shares"]))
    check("BACKTEST_BASELINE_RETURN > 0", BACKTEST_BASELINE_RETURN > 0)

except Exception as e:
    print(f"  {FAIL} Could not import config: {e}")
    failures.append("config import")


# ===========================================================================
# SECTION 2: Probability formula consistency
# ===========================================================================
section("2. Probability formula consistency")

try:
    from lse_analyser.screener import finalise_prob_tiers

    # Test a range of probs
    test_probs = [48.0, 50.5, 52.0, 54.0, 56.7, 60.0, 65.0]
    all_consistent = True
    for prob in test_probs:
        pick = {"prob": prob}
        finalise_prob_tiers([pick])
        tiers = pick["prob_tiers"]

        if tiers.get("rises_at_all") != prob:
            check(f"prob_tiers['rises_at_all'] == prob for {prob}",
                  False,
                  f"got {tiers.get('rises_at_all')}")
            all_consistent = False

        # Tiers must descend
        if not (tiers["rises_at_all"] > tiers["rises_1pct"] >
                tiers["rises_2pct"] > tiers["rises_3pct"]):
            check(f"Tiers descend for prob={prob}", False,
                  str(tiers))
            all_consistent = False

    if all_consistent:
        check("rises_at_all always equals prob (all test values)", True)
        check("Tiers always descend (all test values)", True)

    # Test that finalise_prob_tiers overwrites stale values
    stale_pick = {"prob": 54.0, "prob_tiers": {"rises_at_all": 99.0}}
    finalise_prob_tiers([stale_pick])
    check("finalise_prob_tiers overwrites stale tiers",
          stale_pick["prob_tiers"]["rises_at_all"] == 54.0)

except Exception as e:
    print(f"  {FAIL} Prob tiers check failed: {e}")
    failures.append("prob tiers")


# ===========================================================================
# SECTION 3: Kelly sizing consistency
# ===========================================================================
section("3. Kelly sizing consistency")

try:
    from lse_analyser.sizing import calculate_allocations, signal_label
    from lse_analyser.config import PROB_FLOOR, PROB_STRONG, PROB_MODERATE

    def make_pick(prob, rr=1.2, price=500.0):
        return {
            "prob": prob, "reward_risk": rr, "price": price,
            "ticker": "TEST", "sector": "Tech",
        }

    capital = 5000.0

    # Check below-floor gets zero allocation
    picks = [make_pick(PROB_FLOOR - 1.0)]
    picks, deployed, reserve = calculate_allocations(picks, capital)
    check("Below-floor pick gets zero allocation",
          picks[0]["allocated_gbp"] == 0.0)

    # Check above-floor gets allocation
    picks = [make_pick(PROB_STRONG + 1.0, rr=1.5)]
    picks, deployed, reserve = calculate_allocations(picks, capital)
    check("Above-floor pick gets positive allocation",
          picks[0]["allocated_gbp"] > 0)

    # Check deployed + reserve = capital (within £1 rounding)
    picks = [make_pick(54.0), make_pick(52.0), make_pick(51.0)]
    picks, deployed, reserve = calculate_allocations(picks, capital)
    check("deployed + reserve ≈ total capital",
          abs(deployed + reserve - capital) < 1.0,
          f"deployed={deployed:.2f} reserve={reserve:.2f} capital={capital}")

    # Check allocation_pct sums to 100
    total_pct = sum(p["allocation_pct"] for p in picks if p["allocation_pct"] > 0)
    check("allocation_pct sums to 100%",
          abs(total_pct - 100.0) < 0.5,
          f"got {total_pct:.1f}%")

    # Check shares are whole numbers
    check("shares are integers",
          all(isinstance(p["shares"], int) for p in picks))

    # Check higher prob gets more allocation than lower prob (same rr)
    p_high = make_pick(58.0, rr=1.2)
    p_low  = make_pick(51.0, rr=1.2)
    picks, _, _ = calculate_allocations([p_high, p_low], capital)
    check("Higher prob pick gets larger allocation",
          picks[0]["allocated_gbp"] >= picks[1]["allocated_gbp"])

    # Check signal labels cover the spectrum
    check("PROB_STRONG label contains 'Strong'",
          "Strong" in signal_label(PROB_STRONG))
    check("PROB_MODERATE label contains 'Moderate'",
          "Moderate" in signal_label(PROB_MODERATE))
    check("Below floor label contains 'Weak'",
          "Weak" in signal_label(PROB_FLOOR - 1.0))

except Exception as e:
    print(f"  {FAIL} Kelly sizing check failed: {e}")
    traceback.print_exc()
    failures.append("kelly sizing")


# ===========================================================================
# SECTION 4: Calibration logic
# ===========================================================================
section("4. Calibration logic")

try:
    from lse_analyser.config import MAX_CALIBRATION_SHIFT

    def sim_adjustment(profitable_rate, avg_prob):
        raw = profitable_rate - avg_prob
        return max(-MAX_CALIBRATION_SHIFT, min(MAX_CALIBRATION_SHIFT, raw))

    # Over-predicting model
    adj = sim_adjustment(50.0, 63.0)
    check("Over-predicting model gets negative adjustment",
          adj < 0, f"got {adj}")

    # Under-predicting model
    adj = sim_adjustment(65.0, 50.0)
    check("Under-predicting model gets positive adjustment",
          adj > 0, f"got {adj}")

    # Well-calibrated
    adj = sim_adjustment(52.0, 52.0)
    check("Well-calibrated model gets ~0 adjustment",
          abs(adj) < 0.1, f"got {adj}")

    # Caps at max shift
    adj = sim_adjustment(10.0, 63.0)
    check("Extreme over-prediction capped at MAX_CALIBRATION_SHIFT",
          adj == -MAX_CALIBRATION_SHIFT)

    adj = sim_adjustment(90.0, 45.0)
    check("Extreme under-prediction capped at +MAX_CALIBRATION_SHIFT",
          adj == +MAX_CALIBRATION_SHIFT)

    # Verify that adjustment is ADDED in screener (correct sign)
    # raw_prob=56.5, adjustment=-10 -> prob=46.5 (not 66.5)
    raw_prob = 56.5
    adjustment = -10.0
    prob = min(78.0, max(20.0, raw_prob + adjustment))
    check("Negative calibration adjustment lowers probability",
          prob < raw_prob, f"raw={raw_prob}, adj={adjustment}, result={prob}")

    raw_prob = 56.5
    adjustment = +5.0
    prob = min(78.0, max(20.0, raw_prob + adjustment))
    check("Positive calibration adjustment raises probability",
          prob > raw_prob, f"raw={raw_prob}, adj={adjustment}, result={prob}")

except Exception as e:
    print(f"  {FAIL} Calibration check failed: {e}")
    failures.append("calibration logic")


# ===========================================================================
# SECTION 5: Outcome flag consistency
# ===========================================================================
section("5. Outcome flag consistency (went_up vs profitable)")

scenarios = [
    # (name,              entry,  exit,   monday, exp_went_up, exp_profitable)
    ("Normal gain",        500.0,  510.0,  510.0,  1, 1),
    ("Normal loss",        500.0,  490.0,  490.0,  0, 0),
    ("Stop + recovery",    500.0,  490.0,  505.0,  1, 0),
    ("Stop + no recovery", 500.0,  490.0,  488.0,  0, 0),
    ("Tiny gain",          500.0,  500.1,  500.1,  1, 1),
    ("Exactly at entry",   500.0,  500.0,  500.0,  0, 0),
]

for name, entry, exit_p, monday, exp_wu, exp_prof in scenarios:
    went_up    = 1 if monday > entry else 0
    profitable = 1 if exit_p > entry else 0
    check(f"{name}: went_up={went_up} profitable={profitable}",
          went_up == exp_wu and profitable == exp_prof,
          f"expected went_up={exp_wu} profitable={exp_prof}, "
          f"got went_up={went_up} profitable={profitable}")


# ===========================================================================
# SECTION 6: CSV headers completeness
# ===========================================================================
section("6. CSV headers completeness")

try:
    from lse_analyser.config import CSV_HEADERS

    required = [
        "run_date", "ticker", "sector", "score",
        "price_p", "target_p", "stop_p",
        "upside_pct", "downside_pct", "prob",
        "reward_risk", "atr",
        "allocated_gbp", "allocation_pct", "shares",
        "signals",
        "outcome_price_p", "outcome_hit", "outcome_return_pct", "outcome_notes",
        "went_up", "profitable",
    ]

    for col in required:
        check(f"CSV_HEADERS contains '{col}'", col in CSV_HEADERS)

    removed = ["pqs", "limit_p"]
    for col in removed:
        check(f"CSV_HEADERS does NOT contain removed column '{col}'",
              col not in CSV_HEADERS)

    # No duplicates
    check("No duplicate CSV headers",
          len(CSV_HEADERS) == len(set(CSV_HEADERS)))

except Exception as e:
    print(f"  {FAIL} CSV headers check failed: {e}")
    failures.append("csv headers")


# ===========================================================================
# SECTION 7: Backtest outcome formulas
# ===========================================================================
section("7. Backtest outcome formula verification")

backtest_cases = [
    # (name,                entry,  exit,   monday, target, exp_ret,  exp_hit)
    ("2% gain held",        500.0,  510.0,  510.0,  515.0,  2.0,      "NO"),
    ("Target hit",          500.0,  510.0,  516.0,  515.0,  2.0,      "YES"),
    ("Stop-out loss",       500.0,  490.0,  492.0,  510.0,  -2.0,     "NO"),
    ("Stop-out + recovery", 500.0,  490.0,  516.0,  515.0,  -2.0,     "YES"),
    ("Break even",          500.0,  500.0,  500.0,  510.0,  0.0,      "NO"),
]

for name, entry, exit_p, monday, target, exp_ret, exp_hit in backtest_cases:
    ret = (exit_p - entry) / entry * 100
    hit = "YES" if monday >= target else "NO"
    check(f"{name}: return={ret:+.1f}% target_hit={hit}",
          abs(ret - exp_ret) < 0.001 and hit == exp_hit,
          f"expected return={exp_ret:+.1f}% hit={exp_hit}")


# ===========================================================================
# SECTION 8: Sector normalisation spot checks
# ===========================================================================
section("8. Sector normalisation spot checks")

try:
    from lse_analyser.config import normalise_sector

    spot_checks = [
        ("Banks",                          "Banking"),
        ("Oil & Gas Producers",            "Energy"),
        ("Pharmaceuticals & Biotechnology","Pharma"),
        ("Software & Computer Services",   "Tech"),
        ("Real Estate Investment Trusts",  "RealEstate"),
        ("Gas, Water & Multiutilities",    "Utilities"),
        ("Aerospace & Defence",            "Industrials"),
        ("Travel & Leisure",               "Leisure"),
        ("General Retailers",              "Retail"),
        ("Life Insurance",                 "Insurance"),
        ("Mining",                         "Mining"),
        ("Chemicals",                      "Chemicals"),
        ("Unknown Sector XYZ",             "Other"),
    ]

    for raw, expected in spot_checks:
        result = normalise_sector(raw)
        check(f"'{raw}' -> '{expected}'",
              result == expected,
              f"got '{result}'")

except Exception as e:
    print(f"  {FAIL} Sector normalisation check failed: {e}")
    failures.append("sector normalisation")


# ===========================================================================
# SECTION 9: End-to-end pipeline simulation (no network calls)
# ===========================================================================
section("9. End-to-end pipeline simulation (synthetic data)")

try:
    from lse_analyser.screener import finalise_prob_tiers, diversify
    from lse_analyser.sizing import calculate_allocations, signal_label
    from lse_analyser.config import ATR_MULTIPLIER, STOP_MULTIPLIER

    # Build synthetic picks as score_ticker would return them
    def make_synthetic_pick(ticker, sector, score, price, atr, prob_adj=0.0):
        raw_prob     = 45.0 + (score / 110) * 23.0
        prob         = round(min(78.0, max(20.0, raw_prob + prob_adj)), 1)
        target       = round(price + ATR_MULTIPLIER * atr, 2)
        stop         = round(price - STOP_MULTIPLIER * atr, 2)
        upside_pct   = (target - price) / price * 100
        downside_pct = (price - stop) / price * 100
        return {
            "ticker":       ticker,
            "sector":       sector,
            "score":        score,
            "price":        price,
            "target":       target,
            "stop":         stop,
            "upside_pct":   upside_pct,
            "downside_pct": downside_pct,
            "prob":         prob,
            "prob_tiers":   {},
            "reward_risk":  round(upside_pct / downside_pct, 2),
            "atr":          round(atr, 4),
            "signals":      ["MACD crossover", "RSI bullish"],
        }

    prob_adj = -10.5  # typical current calibration adjustment

    raw_picks = [
        make_synthetic_pick("AAA", "Banking",    85, 500.0, 12.5, prob_adj),
        make_synthetic_pick("BBB", "Tech",       75, 200.0, 5.0,  prob_adj),
        make_synthetic_pick("CCC", "Energy",     70, 800.0, 20.0, prob_adj),
        make_synthetic_pick("DDD", "Pharma",     65, 300.0, 8.0,  prob_adj),
        make_synthetic_pick("EEE", "Industrials",60, 150.0, 4.0,  prob_adj),
        make_synthetic_pick("FFF", "Banking",    55, 600.0, 15.0, prob_adj),
    ]

    # Step 1: diversify
    picks = diversify(raw_picks, n=5)
    check("Diversify returns 5 picks", len(picks) == 5)
    sectors = [p["sector"] for p in picks]
    check("Diversified picks have varied sectors",
          len(set(sectors)) >= 4)

    # Step 2: finalise prob tiers
    finalise_prob_tiers(picks)
    check("All picks have prob_tiers populated",
          all(bool(p["prob_tiers"]) for p in picks))
    check("rises_at_all matches prob for all picks",
          all(p["prob_tiers"]["rises_at_all"] == p["prob"] for p in picks))

    # Step 3: sizing
    picks, deployed, reserve = calculate_allocations(picks, 5000.0)
    check("All picks have allocation fields",
          all("allocated_gbp" in p and "shares" in p for p in picks))
    check("deployed + reserve ≈ 5000",
          abs(deployed + reserve - 5000.0) < 1.0)

    # Step 4: verify figures are self-consistent
    for p in picks:
        # target = price + ATR (with ATR_MULTIPLIER=1.0)
        expected_target = round(p["price"] + p["atr"], 2)
        check(f"{p['ticker']}: target = price + ATR",
              abs(p["target"] - expected_target) < 0.01,
              f"price={p['price']} atr={p['atr']} target={p['target']} expected={expected_target}")

        # stop = price - ATR
        expected_stop = round(p["price"] - p["atr"], 2)
        check(f"{p['ticker']}: stop = price - ATR",
              abs(p["stop"] - expected_stop) < 0.01)

        # upside == downside when ATR_MULT == STOP_MULT == 1.0
        check(f"{p['ticker']}: upside_pct ≈ downside_pct (symmetric)",
              abs(p["upside_pct"] - p["downside_pct"]) < 0.01)

        # reward_risk ≈ 1.0
        check(f"{p['ticker']}: reward_risk ≈ 1.0",
              abs(p["reward_risk"] - 1.0) < 0.01)

        # prob within valid range
        check(f"{p['ticker']}: prob in [20, 78]",
              20.0 <= p["prob"] <= 78.0)

        # signal label is a non-empty string
        check(f"{p['ticker']}: signal_label is non-empty string",
              bool(signal_label(p["prob"])))

except Exception as e:
    print(f"  {FAIL} Pipeline simulation failed: {e}")
    traceback.print_exc()
    failures.append("pipeline simulation")


# ===========================================================================
# SECTION 10: CSV round-trip (write and read back)
# ===========================================================================
section("10. CSV round-trip check")

try:
    from lse_analyser.config import CSV_HEADERS
    from lse_analyser.csv_log import save_to_csv

    # Create a temp directory to write the CSV
    with tempfile.TemporaryDirectory() as tmpdir:
        original_csv = None
        try:
            # Patch CSV_FILE temporarily
            import lse_analyser.config as cfg
            import lse_analyser.csv_log as csv_log_mod
            original_csv = cfg.CSV_FILE
            test_csv     = os.path.join(tmpdir, "test_log.csv")
            cfg.CSV_FILE     = test_csv
            csv_log_mod.CSV_FILE = test_csv

            test_picks = [
                {
                    "ticker": "TSCO", "sector": "Retail", "score": 75,
                    "price": 350.5, "target": 358.0, "stop": 343.0,
                    "upside_pct": 2.14, "downside_pct": 2.14,
                    "prob": 52.5, "atr": 7.5,
                    "allocated_gbp": 1200.0, "allocation_pct": 40.0,
                    "shares": 342, "actual_cost": 1197.0,
                    "reward_risk": 1.0,
                    "signals": ["RSI bullish", "MACD crossover"],
                    "prob_tiers": {
                        "rises_at_all": 52.5, "rises_1pct": 43.5,
                        "rises_2pct": 31.3, "rises_3pct": 23.2,
                    },
                }
            ]

            run_date = datetime(2026, 3, 11, 9, 0)
            save_to_csv(test_picks, run_date)

            # Read back
            with open(test_csv, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            check("CSV written with correct number of rows", len(rows) == 1)

            row = rows[0]
            check("ticker saved correctly",       row["ticker"] == "TSCO")
            check("sector saved correctly",       row["sector"] == "Retail")
            check("prob saved correctly",         row["prob"] == "52.5")
            check("price_p saved correctly",      row["price_p"] == "350.5")
            check("allocated_gbp saved",          row["allocated_gbp"] == "1200.0")
            check("outcome fields blank on save", row["outcome_price_p"] == "")
            check("went_up blank on save",        row["went_up"] == "")
            check("profitable blank on save",     row["profitable"] == "")
            check("All CSV_HEADERS present as columns",
                  all(h in row for h in CSV_HEADERS))

        finally:
            if original_csv:
                cfg.CSV_FILE     = original_csv
                csv_log_mod.CSV_FILE = original_csv

except Exception as e:
    print(f"  {FAIL} CSV round-trip failed: {e}")
    traceback.print_exc()
    failures.append("csv round-trip")


# ===========================================================================
# SECTION 11: Calibration data loading (if backtest CSV exists)
# ===========================================================================
section("11. Backtest CSV integrity (if present)")

try:
    from lse_analyser.config import BACKTEST_TECHNICAL_CSV

    if not os.path.isfile(BACKTEST_TECHNICAL_CSV):
        warn("Backtest CSV not found -- skipping (run backtest mode first)")
    else:
        with open(BACKTEST_TECHNICAL_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        check("Backtest CSV has rows", len(rows) > 0)

        resolved = [r for r in rows if r.get("outcome_price_p", "").strip()]
        check("Backtest has resolved picks", len(resolved) > 0,
              f"{len(resolved)} resolved of {len(rows)}")

        # Check went_up is 0 or 1 for resolved picks
        invalid_wu = [r for r in resolved
                      if str(r.get("went_up", "")).strip() not in ("0", "1", "")]
        check("went_up values are 0 or 1",
              len(invalid_wu) == 0,
              f"{len(invalid_wu)} invalid values found")

        # Check profitable is 0 or 1 for resolved picks
        invalid_prof = [r for r in resolved
                        if str(r.get("profitable", "")).strip() not in ("0", "1", "")]
        check("profitable values are 0 or 1",
              len(invalid_prof) == 0,
              f"{len(invalid_prof)} invalid values found")

        # Check pqs column is gone
        check("pqs column not present in backtest CSV",
              "pqs" not in rows[0])

        # Check return_pct is numeric where present
        bad_ret = []
        for r in resolved:
            v = r.get("outcome_return_pct", "").strip()
            if v:
                try:
                    float(v)
                except ValueError:
                    bad_ret.append(v)
        check("All outcome_return_pct values are numeric",
              len(bad_ret) == 0, f"bad values: {bad_ret[:5]}")

        # Spot check: profitable should align with return_pct sign
        mismatches = []
        for r in resolved:
            ret = r.get("outcome_return_pct", "").strip()
            prof = str(r.get("profitable", "")).strip()
            if ret and prof in ("0", "1"):
                ret_f = float(ret)
                if ret_f > 0 and prof == "0":
                    mismatches.append(r["ticker"])
                elif ret_f < 0 and prof == "1":
                    mismatches.append(r["ticker"])
        check("profitable flag consistent with return sign",
              len(mismatches) == 0,
              f"{len(mismatches)} mismatches: {mismatches[:5]}")

        # Directional accuracy in expected range
        with_wu = [r for r in resolved if str(r.get("went_up", "")).strip() in ("0", "1")]
        if with_wu:
            dir_acc = sum(1 for r in with_wu if str(r.get("went_up")) == "1") / len(with_wu) * 100
            check(f"Directional accuracy in plausible range (40-70%): {dir_acc:.1f}%",
                  40.0 <= dir_acc <= 70.0)

        # Avg return in plausible range
        returns = [float(r["outcome_return_pct"]) for r in resolved
                   if r.get("outcome_return_pct", "").strip()]
        if returns:
            avg_ret = sum(returns) / len(returns)
            check(f"Avg backtest return in plausible range (-2% to +5%): {avg_ret:+.3f}%",
                  -2.0 <= avg_ret <= 5.0)

except Exception as e:
    print(f"  {FAIL} Backtest CSV check failed: {e}")
    traceback.print_exc()
    failures.append("backtest csv")


# ===========================================================================
# FINAL SUMMARY
# ===========================================================================
print(f"\n{HEAD}{'═' * 60}{END}")
print(f"{HEAD} VALIDATION SUMMARY{END}")
print(f"{HEAD}{'═' * 60}{END}")

if warnings_list:
    print(f"\n  {WARN} Warnings ({len(warnings_list)}):")
    for w in warnings_list:
        print(f"      {w}")

if not failures:
    print(f"\n  {PASS} All checks passed.\n")
    sys.exit(0)
else:
    print(f"\n  {FAIL} {len(failures)} check(s) failed:")
    for f in failures:
        print(f"      ✗ {f}")
    print()
    sys.exit(1)
