# LSE Stock Analyser

A quantitative screening tool for LSE-listed stocks. Scores stocks using technical indicators, company news, sector sentiment, and macro conditions to produce a weekly shortlist of five picks with position sizing and risk management guidance.

---

## Strategy

The programme is designed to be run **Tuesday morning before market open**.

| Parameter | Value |
|-----------|-------|
| **Run day** | Tuesday morning, before open |
| **Data used** | Monday close as the final bar |
| **Entry** | Tuesday open, all 5 picks |
| **Stop price** | Monday close − 1× ATR (displayed before market opens) |
| **Stop monitoring** | Manual — check each day's close Tuesday through Friday |
| **Stop exit** | If daily close ≤ stop price → sell at market before that day's close |
| **Normal exit** | Sell before Monday close (following week) |
| **Upper limits** | None — hold to exit unless stopped |
| **Hard stop orders** | Not used — mental stops only |
| **Baseline return** | +1.119% per pick per week (see RESEARCH.md) |

You can also run the programme on other days (e.g. Friday) in **Preview mode** — it will use all available data up to the current time and display picks, but nothing will be saved.

---

## Modes

| Mode | Key | Description |
|------|-----|-------------|
| **Live** | L | Run full analysis, save picks to CSV, log news data |
| **Preview** | P | Run full analysis, display results, news saved but picks not saved |
| **History** | H | Browse predictions [P] or track actual trades [T] |
| **Backtest** | B | Simulate ~52 weeks of historical picks to bootstrap calibration |
| **Spotlight** | S | Full single-stock analysis (not saved) |

---

## Quick Start

```bash
# Install dependencies
pip install yfinance pandas ta rich python-dotenv

# Add your NewsAPI key (optional but recommended)
cp .env.example .env
# Edit .env and add: NEWSAPI_KEY=your_key_here

# Run
python -m lse_analyser
```

---

## Columns

| Column | Description |
|--------|-------------|
| Price (p) | Current price in pence |
| Target (p) | Predicted upside target (entry + 1× ATR) |
| Upside | % upside to target |
| P(rise) | Model's probability the stock rises at all |
| Stop (p) | Mental stop price (Monday close − 1× ATR) |
| R:R | Reward:risk ratio |
| Score | Technical score + news adjustment |
| Co. News | Sentiment of company-specific headlines |
| Sector News | Sentiment of sector-wide headlines |

All prices are in **pence** — divide by 100 for pounds.

---

## Calibration

The programme self-calibrates using resolved outcomes:

- **Phase 1 backtest** — ~52 weeks of simulated picks using Tuesday open → Monday close. Run once via Backtest mode to bootstrap calibration.
- **Live picks** — accumulate automatically each time you run in Live mode. Outcomes are resolved the following Tuesday morning.
- **Backtest data phases out** once 30+ live picks have been resolved.

The calibration panel shows:
- **Probability adjustment** — how much the model's raw probabilities are being corrected up or down
- **Directional accuracy** — % of live picks where the stock's Monday close exceeded Tuesday's open (independent of stops)
- **Target hit rate** — % of picks that reached the predicted target price

---

## News Logging

Every run (Live or Preview) appends to `lse_news_log.csv`, recording the sentiment scores, headlines, and score adjustments used for each pick. Running in Preview mode throughout the week therefore builds a continuous record of how sentiment shifts day-to-day, independent of whether picks are saved. This data is a pure audit log for future research — it is not read by the programme.

---

## Files

| File | Description |
|------|-------------|
| `lse_screener_log.csv` | Live picks and outcomes |
| `lse_backtest_technical.csv` | Phase 1 backtest results |
| `lse_news_log.csv` | News sentiment audit log |
| `lse_trade_log.csv` | Actual buy/sell trade log |
| `ftse_tickers.json` | Ticker universe (auto-generated) |
| `.env` | API keys (not committed to git) |
| `RESEARCH.md` | Backtesting research and strategy rationale |

---

## Disclaimer

Quantitative screening tool only — **not financial advice**. All probabilities are model estimates. Past patterns do not guarantee future results. Consult a regulated adviser before trading.
