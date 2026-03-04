# LSE Stock Analyser v5.1

A Python tool that screens London Stock Exchange stocks using technical analysis and news sentiment to identify the five most likely to rise over the next seven calendar days. For each pick it suggests a target price, stop-loss, take-profit limit, and a position size based on your available capital.

> **Disclaimer:** This is a quantitative screening tool only. All outputs are model estimates and do not constitute financial advice. Past technical patterns do not guarantee future results. Always apply your own judgement, and consider consulting a regulated financial adviser before trading.

---

## Requirements

Install dependencies before running:

```bash
pip install yfinance pandas numpy ta rich lxml requests html5lib vaderSentiment
```

---

## How to run

```bash
cd LSE_Stock_Analyser
python3 main.py
```

Run it from the `LSE_Stock_Analyser` folder every time — this is where it will create and read back `lse_screener_log.csv` and `ftse_tickers.json`.

When prompted, enter the total amount of capital (in £) you are willing to invest across all five picks. The script will suggest how to split this between the picks based on their signal quality.

---

## Setup

### 1. Install dependencies

```bash
pip install yfinance pandas numpy ta rich lxml requests html5lib vaderSentiment
```

### 2. Get a free NewsAPI key

Sign up at [newsapi.org](https://newsapi.org) — it only requires an email address. Once registered, copy your API key from the dashboard.

### 3. Create your .env file

Create a file named `.env` in the `LSE_Stock_Analyser/` folder (the same folder as `main.py`) containing:

```
NEWSAPI_KEY=your_api_key_here
```

No quotes, no spaces around the equals sign. This file is listed in `.gitignore` and will never be uploaded to GitHub.

---

## Project structure

```
LSE_Stock_Analyser/
├── main.py                  ← entry point
├── .env                     ← your API key (gitignored, never uploaded)
├── .env.example             ← template showing the required format
├── lse_screener_log.csv     ← auto-created on first Live run
├── ftse_tickers.json        ← auto-created after first Wikipedia fetch
└── lse_analyser/            ← the package
    ├── config.py            ← all tunable parameters
    ├── tickers.py           ← FTSE constituent list management
    ├── screener.py          ← data fetching, filters, technical scoring
    ├── news.py              ← NewsAPI fetching and VADER sentiment analysis
    ├── calibration.py       ← outcome back-filling and self-calibration
    ├── sizing.py            ← Kelly position sizing
    ├── csv_log.py           ← CSV saving
    ├── display.py           ← table rendering
    └── history.py           ← history viewer
```

---

## Recommended usage

**Run once a week on Sunday evening or after 16:45 on Friday.** The prediction window is seven calendar days, so weekly runs give you clean, non-overlapping rounds of picks. Running more frequently would create overlapping positions and muddy the calibration data.

The 16:45 timing matters because Yahoo Finance data for LSE stocks is delayed by approximately 15 minutes, and the LSE closes at 16:30. Running before 16:45 risks using incomplete data where the "closing" price is actually a mid-session price rather than the true close.

Each time you run the script it will automatically:
1. Resolve any picks from seven or more days ago by fetching their actual outcome prices
2. Update the CSV log with those outcomes
3. Use the accumulated history to self-calibrate its probability estimates
4. Screen all ~350 FTSE tickers technically, then run news sentiment on the top candidates

After roughly four to six weeks (ten or more resolved picks) the self-calibration will start meaningfully adjusting the model's outputs based on its real track record.

### Monday morning workflow

After running the script on Sunday evening, check each pick on Monday morning after the market opens at 8am. Stocks sometimes gap up or down over the weekend, which can make a pick's entry less attractive:

- If a stock has gapped **significantly upward**, the predicted upside may already be consumed — consider skipping it
- If a stock has gapped **significantly downward** and is already near or below its stop price, the thesis has been invalidated before you have even entered — skip it
- If the gap is **negligible**, the pick is still valid and you can place your limit buy order as planned

As a general guide, a gap of more than ±1% warrants reassessment of the R:R before entering. A gap beyond the stop price is an automatic skip.

**If a major unexpected macro event occurs between your Sunday run and Monday morning** (geopolitical shock, surprise interest rate decision, etc.), consider skipping the week entirely. The model's signals were calculated before the event and may no longer reflect market reality.

### Selling

Place your stop-loss and take-profit limit orders when you buy. These will execute automatically during the week. For any positions that have not triggered by **Friday before 4:30pm close**, sell manually. This keeps your outcome data consistent with the model's seven-day measurement window and removes weekend gap risk on the exit side.

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
| **Score** | The model's internal signal quality score, combining technical indicators and news sentiment adjustment. The technical maximum is approximately **110**; news can add or subtract up to **15** further points |
| **News** | The overall sentiment of recent headlines for this stock: Very positive, Positive, Neutral, Negative, or Very negative. Shown as -- if no news data was available |

---

### Detailed Signal Breakdown

Printed below the main table, this lists the specific technical indicators that fired for each stock — for example "MACD histogram bullish crossover" or "EMA20 > EMA50 > SMA200 (fully aligned bullish)". It also shows:

- The ATR value (a measure of recent volatility used to set the target and stop distances)
- The stop distance as a percentage below your entry price
- The news sentiment score and article count
- The actual headlines that were used to calculate the sentiment score

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
- `Weak signal — small stake only` — probability below 50% but above the minimum threshold
- `★ Strong signal — favoured  ·  fractional share` — the pick is good but the suggested investment is less than one whole share
- `⚠ Below confidence threshold — skip` — the probability is too low for the model to recommend any capital

**Capital summary line:** Shows your total entered capital, the suggested amount to deploy across all picks, and how much to keep in reserve.

---

## Run modes

The first thing the script asks on every run is which mode to use:

- **Live mode (L)** — the full run. Outcome back-filling and CSV saving are both active. Use this for your weekly run.
- **Preview mode (P)** — everything runs as normal (screening, news sentiment, calibration report, tables, position sizing) but nothing is written to the CSV. Use this if you want to check the current picks mid-week without it counting as your weekly log entry.
- **History mode (H)** — skips the screener entirely and lets you browse past runs. You will be shown a numbered list of all previous Live runs with their hit rates, and can select any one to see the full predictions table alongside the actual outcomes.

Keeping Live and Preview separate ensures the calibration data stays clean — one set of picks per week, each with a full seven days to resolve before the next run is logged.

---

## Ticker universe

On each run the script fetches the current FTSE 100 and FTSE 250 constituent lists from Wikipedia, giving a universe of approximately 350 stocks. Each index is cached independently in `ftse_tickers.json`, so a failed fetch for one index never overwrites the cached data for the other.

**Three-tier fallback (per index):**
1. **Wikipedia (live)** — fetched fresh on every run, updates that index's section in the JSON cache
2. **JSON cache** — used if Wikipedia is unavailable for that index; shows the date it was last updated
3. **Emergency bootstrap** — a hardcoded list of ~20 major FTSE 100 stocks, used only if both Wikipedia and the cache are unavailable

The source being used is shown at startup in green (live), yellow (cache), or red (emergency bootstrap).

Occasionally Yahoo Finance will report that a ticker is not found — this is normal for a small number of FTSE 250 stocks and does not affect the results.

---

## How news sentiment works

After the technical screening pass, the top 20 candidates by score are passed to the news analysis module:

1. **Headlines are fetched** from NewsAPI for each candidate using the company name as a search query
2. **Each headline is scored** individually using VADER sentiment analysis (-1.0 to +1.0)
3. **Scores are weighted by recency** — articles from today carry full weight (1.0), scaling down to 0.3 for the oldest articles in the window
4. **Volume has a mild amplifying effect** — more articles nudge the score slightly further in the same direction, but a single extreme headline (e.g. a major profit warning) still carries meaningful weight on its own
5. **The final score adjusts the pick's technical score** by up to ±15 points and its probability by up to ±10 percentage points

**Expanding fallback:** If fewer than 5 of the top 20 candidates have neutral or better news sentiment, the screener automatically checks the next 10 candidates (positions 21–30), then the next 10 after that, and so on, until 5 viable picks are found or the list is exhausted.

If NewsAPI is unavailable, tickers pass through unchanged — a NewsAPI outage never prevents the screener from producing results.

---

## How the self-calibration works

Every pick is saved to `lse_screener_log.csv`. Seven days later, when you run the script again, it automatically fetches each stock's actual closing price and records:

- `outcome_price_p` — the actual price on day 7 (pence)
- `outcome_hit` — YES or NO, depending on whether the target was reached
- `outcome_return_pct` — the actual percentage change from entry to outcome
- `outcome_notes` — a brief auto-generated description (e.g. "Target reached. +3.2%" or "Stop-loss triggered. -1.8%")

Once ten or more picks have been resolved, the calibration engine calculates the gap between the model's average predicted probability and the real hit rate. This gap (the "bias") is then subtracted from all future probability outputs, capped at ±15 percentage points. The model effectively develops a memory of its own accuracy and corrects for it over time.

The calibration uses only the most recent 50 resolved picks, so very old results from the early weeks do not drag on the calculation indefinitely.

---

## Key parameters

These are in `lse_analyser/config.py` and can be adjusted if needed:

| Parameter | Default | What it controls |
|---|---|---|
| `ATR_MULTIPLIER` | 1.5 | How far above the current price the target is set (in multiples of recent volatility) |
| `STOP_MULTIPLIER` | 1.0 | How far below the current price the stop-loss is set |
| `MIN_AVG_VOLUME_GBP` | £500,000 | Minimum average daily traded value — filters out illiquid stocks |
| `PROB_FLOOR` | 40.0 | Minimum probability for a pick to receive any capital allocation |
| `KELLY_FRACTION` | 0.25 | Conservative scaling applied to the Kelly position sizing formula (25% Kelly) |
| `MIN_OUTCOMES_TO_CALIBRATE` | 10 | Number of resolved picks needed before calibration activates |
| `CALIBRATION_WINDOW` | 50 | Number of most recent resolved picks used for calibration |
| `MAX_CALIBRATION_SHIFT` | 15.0 | Maximum probability adjustment (pp) the calibration can apply in either direction |
| `NEWS_LOOKBACK_DAYS` | 7 | How many days of news articles to fetch per candidate |
| `NEWS_MAX_SCORE_ADJ` | 15 | Maximum points added or subtracted from the technical score by news sentiment |
| `NEWS_CANDIDATE_COUNT` | 20 | Number of top technical candidates to run news analysis on |
| `NEWS_FALLBACK_BATCH` | 10 | Number of additional candidates to check if fewer than 5 pass sentiment |

---

## Filters applied during screening

- **Volume filter** — removes stocks whose average daily traded value is below £500,000. Low-volume stocks have less reliable price signals and wider bid/ask spreads.
- **Sector diversification** — ensures the five picks span different sectors (energy, banking, pharma, etc.), so a single sector shock does not affect all positions simultaneously.
- **Event filter** — removes any stock with an earnings announcement or ex-dividend date falling within the next seven days. These events can cause large price moves that have nothing to do with the technical setup, making the model's predictions unreliable for those stocks.
- **News sentiment** — recent headlines are analysed for each top candidate. Strong negative sentiment reduces a stock's score and probability; strong positive sentiment increases them.

---

## The CSV log

`lse_screener_log.csv` is created automatically on the first Live run in the same folder as `main.py`. It stores every pick made, along with the auto-filled outcome columns once seven days have passed. You do not need to edit it manually.

If you want to review your historical performance outside the script, you can open it in Excel or any spreadsheet application.

---

## Important notes

- **All prices in the tables are in pence, not pounds.** A price of 2340 means £23.40. The exception is the Position Sizing table, where the "Price (£)" and "Invest (£)" columns are already converted to pounds.
- **Data comes from Yahoo Finance** and is typically delayed by around 15 minutes for LSE stocks. The script is designed for end-of-day use — run after 16:45 to ensure Friday closing prices are fully settled.
- **The script screens approximately 350 FTSE 100/250 tickers**, fetched live from Wikipedia on each run.
- **News sentiment uses the free tier of NewsAPI**, which allows 100 requests per day. Since the screener typically analyses 20–30 candidates per run, this limit is unlikely to be reached in normal weekly use.
- **The probability figures are model estimates**, not guaranteed odds. They reflect the historical hit rate of similar technical setups, adjusted for the model's own track record and current news sentiment.
