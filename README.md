# LSE Stock Analyser v6.1

A Python tool that screens London Stock Exchange stocks using technical analysis, company news sentiment, and macro/sector intelligence to identify the five most likely to rise over the next seven calendar days. For each pick it suggests a target price, stop-loss, take-profit limit, and a position size based on your available capital.

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
├── main.py                       ← entry point
├── .env                          ← your API key (gitignored, never uploaded)
├── .env.example                  ← template showing the required format
├── lse_screener_log.csv          ← auto-created on first Live run
├── lse_backtest_technical.csv    ← auto-created when Backtest mode is run
├── lse_backtest_news.csv         ← auto-created when Backtest mode is run
├── ftse_tickers.json             ← auto-created after first Wikipedia fetch
└── lse_analyser/                 ← the package
    ├── config.py                 ← all tunable parameters
    ├── tickers.py                ← FTSE constituent list management
    ├── screener.py               ← data fetching, filters, technical scoring
    ├── news.py                   ← company-level NewsAPI and VADER sentiment
    ├── macro.py                  ← macro/sector sentiment and event classification
    ├── backtest.py               ← historical backtesting engine
    ├── calibration.py            ← outcome back-filling and self-calibration
    ├── sizing.py                 ← Kelly position sizing
    ├── csv_log.py                ← CSV saving
    ├── display.py                ← table rendering
    └── history.py                ← history viewer
```

---

## Recommended usage

**Run at 7:30am on Monday morning.** This captures any news that broke over the weekend — geopolitical events, company announcements, macro developments — before you commit to any positions. The prediction window is seven calendar days, so weekly Monday runs give you clean, non-overlapping rounds of picks. Running more frequently would create overlapping positions and muddy the calibration data.

The 7:30am timing works because Yahoo Finance uses Friday's closing prices for LSE stocks over the weekend, so the technical analysis is based on clean end-of-week data. The news and macro sentiment layers will reflect anything that happened over the weekend, giving you the most complete picture possible before the market opens.

Each time you run the script it will automatically:
1. Resolve any picks from seven or more days ago by fetching their actual outcome prices
2. Update the CSV log with those outcomes
3. Use the accumulated history to self-calibrate its probability estimates
4. Fetch macro and sector sentiment before screening begins
5. Screen all ~350 FTSE tickers technically, then run company news on the top candidates
6. Apply sector sensitivity adjustments based on the detected macro event type

On your very first run, consider choosing **Backtest mode (B)** before making any live picks. This simulates a year of historical weekly runs to bootstrap the calibration system immediately rather than waiting weeks for live picks to accumulate.

After roughly four to six weeks (ten or more resolved picks) the self-calibration will start meaningfully adjusting the model's outputs based on its real track record.

### Weekly workflow

**7:30am Monday** — run the programme in Live mode. The news and macro sentiment layers will capture anything that broke over the weekend. Review the picks and note the macro warning panel if present.

**8:00am Monday** — the LSE opens. Check the opening price of each pick against Friday's closing price. The gap (if any) only becomes visible at this point since the programme runs off Friday's close and the Monday opening price isn't known until the market opens.

- If a stock has gapped **significantly upward**, the predicted upside may already be consumed — consider skipping it
- If a stock has gapped **significantly downward** and is already near or below its stop price, the thesis has been invalidated — skip it
- If the gap is **negligible**, the pick is still valid

**8:00–8:15am** — place limit buy orders for picks that pass the gap check. Waiting briefly after open is better practice than placing orders pre-market, as spreads tend to be wider in the first few minutes of trading.

As a general guide, a gap of more than ±1% warrants reassessment of the R:R before entering. A gap beyond the stop price is an automatic skip.

**If a major unexpected macro event occurs over the weekend** that the programme couldn't have anticipated, check the macro warning panel carefully. If the model is recommending skipping the week, take that seriously.

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

### Macro Warning Panel

After fetching market-wide headlines, the script checks the overall sentiment score against two thresholds:

- **Caution (score below -0.4)** — a yellow warning panel is shown. The detected event type is named (e.g. "Geopolitical event detected"), sector sensitivities have been applied to the picks, and you are advised to review each pick's sector response before entering.
- **Skip recommendation (score below -0.6)** — a red warning panel is shown. The script still produces picks for reference, but explicitly recommends considering sitting out the week due to elevated market-wide risk.

If no warning threshold is breached the panel is not shown and the run proceeds normally.

---

### Main Results Table

| Column | What it means |
|---|---|
| **Ticker** | LSE stock symbol |
| **Sector** | The sector the stock belongs to. The five picks will always span different sectors to avoid concentration risk |
| **Price (p)** | Last closing price, in **pence**. Divide by 100 for £ (e.g. 2340p = £23.40) |
| **Target (p)** | The price the model expects the stock could reach within seven days, in pence |
| **Upside** | The percentage gain if the stock reaches its target price |
| **Prob.** | The model's estimated probability that the target will be reached, after calibration, macro, and sector adjustments. Colour-coded: green ≥ 60%, yellow ≥ 45%, red below 45% |
| **Stop (p)** | The stop-loss price to include on your buy order. If the stock falls to this level, your broker will automatically sell, limiting your loss |
| **Limit (p)** | The take-profit limit to set after buying. Your broker will automatically sell when the stock reaches this price, locking in the gain without you having to monitor it |
| **R:R** | Reward:Risk ratio — how much you stand to gain relative to how much you risk. An R:R of 2.0 means for every £1 you risk losing (to the stop-loss), you stand to make £2 if the target is hit. **Aim for ≥ 1.5.** Below 1.0 means you are risking more than you could gain |
| **Score** | The model's internal signal quality score, combining technical indicators and company news sentiment adjustment. The technical maximum is approximately **110**; news can add or subtract up to **15** further points |
| **Co. News** | Sentiment of company-specific headlines: Very positive, Positive, Neutral, Negative, or Very negative |
| **Sector News** | Sentiment of sector-wide headlines for this pick's sector |

---

### Detailed Signal Breakdown

Printed below the main table, this lists the specific technical indicators that fired for each stock — for example "MACD histogram bullish crossover" or "EMA20 > EMA50 > SMA200 (fully aligned bullish)". It also shows:

- The ATR value (a measure of recent volatility used to set the target and stop distances)
- The stop distance as a percentage below your entry price
- The company news sentiment score and article count
- The actual company headlines used to calculate the sentiment score
- The macro event type detected and how this stock's sector responds to it (e.g. "strong beneficiary", "mild headwind")

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
- `Strong signal — favoured` — probability ≥ 60%, Kelly sizing has weighted it heavily
- `Moderate signal` — probability between 50–59%
- `Weak signal — small stake only` — probability below 50% but above the minimum threshold
- `Strong signal — favoured  ·  fractional share` — the pick is good but the suggested investment is less than one whole share
- `⚠ Below confidence threshold — skip` — the probability is too low for the model to recommend any capital

**Capital summary line:** Shows your total entered capital, the suggested amount to deploy across all picks, and how much to keep in reserve.

---

### Macro & Sector News Table

Printed after the signal breakdown, this table summarises the macro and sector context for the week:

- **Market-wide row** — the overall market sentiment score, the detected event type, and up to three of the most relevant headlines
- **One row per sector** represented in the final five picks — sector sentiment label and key headlines for that sector

This table only shows the sectors that appear in the final picks, not all sectors. If the top five picks span Tech, Energy, Banking, Pharma, and Mining, only those five sectors are shown alongside the market-wide row.

---

## Run modes

The first thing the script asks on every run is which mode to use:

- **Live mode (L)** — the full run. Outcome back-filling and CSV saving are both active. Use this for your weekly run.
- **Preview mode (P)** — everything runs as normal (screening, news sentiment, macro analysis, calibration report, tables, position sizing) but nothing is written to the CSV. Use this if you want to check the current picks mid-week without it counting as your weekly log entry.
- **History mode (H)** — skips the screener entirely and lets you browse past runs. You will be shown a numbered list of all previous Live runs with their hit rates, and can select any one to see the full predictions table alongside the actual outcomes.
- **Backtest mode (B)** — simulates historical weekly runs to bootstrap the calibration system. See the Backtesting section below for full details.

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

## How company news sentiment works

After the technical screening pass, the top 20 candidates by score are passed to the company news module:

1. **Headlines are fetched** from NewsAPI for each candidate using the company name as a search query
2. **Each headline is scored** individually using VADER sentiment analysis (-1.0 to +1.0)
3. **Scores are weighted by recency** — articles from today carry full weight (1.0), scaling down to 0.3 for the oldest articles in the window
4. **Volume has a mild amplifying effect** — more articles nudge the score slightly further in the same direction, but a single extreme headline (e.g. a major profit warning) still carries meaningful weight on its own
5. **The final score adjusts the pick's technical score** by up to ±15 points and its probability by up to ±10 percentage points

**Surgical replacement:** If a pick has negative company news sentiment, the screener tries to replace just that pick with the next best candidate. Picks with good sentiment are kept. If replacement candidates also have bad news, the screener expands the pool in batches of 10 until a viable replacement is found or the list is exhausted.

If NewsAPI is unavailable, tickers pass through unchanged — a NewsAPI outage never prevents the screener from producing results.

---

## How macro and sector sentiment works

Before any stock screening begins, the script fetches market-wide headlines and classifies the dominant macro event type:

| Event type | Detected when headlines contain |
|---|---|
| **Geopolitical** | war, conflict, attack, military, sanctions, invasion, missile... |
| **Recession** | recession, GDP, contraction, slowdown, unemployment, layoffs... |
| **Inflation** | inflation, CPI, rate rise, interest rate, rate hike, hawkish... |
| **Currency** | pound, sterling, GBP, exchange rate, weak pound, strong dollar... |
| **General** | fallback if no specific event type is detected |

Each sector has a defined sensitivity to each event type, reflecting how that sector actually responds in practice:

- During a **geopolitical event** (e.g. a war in the Middle East), Energy and Mining picks are boosted (oil price spike, gold safe haven), while Tech and Leisure are penalised
- During a **recession**, defensives like ConsStaples and Utilities hold up or benefit from rotation, while cyclicals like ConsDis and Leisure are penalised
- During an **inflation/rate rise** event, Energy and Mining benefit as commodity hedges, while RealEstate and Tech are penalised by higher rates
- During **currency weakness** (GBP falling), exporters and dollar-earning multinationals benefit, while importers and domestic-focused companies face headwinds

This means macro sentiment never applies a flat dampener to all picks equally. A geopolitical event might simultaneously boost an Energy pick's probability while reducing a Tech pick's probability, which better reflects how markets actually move.

**Sector sentiment** adds a further layer — for each sector represented in the shortlist, a sector-specific news search is run. If a sector has very negative news, the screener tries to replace that pick with the next best candidate from a different sector. If no better alternative exists, the best available pick is kept regardless, and the negative sector sentiment is noted in the output.

**Warning thresholds:**
- Score below **-0.4**: yellow caution panel shown, picks produced with sensitivity-adjusted probabilities
- Score below **-0.6**: red skip recommendation shown, picks still produced for reference

---

## Backtesting

Backtest mode simulates historical weekly runs to bootstrap the calibration system immediately rather than waiting weeks for live picks to accumulate. It runs two phases:

**Phase 1 — Technical backtest (52 weeks)**
Simulates a full year of weekly runs using only technical scoring on historical price data. News and macro sentiment are excluded because genuinely historical article data is not available. Produces approximately 260 resolved picks and saves them to `lse_backtest_technical.csv`. These results carry a calibration weight of **0.6** — lower than live picks because news context is absent.

**Phase 2 — News-enhanced backtest (4 weeks)**
Reruns the last 4 weeks with company, sector, and macro news included. Note that NewsAPI returns articles filtered by date but drawn from its current index — it is not a perfect reconstruction of what news was available on a specific Sunday evening. Results are saved to `lse_backtest_news.csv` and carry a calibration weight of **0.3**, reflecting this limitation.

**How the calibration uses all three sources**

The calibration engine reads from all three CSV files and combines them using weighted averaging:

| Source | File | Weight | Phases out when |
|---|---|---|---|
| Live picks | `lse_screener_log.csv` | 1.0 | Never |
| Technical backtest | `lse_backtest_technical.csv` | 0.6 | 30 live picks |
| News backtest | `lse_backtest_news.csv` | 0.3 | 30 live picks |

Once you have 30 resolved live picks the backtest data is phased out automatically and the calibration relies entirely on your real trading history. At that point the backtest has served its purpose.

**When to run it**
Backtest mode is designed to be run once or twice — ideally before your first Live run so the calibration is active from day one. It takes 10–20 minutes due to the volume of historical price data being downloaded. Both output CSV files are created automatically.

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
| `NEWS_MAX_SCORE_ADJ` | 15 | Maximum points added or subtracted from the technical score by company news |
| `NEWS_CANDIDATE_COUNT` | 20 | Number of top technical candidates to run company news analysis on |
| `NEWS_FALLBACK_BATCH` | 10 | Number of additional candidates to check if replacements are needed |
| `MACRO_WARNING_THRESHOLD` | -0.4 | Macro score below which a caution panel is shown |
| `MACRO_SKIP_THRESHOLD` | -0.6 | Macro score below which a skip recommendation is shown |
| `MACRO_MAX_PROB_SHIFT` | 15.0 | Maximum probability adjustment (pp) from macro + sector sentiment combined |
| `BACKTEST_WEEKS_TECHNICAL` | 52 | Number of weeks simulated in Phase 1 of the backtest |
| `BACKTEST_WEEKS_NEWS` | 4 | Number of weeks simulated in Phase 2 of the backtest |
| `CALIBRATION_WEIGHT_LIVE` | 1.0 | Calibration weight for live picks |
| `CALIBRATION_WEIGHT_TECHNICAL` | 0.6 | Calibration weight for technical backtest picks |
| `CALIBRATION_WEIGHT_NEWS` | 0.3 | Calibration weight for news backtest picks |
| `CALIBRATION_LIVE_THRESHOLD` | 30 | Live picks needed before backtest data is phased out |

---

## Filters applied during screening

- **Volume filter** — removes stocks whose average daily traded value is below £500,000. Low-volume stocks have less reliable price signals and wider bid/ask spreads.
- **Sector diversification** — ensures the five picks span different sectors where possible, so a single sector shock does not affect all positions simultaneously.
- **Event filter** — removes any stock with an earnings announcement or ex-dividend date falling within the next seven days.
- **Company news sentiment** — recent headlines are analysed per stock. Strong negative sentiment reduces score and probability; strong positive sentiment increases them.
- **Sector sentiment** — sector-wide headlines are analysed. Picks with very negative sector sentiment are replaced if a better alternative exists.
- **Macro sensitivity** — market-wide sentiment is classified by event type and applied to each sector according to how that sector historically responds to that type of event.

---

## Data files

Three CSV files are used to track picks and calibration data:

| File | Created by | Purpose |
|---|---|---|
| `lse_screener_log.csv` | First Live run | Logs all live picks and their outcomes |
| `lse_backtest_technical.csv` | Backtest mode | Stores Phase 1 technical backtest results |
| `lse_backtest_news.csv` | Backtest mode | Stores Phase 2 news-enhanced backtest results |

None of these need to be created or edited manually. If you want to review your historical performance outside the script, you can open any of them in Excel or a spreadsheet application.

---

## Important notes

- **All prices in the tables are in pence, not pounds.** A price of 2340 means £23.40. The exception is the Position Sizing table, where the "Price (£)" and "Invest (£)" columns are already converted to pounds.
- **Data comes from Yahoo Finance** and uses Friday's closing prices for LSE stocks over the weekend. Running at 7:30am Monday ensures the technical analysis is based on clean end-of-week data while the news sentiment captures any weekend developments.
- **The script screens approximately 350 FTSE 100/250 tickers**, fetched live from Wikipedia on each run.
- **News and macro sentiment use NewsAPI**, which provides up to 1,000 requests per day on the plan used. A typical run makes 26–36 requests (20 company searches, 1 market-wide search, up to 5 sector searches), well within the daily limit.
- **The probability figures are model estimates**, not guaranteed odds. They reflect the historical hit rate of similar technical setups, adjusted for the model's own track record, current news sentiment, and macro/sector conditions.
