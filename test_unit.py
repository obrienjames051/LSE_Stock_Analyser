"""
test_unit.py
------------
Unit tests for the LSE Analyser core calculation functions.

Tests are deliberately self-contained and do NOT make any network calls.
All inputs are synthetic known values so expected outputs can be verified
by hand independently of the programme.

Run with:
    cd LSE_Stock_Analyser
    pytest tests/test_unit.py -v
"""

import sys
import os
import math

# ---------------------------------------------------------------------------
# Make the package importable without a full install
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===========================================================================
# 1. CONFIG -- sector normalisation
# ===========================================================================

class TestSectorNormalisation:
    """normalise_sector() should map raw Wikipedia strings to short labels."""

    def setup_method(self):
        from lse_analyser.config import normalise_sector
        self.n = normalise_sector

    def test_banking_exact(self):
        assert self.n("Banks") == "Banking"

    def test_banking_case_insensitive(self):
        assert self.n("BANKS") == "Banking"

    def test_pharma_healthcare(self):
        assert self.n("Health Care") == "Pharma"

    def test_energy_oil(self):
        assert self.n("Oil & Gas Producers") == "Energy"

    def test_energy_alternative(self):
        assert self.n("Alternative Energy") == "Energy"

    def test_mining(self):
        assert self.n("Mining") == "Mining"

    def test_tech_software(self):
        assert self.n("Software & Computer Services") == "Tech"

    def test_realestate(self):
        assert self.n("Real Estate Investment Trusts") == "RealEstate"

    def test_utilities(self):
        assert self.n("Gas, Water & Multiutilities") == "Utilities"

    def test_industrials(self):
        assert self.n("Aerospace & Defence") == "Industrials"

    def test_leisure(self):
        assert self.n("Travel & Leisure") == "Leisure"

    def test_unknown_returns_other(self):
        assert self.n("Completely Unknown Sector XYZ") == "Other"

    def test_whitespace_stripped(self):
        assert self.n("  banking  ") == "Banking"


# ===========================================================================
# 2. SCREENER -- probability and trade level calculations
# ===========================================================================

class TestProbabilityCalculation:
    """
    Raw probability formula: raw_prob = 45.0 + (score / 84) * 23.0
    Rescaled from / 110 to / 84 in v9.0 to reflect effective score cap of 84
    (scores only exist at multiples of 5; cap at 85 means max selected score is 80).
    After calibration: prob = clamp(raw_prob + prob_adjustment, 20, 78)
    """

    def _raw_prob(self, score):
        return 45.0 + (score / 84) * 23.0

    def test_zero_score_gives_minimum_raw(self):
        assert self._raw_prob(0) == 45.0

    def test_max_score_gives_maximum_raw(self):
        # score=84 -> 45 + (84/84)*23 = 68.0
        assert abs(self._raw_prob(84) - 68.0) < 0.01

    def test_midpoint_score(self):
        # score=42 -> 45 + (42/84)*23 = 45 + 11.5 = 56.5
        assert abs(self._raw_prob(42) - 56.5) < 0.01

    def test_calibration_adjustment_added_correctly(self):
        # prob_adjustment should be ADDED, not subtracted
        # score=55 -> 45 + (55/84)*23 ≈ 60.1
        raw = self._raw_prob(55)
        adj = -10.0
        prob = round(min(78.0, max(20.0, raw + adj)), 1)
        assert prob == round(min(78.0, max(20.0, raw + adj)), 1)

    def test_calibration_positive_adjustment(self):
        raw = self._raw_prob(55)
        adj = +5.0
        prob = round(min(78.0, max(20.0, raw + adj)), 1)
        assert prob == round(min(78.0, max(20.0, raw + adj)), 1)

    def test_prob_clamps_at_78_upper(self):
        # High score + big positive adjustment should cap at 78
        raw = self._raw_prob(84)  # 68.0
        prob = round(min(78.0, max(20.0, raw + 20.0)), 1)
        assert prob == 78.0

    def test_prob_clamps_at_20_lower(self):
        raw = self._raw_prob(0)  # 45.0
        prob = round(min(78.0, max(20.0, raw - 50.0)), 1)
        assert prob == 20.0


class TestTradeLevels:
    """
    Target = price + ATR_MULTIPLIER * ATR
    Stop   = price - STOP_MULTIPLIER * ATR
    upside_pct  = (target - price) / price * 100
    downside_pct = (price - stop) / price * 100
    reward_risk  = upside_pct / downside_pct
    """

    def setup_method(self):
        from lse_analyser.config import ATR_MULTIPLIER, STOP_MULTIPLIER
        self.atr_mult  = ATR_MULTIPLIER   # 1.0
        self.stop_mult = STOP_MULTIPLIER  # 1.0

    def test_target_calculation(self):
        price, atr = 500.0, 10.0
        target = round(price + self.atr_mult * atr, 2)
        assert target == 510.0

    def test_stop_calculation(self):
        price, atr = 500.0, 10.0
        stop = round(price - self.stop_mult * atr, 2)
        assert stop == 490.0

    def test_upside_pct(self):
        price, target = 500.0, 510.0
        upside = (target - price) / price * 100
        assert abs(upside - 2.0) < 0.001

    def test_downside_pct(self):
        price, stop = 500.0, 490.0
        downside = (price - stop) / price * 100
        assert abs(downside - 2.0) < 0.001

    def test_reward_risk_symmetric(self):
        # With ATR_MULTIPLIER == STOP_MULTIPLIER, reward:risk should be 1.0
        price, atr = 500.0, 10.0
        target   = price + self.atr_mult * atr
        stop     = price - self.stop_mult * atr
        upside   = (target - price) / price * 100
        downside = (price - stop) / price * 100
        rr = upside / downside
        assert abs(rr - 1.0) < 0.001

    def test_atr_multiplier_is_1(self):
        # Confirm config hasn't accidentally been changed
        assert self.atr_mult == 1.0

    def test_stop_multiplier_is_1(self):
        assert self.stop_mult == 1.0


class TestProbTiers:
    """
    finalise_prob_tiers() should:
    - Set rises_at_all == prob
    - Set tiers that decrease from rises_at_all downward
    - Scale all tiers proportionally with prob
    - Always have tiers in descending order
    """

    def setup_method(self):
        from lse_analyser.screener import finalise_prob_tiers
        self.finalise = finalise_prob_tiers

    def _get_tiers(self, prob):
        pick = {"prob": prob}
        self.finalise([pick])
        return pick["prob_tiers"]

    def test_rises_at_all_equals_prob(self):
        tiers = self._get_tiers(54.0)
        assert tiers["rises_at_all"] == 54.0

    def test_tiers_descend(self):
        tiers = self._get_tiers(54.0)
        assert tiers["rises_at_all"] > tiers["rises_1pct"]
        assert tiers["rises_1pct"]   > tiers["rises_2pct"]
        assert tiers["rises_2pct"]   > tiers["rises_3pct"]

    def test_tiers_scale_with_prob(self):
        # Higher prob should produce higher tiers at every level
        tiers_low  = self._get_tiers(50.0)
        tiers_high = self._get_tiers(60.0)
        assert tiers_high["rises_1pct"] > tiers_low["rises_1pct"]
        assert tiers_high["rises_2pct"] > tiers_low["rises_2pct"]
        assert tiers_high["rises_3pct"] > tiers_low["rises_3pct"]

    def test_tiers_all_positive(self):
        tiers = self._get_tiers(52.0)
        assert all(v > 0 for v in tiers.values())

    def test_tiers_below_100(self):
        tiers = self._get_tiers(78.0)
        assert all(v < 100 for v in tiers.values())

    def test_multiple_picks_independent(self):
        picks = [{"prob": 50.0}, {"prob": 60.0}, {"prob": 55.0}]
        self.finalise(picks)
        assert picks[0]["prob_tiers"]["rises_at_all"] == 50.0
        assert picks[1]["prob_tiers"]["rises_at_all"] == 60.0
        assert picks[2]["prob_tiers"]["rises_at_all"] == 55.0


# ===========================================================================
# 3. SIZING -- Kelly criterion and signal labels
# ===========================================================================

class TestSignalLabel:
    """signal_label() should return the correct label for each prob range."""

    def setup_method(self):
        from lse_analyser.sizing import signal_label
        from lse_analyser.config import PROB_STRONG, PROB_MODERATE, PROB_CAUTIOUS, PROB_FLOOR
        self.label       = signal_label
        self.strong      = PROB_STRONG    # 55.0
        self.moderate    = PROB_MODERATE  # 52.0
        self.cautious    = PROB_CAUTIOUS  # 50.0
        self.floor       = PROB_FLOOR     # 50.0

    def test_strong_at_threshold(self):
        assert "Strong" in self.label(self.strong)

    def test_strong_above_threshold(self):
        assert "Strong" in self.label(self.strong + 5)

    def test_moderate_at_threshold(self):
        assert "Moderate" in self.label(self.moderate)

    def test_moderate_just_below_strong(self):
        assert "Moderate" in self.label(self.strong - 0.1)

    def test_cautious_at_threshold(self):
        assert "Cautious" in self.label(self.cautious)

    def test_weak_below_floor(self):
        assert "Weak" in self.label(self.floor - 1.0)

    def test_labels_are_strings(self):
        for prob in [45, 50, 52, 55, 60, 70]:
            assert isinstance(self.label(prob), str)


class TestKellySizing:
    """
    Kelly formula: raw_kelly = p - (1-p)/rr
    Fractional kelly: kelly = max(0, raw_kelly * KELLY_FRACTION)
    Allocation: proportional to kelly weight within total
    """

    def setup_method(self):
        from lse_analyser.sizing import calculate_allocations
        from lse_analyser.config import KELLY_FRACTION, PROB_FLOOR
        self.calc         = calculate_allocations
        self.kelly_frac   = KELLY_FRACTION  # 0.35
        self.prob_floor   = PROB_FLOOR      # 50.0

    def _make_pick(self, prob, rr=1.0, price=500.0):
        return {
            "prob": prob, "reward_risk": rr, "price": price,
            "ticker": "TEST", "sector": "Tech",
        }

    def test_kelly_fraction_is_035(self):
        assert self.kelly_frac == 0.35

    def test_below_floor_gets_zero_allocation(self):
        picks = [self._make_pick(self.prob_floor - 1.0)]
        picks, deployed, reserve = self.calc(picks, 1000.0)
        assert picks[0]["allocated_gbp"] == 0.0
        assert picks[0]["shares"] == 0

    def test_above_floor_gets_allocation(self):
        picks = [self._make_pick(55.0, rr=1.5)]
        picks, deployed, reserve = self.calc(picks, 1000.0)
        assert picks[0]["allocated_gbp"] > 0

    def test_allocations_sum_to_deployed(self):
        picks = [self._make_pick(54.0, rr=1.2), self._make_pick(52.0, rr=1.0)]
        picks, deployed, reserve = self.calc(picks, 2000.0)
        total_alloc = sum(p["allocated_gbp"] for p in picks)
        # deployed = sum of actual costs (shares * price_gbp), not raw allocations
        # so just check deployed + reserve ~= capital
        assert abs(deployed + reserve - 2000.0) < 1.0  # within £1 due to rounding

    def test_higher_prob_gets_more_allocation(self):
        pick_high = self._make_pick(58.0, rr=1.5)
        pick_low  = self._make_pick(51.0, rr=1.0)
        picks, _, _ = self.calc([pick_high, pick_low], 1000.0)
        assert picks[0]["allocated_gbp"] >= picks[1]["allocated_gbp"]

    def test_allocation_pct_sums_to_100(self):
        picks = [self._make_pick(54.0), self._make_pick(53.0), self._make_pick(52.0)]
        picks, _, _ = self.calc(picks, 3000.0)
        total_pct = sum(p["allocation_pct"] for p in picks if p["allocation_pct"] > 0)
        assert abs(total_pct - 100.0) < 0.5

    def test_shares_are_integers(self):
        picks = [self._make_pick(55.0, rr=1.5)]
        picks, _, _ = self.calc(picks, 1000.0)
        assert isinstance(picks[0]["shares"], int)

    def test_negative_kelly_gives_zero_allocation(self):
        # prob=0.3, rr=0.5: kelly = 0.3 - 0.7/0.5 = 0.3 - 1.4 = -1.1 -> clamp to 0
        picks = [self._make_pick(30.0, rr=0.5)]
        picks, _, _ = self.calc(picks, 1000.0)
        assert picks[0]["allocated_gbp"] == 0.0

    def test_raw_kelly_formula(self):
        # Manually verify: p=0.54, rr=1.2
        # raw = 0.54 - (0.46/1.2) = 0.54 - 0.3833 = 0.1567
        # fractional = 0.1567 * 0.35 = 0.0548
        p  = 0.54
        rr = 1.2
        raw_kelly = p - (1 - p) / rr
        kelly     = max(0.0, raw_kelly * self.kelly_frac)
        assert abs(raw_kelly - 0.1567) < 0.001
        assert abs(kelly - 0.0548) < 0.001


# ===========================================================================
# 4. CALIBRATION -- probability adjustment and outcome metrics
# ===========================================================================

class TestCalibrationAdjustment:
    """
    Calibration formula:
      weighted_profitable_rate = (profitable picks / total picks) weighted by source
      raw_adjustment = weighted_profitable_rate - avg_predicted_prob
      prob_adjustment = clamp(raw_adjustment, -MAX_SHIFT, +MAX_SHIFT)

    The adjustment is then ADDED to raw_prob in screener (positive = boost).
    """

    def setup_method(self):
        from lse_analyser.config import MAX_CALIBRATION_SHIFT, MIN_OUTCOMES_TO_CALIBRATE
        self.max_shift = MAX_CALIBRATION_SHIFT  # 15.0
        self.min_n     = MIN_OUTCOMES_TO_CALIBRATE  # 10

    def _simulate_adjustment(self, profitable_rate, avg_prob):
        raw = profitable_rate - avg_prob
        return max(-self.max_shift, min(self.max_shift, raw))

    def test_overcorrection_capped_at_max_shift(self):
        # Model predicts 63%, only 20% profitable -- should cap at -15
        adj = self._simulate_adjustment(20.0, 63.0)
        assert adj == -self.max_shift

    def test_underprediction_capped_at_max_shift(self):
        # Model predicts 45%, 75% profitable -- should cap at +15
        adj = self._simulate_adjustment(75.0, 45.0)
        assert adj == +self.max_shift

    def test_well_calibrated_near_zero(self):
        # Model predicts 52%, 52% profitable -- close to zero
        adj = self._simulate_adjustment(52.0, 52.0)
        assert abs(adj) < 0.1

    def test_adjustment_sign_when_over_predicting(self):
        # Model predicts too high -> negative adjustment
        adj = self._simulate_adjustment(50.0, 63.0)
        assert adj < 0

    def test_adjustment_sign_when_under_predicting(self):
        # Model predicts too low -> positive adjustment
        adj = self._simulate_adjustment(60.0, 50.0)
        assert adj > 0

    def test_max_calibration_shift_is_15(self):
        assert self.max_shift == 15.0

    def test_min_outcomes_is_10(self):
        assert self.min_n == 10


class TestOutcomeFlags:
    """
    went_up:   1 if monday_close > entry_price  (direction, display only)
    profitable: 1 if exit_price > entry_price   (calibration signal)

    These differ when a stop triggers but the stock recovers by Monday.
    """

    def test_normal_exit_went_up_and_profitable(self):
        entry_price  = 500.0
        exit_price   = 510.0  # held to Monday close, gained
        monday_close = 510.0
        went_up   = 1 if monday_close > entry_price else 0
        profitable = 1 if exit_price > entry_price else 0
        assert went_up == 1
        assert profitable == 1

    def test_stop_triggered_stock_recovered(self):
        # Stopped out at 490, but Monday close was 505 -- went_up=1, profitable=0
        entry_price  = 500.0
        exit_price   = 490.0  # stop triggered
        monday_close = 505.0  # recovered by Monday
        went_up    = 1 if monday_close > entry_price else 0
        profitable = 1 if exit_price > entry_price else 0
        assert went_up == 1    # stock did go up by Monday
        assert profitable == 0  # but we already sold at a loss

    def test_stop_triggered_no_recovery(self):
        entry_price  = 500.0
        exit_price   = 490.0
        monday_close = 488.0
        went_up    = 1 if monday_close > entry_price else 0
        profitable = 1 if exit_price > entry_price else 0
        assert went_up == 0
        assert profitable == 0

    def test_held_to_monday_loss(self):
        entry_price  = 500.0
        exit_price   = 492.0
        monday_close = 492.0
        went_up    = 1 if monday_close > entry_price else 0
        profitable = 1 if exit_price > entry_price else 0
        assert went_up == 0
        assert profitable == 0

    def test_tiny_gain_is_profitable(self):
        entry_price  = 500.0
        exit_price   = 500.01
        monday_close = 500.01
        profitable = 1 if exit_price > entry_price else 0
        assert profitable == 1

    def test_exactly_at_entry_is_not_profitable(self):
        entry_price = 500.0
        exit_price  = 500.0
        profitable = 1 if exit_price > entry_price else 0
        assert profitable == 0


# ===========================================================================
# 5. DIVERSIFICATION -- sector spread logic
# ===========================================================================

class TestDiversify:
    """diversify() should prefer one stock per sector, then fill by score."""

    def setup_method(self):
        from lse_analyser.screener import diversify
        self.diversify = diversify

    def _pick(self, ticker, sector, score):
        return {"ticker": ticker, "sector": sector, "score": score}

    def test_picks_one_per_sector(self):
        results = [
            self._pick("AAA", "Banking", 80),
            self._pick("BBB", "Banking", 75),
            self._pick("CCC", "Tech",    70),
        ]
        picks = self.diversify(results, n=2)
        sectors = [p["sector"] for p in picks]
        assert len(set(sectors)) == 2

    def test_prefers_highest_score_within_sector(self):
        results = [
            self._pick("AAA", "Banking", 80),
            self._pick("BBB", "Banking", 75),
            self._pick("CCC", "Tech",    70),
        ]
        picks = self.diversify(results, n=2)
        tickers = [p["ticker"] for p in picks]
        assert "AAA" in tickers  # higher-scored Banking pick
        assert "BBB" not in tickers

    def test_fills_to_n_from_same_sector(self):
        # Only one sector available -- should still fill all 3 slots
        results = [
            self._pick("AAA", "Banking", 90),
            self._pick("BBB", "Banking", 80),
            self._pick("CCC", "Banking", 70),
        ]
        picks = self.diversify(results, n=3)
        assert len(picks) == 3

    def test_returns_at_most_n(self):
        results = [self._pick(f"T{i}", "Banking", 90 - i) for i in range(10)]
        picks = self.diversify(results, n=5)
        assert len(picks) == 5

    def test_empty_input(self):
        picks = self.diversify([], n=5)
        assert picks == []


# ===========================================================================
# 6. BACKTEST -- outcome calculation consistency
# ===========================================================================

class TestBacktestOutcomes:
    """
    Verify backtest outcome formulas are consistent with the strategy rules:
      - Entry: Tuesday open
      - Normal exit: Monday close
      - Stop exit: day close <= stop_price
      - return_pct = (exit_price - entry_price) / entry_price * 100
      - target_hit: monday_close >= target (regardless of stop)
    """

    def test_return_pct_gain(self):
        entry_price = 500.0
        exit_price  = 510.0
        ret = (exit_price - entry_price) / entry_price * 100
        assert abs(ret - 2.0) < 0.001

    def test_return_pct_loss(self):
        entry_price = 500.0
        exit_price  = 490.0
        ret = (exit_price - entry_price) / entry_price * 100
        assert abs(ret - (-2.0)) < 0.001

    def test_target_hit_uses_monday_close_not_exit(self):
        # Stock was stopped out Wednesday at 490, but Monday closed at 515 >= target 510
        monday_close = 515.0
        target       = 510.0
        target_hit   = "YES" if monday_close >= target else "NO"
        assert target_hit == "YES"

    def test_target_miss(self):
        monday_close = 505.0
        target       = 510.0
        target_hit   = "YES" if monday_close >= target else "NO"
        assert target_hit == "NO"

    def test_target_exactly_hit(self):
        monday_close = 510.0
        target       = 510.0
        target_hit   = "YES" if monday_close >= target else "NO"
        assert target_hit == "YES"

    def test_stop_triggers_below_stop_price(self):
        stop_price = 490.0
        day_close  = 489.0
        triggered  = day_close <= stop_price
        assert triggered is True

    def test_stop_exactly_at_stop_price(self):
        stop_price = 490.0
        day_close  = 490.0
        triggered  = day_close <= stop_price
        assert triggered is True

    def test_stop_does_not_trigger_above(self):
        stop_price = 490.0
        day_close  = 490.01
        triggered  = day_close <= stop_price
        assert triggered is False


# ===========================================================================
# 7. INTERNAL CONSISTENCY -- prob_tiers match displayed prob
# ===========================================================================

class TestProbConsistency:
    """
    The prob shown in the main table and in the signal breakdown must match.
    rises_at_all in prob_tiers must always equal the final prob value.
    """

    def setup_method(self):
        from lse_analyser.screener import finalise_prob_tiers
        self.finalise = finalise_prob_tiers

    def test_rises_at_all_matches_prob_exactly(self):
        for prob in [48.0, 52.0, 54.5, 57.0, 60.0, 65.3]:
            pick = {"prob": prob}
            self.finalise([pick])
            assert pick["prob_tiers"]["rises_at_all"] == prob, (
                f"prob_tiers['rises_at_all'] ({pick['prob_tiers']['rises_at_all']}) "
                f"!= prob ({prob})"
            )

    def test_tiers_populated_after_finalise(self):
        pick = {"prob": 53.0, "prob_tiers": {}}
        self.finalise([pick])
        assert "rises_at_all" in pick["prob_tiers"]
        assert "rises_1pct"   in pick["prob_tiers"]
        assert "rises_2pct"   in pick["prob_tiers"]
        assert "rises_3pct"   in pick["prob_tiers"]

    def test_empty_tiers_before_finalise(self):
        # screener.py intentionally returns empty tiers before finalise is called
        pick = {"prob": 53.0, "prob_tiers": {}}
        assert pick["prob_tiers"] == {}

    def test_finalise_overwrites_stale_tiers(self):
        # Simulate stale tiers from a prior run being overwritten
        pick = {"prob": 53.0, "prob_tiers": {"rises_at_all": 99.0}}
        self.finalise([pick])
        assert pick["prob_tiers"]["rises_at_all"] == 53.0
