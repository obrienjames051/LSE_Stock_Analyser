"""
screener.py
-----------
Core screening logic: data fetching, event/volume filters,
technical scoring, and sector diversification.
"""

import warnings
warnings.filterwarnings("ignore")

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator, SMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator

from .config import (
    LOOKBACK_DAYS, ATR_MULTIPLIER, STOP_MULTIPLIER, LIMIT_BUFFER,
    TOP_N, MIN_AVG_VOLUME_GBP,
)
from .utils import silent


def fetch_data(ticker: str):
    """Download LOOKBACK_DAYS of daily OHLCV data. Returns None on failure."""
    try:
        with silent():
            df = yf.download(ticker, period=f"{LOOKBACK_DAYS}d", interval="1d",
                             progress=False, auto_adjust=True)
        if df is None or len(df) < 30:
            return None
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        df.dropna(inplace=True)
        return df
    except Exception:
        return None


def fetch_ticker_info(ticker: str) -> dict:
    """Return next earnings and ex-dividend dates for a ticker."""
    try:
        t   = yf.Ticker(ticker)
        cal = t.calendar
        earnings_date = exdiv_date = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                earnings_date = pd.to_datetime(ed[0] if isinstance(ed, list) else ed)
            dd = cal.get("Ex-Dividend Date")
            if dd:
                exdiv_date = pd.to_datetime(dd)
        elif isinstance(cal, pd.DataFrame):
            if "Earnings Date" in cal.index:
                earnings_date = pd.to_datetime(cal.loc["Earnings Date"].iloc[0])
            if "Ex-Dividend Date" in cal.index:
                exdiv_date = pd.to_datetime(cal.loc["Ex-Dividend Date"].iloc[0])
        return {"earnings_date": earnings_date, "exdiv_date": exdiv_date}
    except Exception:
        return {"earnings_date": None, "exdiv_date": None}


def has_event_in_window(ticker: str, days: int = 7) -> tuple:
    """Return (True, reason) if an earnings/ex-div event falls within the next N days."""
    info   = fetch_ticker_info(ticker)
    now    = datetime.now()
    cutoff = now + timedelta(days=days)
    for key, label in [("earnings_date", "earnings"), ("exdiv_date", "ex-dividend")]:
        date = info.get(key)
        if date is not None:
            try:
                dt = date.to_pydatetime().replace(tzinfo=None)
                if now <= dt <= cutoff:
                    return True, f"{label} on {dt.strftime('%d %b')}"
            except Exception:
                pass
    return False, ""


def passes_volume_filter(df) -> bool:
    """Return True if 20-day avg daily traded value exceeds MIN_AVG_VOLUME_GBP."""
    recent        = df.tail(20)
    avg_value_gbp = (recent["close"] * recent["volume"] / 100).mean()
    return avg_value_gbp >= MIN_AVG_VOLUME_GBP


def score_ticker(ticker: str, sector: str, prob_adjustment: float = 0.0):
    """
    Score a ticker using technical indicators and compute trade levels.
    Returns a result dict, or None if the ticker fails filters/data fetch.
    """
    df = fetch_data(ticker)
    if df is None or len(df) < 30:
        return None
    if not passes_volume_filter(df):
        return None

    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

    try:
        rsi       = RSIIndicator(close, window=14).rsi()
        macd_obj  = MACD(close)
        macd_hist = macd_obj.macd_diff()
        bb        = BollingerBands(close, window=20, window_dev=2)
        bb_pct    = bb.bollinger_pband()
        ema20     = EMAIndicator(close, window=20).ema_indicator()
        ema50     = EMAIndicator(close, window=50).ema_indicator()
        sma200    = SMAIndicator(close, window=min(200, len(close)-1)).sma_indicator()
        atr       = AverageTrueRange(high, low, close, window=14).average_true_range()
        obv       = OnBalanceVolumeIndicator(close, vol).on_balance_volume()
        stoch     = StochasticOscillator(high, low, close, window=14, smooth_window=3)
        stoch_k   = stoch.stoch()
        stoch_d   = stoch.stoch_signal()

        c, r       = float(close.iloc[-1]), float(rsi.iloc[-1])
        mh, mh_p   = float(macd_hist.iloc[-1]), float(macd_hist.iloc[-2])
        bp         = float(bb_pct.iloc[-1])
        e20, e50, s200 = float(ema20.iloc[-1]), float(ema50.iloc[-1]), float(sma200.iloc[-1])
        atr_v      = float(atr.iloc[-1])
        sk, sd     = float(stoch_k.iloc[-1]), float(stoch_d.iloc[-1])
        obv_slope  = (float(obv.iloc[-1]) - float(obv.iloc[-10])) / (abs(float(obv.iloc[-10])) + 1)
        mom5       = (c - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

        score, signals = 0, []

        if 40 <= r <= 65:
            score += 20; signals.append(f"RSI {r:.0f} (bullish range)")
        elif r < 35:
            score += 10; signals.append(f"RSI {r:.0f} (oversold -- potential reversal)")

        if mh > 0 and mh_p < 0:
            score += 25; signals.append("MACD histogram bullish crossover")
        elif mh > mh_p and mh > 0:
            score += 15; signals.append("MACD histogram rising")

        if 0.2 <= bp <= 0.5:
            score += 15; signals.append(f"BB %B {bp:.2f} (below mid, room to rise)")
        elif bp < 0.2:
            score += 8;  signals.append(f"BB %B {bp:.2f} (near lower band, bounce candidate)")

        if e20 > e50 > s200:
            score += 20; signals.append("EMA20 > EMA50 > SMA200 (fully aligned bullish)")
        elif e20 > e50:
            score += 10; signals.append("EMA20 > EMA50 (short-term bullish)")

        if sk > sd and 20 < sk < 80:
            score += 10; signals.append(f"Stochastic bullish ({sk:.0f}/{sd:.0f})")

        if obv_slope > 0.01:
            score += 10; signals.append("OBV rising (institutional accumulation)")

        if 0.5 <= mom5 <= 4.0:
            score += 10; signals.append(f"5-day momentum +{mom5:.1f}%")
        elif -1.0 <= mom5 < 0:
            score += 5;  signals.append(f"Slight dip {mom5:.1f}% -- potential entry point")

        target       = round(c + ATR_MULTIPLIER * atr_v, 2)
        stop         = round(c - STOP_MULTIPLIER * atr_v, 2)
        limit        = round(target * LIMIT_BUFFER, 2)
        upside_pct   = (target - c) / c * 100
        downside_pct = (c - stop) / c * 100
        raw_prob     = 35.0 + (score / 110) * 40
        prob         = round(min(78.0, max(20.0, raw_prob - prob_adjustment)), 1)

        return {
            "ticker":       ticker.replace(".L", ""),
            "sector":       sector,
            "score":        score,
            "price":        c,
            "target":       target,
            "stop":         stop,
            "limit":        limit,
            "upside_pct":   upside_pct,
            "downside_pct": downside_pct,
            "prob":         prob,
            "signals":      signals,
            "atr":          round(atr_v, 4),
            "reward_risk":  round(upside_pct / downside_pct, 2) if downside_pct > 0 else 0,
        }
    except Exception:
        return None


def diversify(results: list, n: int = TOP_N) -> list:
    """Select top n picks ensuring one stock per sector where possible."""
    seen, picks = set(), []
    for r in results:
        if r["sector"] not in seen:
            picks.append(r); seen.add(r["sector"])
        if len(picks) == n:
            return picks
    for r in results:
        if r not in picks:
            picks.append(r)
        if len(picks) == n:
            return picks
    return picks
