# LSE Stock Analyser

A Python tool that screens London Stock Exchange stocks using technical analysis to identify the five most likely to rise over the next seven calendar days. For each pick it suggests a target price, stop-loss, take-profit limit, and a position size based on your available capital.

> **Disclaimer:** This is a quantitative screening tool only. All outputs are model estimates and do not constitute financial advice. Past technical patterns do not guarantee future results. Always apply your own judgement, and consider consulting a regulated financial adviser before trading.

---

## Requirements

Install dependencies before running:

```bash
pip install yfinance pandas numpy ta rich
```

---

## How to run

```bash
python lse_stock_analyser.py
```

Run it from the same folder every time — this is where it will create and read back `lse_screener_log.csv`.

When prompted, enter the total amount of capital (in £) you are willing to invest across all five picks. The script will suggest how to split this between the picks based on their signal quality.

---

## Recommended usage

**Run once a week, before the market opens Monday morning (8am).** The prediction window is seven calendar days, so weekly runs give you clean, non-overlapping rounds of picks. Running more frequently would create overlapping positions and muddy the calibration data. Sunday evening works equally well — the script only uses historical closing data and does not need the market to be open.

Each time you run the script it will automatically:
1. Resolve any picks from seven or more days ago by fetching their actual outcome prices
2. Update the CSV log with those outcomes
3. Use the accumulated history to self-calibrate its probability estimates before showing you today's picks

After roughly four to six weeks (ten or more resolved picks) the self-calibration will start meaningfully adjusting the model's outputs based on its real track record.

### Live mode vs. Preview mode

The first thing the script asks on every run is which mode to use:

- **Live mode (L)** — the full run. Outcome back-filling and CSV saving are both active. Use this for your weekly run.
- **Preview mode (P)** — everything runs as normal (screening, calibration report, tables, position sizing) but nothing is written to the CSV. Use this if you want to check the current picks mid-week without it counting as your weekly log entry.

Keeping these two modes separate ensures the calibration data stays clean — one set of picks per week, each with a full seven days to resolve before the next run is logged.

---

## Understanding the output

### Startup — Performance Report

Before the main results, the script prints a historical performance panel showing:

- **Hit rate** — the percentage of past picks where the stock actually reached its target price
- **Average actual return** — the mean percentage gain or loss across all resolved picks
- **Calibration status** — whether the model has been over- or under-confident, and by how much. If the model has historically predicted 60% probability but only hit 45% of the time, it will say so and automatically reduce today's probabilities to compensate.

This panel only appears once you have resolved picks in the log. On your first few runs it will say calibration is not yet active.

---

### Main Results Table

| Column | What it means |
|---|---|
| **Ticker** | LSE stock symbol |
| **Sector** | The sector the stock belongs to. The five picks will always span different sectors to avoid concentration risk |
| **Price (p)** | Last closing price, in **pence**. Divide by 100 for £ (e.g. 2340p = £23.40) |
| **Target (p)** | The price the model expects the stock could reach within seven days, in pence |
| **Upside** | The percentage gain if the stock reaches its target price |
| **Prob.** | The model's estimated probability that the target will be reached, after calibration adjustment. Colour-coded: green ≥ 60%, yellow ≥ 45%, red below 45% |
| **Stop (p)** | The stop-loss price to include on your buy order. If the stock falls to this level, your broker will automatically sell, limiting your loss |
| **Limit (p)** | The take-profit limit to set after buying. Your broker will automatically sell when the stock reaches this price, locking in the gain without you having to monitor it |
| **R:R** | Reward:Risk ratio — how much you stand to gain relative to how much you risk. An R:R of 2.0 means for every £1 you risk losing (to the stop-loss), you stand to make £2 if the target is hit. **Aim for ≥ 1.5.** Below 1.0 means you are risking more than you could gain |
| **Score** | The model's internal signal quality score, built up by adding points for each bullish technical indicator. The maximum is approximately **110**. It is most useful for comparing picks against each other within the same run rather than across different weeks |

---

### Detailed Signal Breakdown

Printed below the main table, this lists the specific technical indicators that fired for each stock — for example "MACD histogram bullish crossover" or "EMA20 > EMA50 > SMA200 (fully aligned bullish)". It also shows the ATR value (a measure of recent volatility used to set the target and stop distances) and the stop distance as a percentage below your entry price.

---

### Position Sizing Table

| Column | What it means |
|---|---|
| **Probability** | Same as the main table |
| **Allocation %** | The percentage of your total entered capital suggested for this stock |
| **Invest (£)** | The suggested pound amount to put into this stock — **this is the primary figure to act on** |
| **Price (£)** | Current share price in pounds |
| **~Shares** | Approximate number of whole shares the investment amount would buy. This is advisory — fractional shares are fine if your broker supports them |
| **Note** | Signal quality and any relevant flags — see below |

**Note field values:**
- `★ Strong signal — favoured` — probability ≥ 60%, Kelly sizing has weighted it heavily
- `Moderate signal` — probability between 50–59%
- `Weak signal — small stake only` — probability below 50% but above the minimum threshold; the model still sees something but with less confidence
- `★ Strong signal — favoured  ·  fractional share` (or similar) — the pick is good but the suggested investment is less than the cost of one whole share. This is fine if your broker supports fractional shares
- `⚠ Below confidence threshold — skip` — the probability is too low for the model to recommend putting any capital in. The stock appeared in the top five due to its technical score, but the probability estimate fell below the minimum threshold (default: 40%)

**Capital summary line:** Shows your total entered capital, the suggested amount to deploy across all picks, and how much to keep in reserve. Not all capital needs to be deployed — the model will sometimes hold back a significant portion if the picks are collectively weak.

---

## How the self-calibration works

Every pick is saved to `lse_screener_log.csv`. Seven days later, when you run the script again, it automatically fetches each stock's actual closing price and records:

- `outcome_price_p` — the actual price on day 7 (pence)
- `outcome_hit` — YES or NO, depending on whether the target was reached
- `outcome_return_pct` — the actual percentage change from entry to outcome
- `outcome_notes` — a brief auto-generated description (e.g. "Target reached. +3.2%" or "Stop-loss triggered. -1.8%")

Once ten or more picks have been resolved, the calibration engine calculates the gap between the model's average predicted probability and the real hit rate. This gap (the "bias") is then subtracted from all future probability outputs, capped at ±15 percentage points. The model effectively develops a memory of its own accuracy and corrects for it over time.

The calibration uses only the most recent 50 resolved picks, so very old results from the early weeks — before the model was calibrated — do not drag on the calculation indefinitely.

---

## Key parameters

These are at the top of the script and can be adjusted if needed:

| Parameter | Default | What it controls |
|---|---|---|
| `ATR_MULTIPLIER` | 1.5 | How far above the current price the target is set (in multiples of recent volatility) |
| `STOP_MULTIPLIER` | 1.0 | How far below the current price the stop-loss is set |
| `MIN_AVG_VOLUME_GBP` | £500,000 | Minimum average daily traded value — filters out illiquid stocks with unreliable signals |
| `PROB_FLOOR` | 40.0 | Minimum probability for a pick to receive any capital allocation |
| `KELLY_FRACTION` | 0.25 | Conservative scaling applied to the Kelly position sizing formula (25% Kelly) |
| `MIN_OUTCOMES_TO_CALIBRATE` | 10 | Number of resolved picks needed before calibration activates |
| `CALIBRATION_WINDOW` | 50 | Number of most recent resolved picks used for calibration |
| `MAX_CALIBRATION_SHIFT` | 15.0 | Maximum probability adjustment (pp) the calibration can apply in either direction |

---

## Filters applied during screening

- **Volume filter** — removes stocks whose average daily traded value is below £500,000. Low-volume stocks have less reliable price signals and wider bid/ask spreads.
- **Sector diversification** — ensures the five picks span different sectors (energy, banking, pharma, etc.), so a single sector shock does not affect all positions simultaneously.
- **Event filter** — removes any stock with an earnings announcement or ex-dividend date falling within the next seven days. These events can cause large price moves that have nothing to do with the technical setup, making the model's predictions unreliable for those stocks.

---

## The CSV log

`lse_screener_log.csv` is created automatically on first run in the same folder as the script. It stores every pick made, along with the auto-filled outcome columns once seven days have passed. You do not need to edit it manually.

If you want to review your historical performance outside the script, you can open it in Excel or any spreadsheet application.

---

## Important notes

- **All prices in the tables are in pence, not pounds.** A price of 2340 means £23.40. The exception is the Position Sizing table, where the "Price (£)" and "Invest (£)" columns are already converted to pounds.
- **Data comes from Yahoo Finance** and is typically delayed by around 15 minutes for LSE stocks. The script is designed for end-of-day use, not intraday trading.
- **The script screens approximately 65 FTSE 100/250 tickers.** If you see a Yahoo Finance error mentioning a ticker is "possibly delisted", you can simply remove that ticker from the `TICKERS` dictionary at the top of the script.
- **The probability figures are model estimates**, not guaranteed odds. They reflect the historical hit rate of similar technical setups, adjusted for the model's own track record over time.
