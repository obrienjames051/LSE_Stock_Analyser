"""
charts.py
---------
Visualisation mode for the LSE Analyser (v10.0).

Accessible from the main menu via [G] Graphs.

Four views:
  [W] Week view     -- price trajectory for all picks in a selected week
  [C] Compare weeks -- bar chart of weekly returns vs FTSE 100/250
  [S] Stock view    -- all appearances of a single ticker across all runs
  [F] Filters       -- set persistent filters applied to all views

Charts open in separate matplotlib windows and can be open simultaneously.
The terminal stays active underneath each chart window.

Data sources:
  lse_screener_log.csv  -- live picks and outcomes
  lse_preview_log.csv   -- preview picks (all runs)
  lse_trade_log.csv     -- actual buy/sell prices
  lse_market_log.csv    -- FTSE 100/250 weekly returns
  yfinance              -- historical price data fetched on demand
"""

import csv
import os
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
import yfinance as yf

from rich.panel import Panel
from rich import box

from .config import (
    CSV_FILE, PREVIEW_LOG_FILE, TRADE_LOG_FILE, MARKET_LOG_FILE,
    SECTOR_QUERIES,
)
from .utils import console

# Matplotlib imported lazily to avoid slowing down other modes
_mpl_imported = False
plt = None
mdates = None


def _ensure_mpl():
    """Import matplotlib on first use."""
    global _mpl_imported, plt, mdates
    if not _mpl_imported:
        try:
            import matplotlib.pyplot as _plt
            import matplotlib.dates as _mdates
            plt     = _plt
            mdates  = _mdates
            plt.style.use("dark_background")
            _mpl_imported = True
        except ImportError:
            console.print(
                "[red]matplotlib is not installed.[/red]\n"
                "[dim]Run: pip install matplotlib[/dim]\n"
            )
            return False
    return True


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_live_picks() -> list:
    if not os.path.isfile(CSV_FILE):
        return []
    with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_preview_picks() -> list:
    if not os.path.isfile(PREVIEW_LOG_FILE):
        return []
    with open(PREVIEW_LOG_FILE, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_trade_log() -> list:
    if not os.path.isfile(TRADE_LOG_FILE):
        return []
    with open(TRADE_LOG_FILE, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_market_log() -> dict:
    if not os.path.isfile(MARKET_LOG_FILE):
        return {}
    with open(MARKET_LOG_FILE, "r", newline="", encoding="utf-8") as f:
        return {r["week_date"]: r for r in csv.DictReader(f)}


def _fetch_price_history(ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Fetch daily OHLC from yfinance for a ticker between start and end dates."""
    try:
        df = yf.download(
            ticker + ".L", start=start - timedelta(days=1),
            end=end + timedelta(days=2),
            interval="1d", progress=False, auto_adjust=True,
        )
        if df.empty:
            return pd.DataFrame()

        # Flatten MultiIndex columns (newer yfinance versions return these)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() if isinstance(c, str) else str(c).lower()
                          for c in df.columns]

        # Ensure index is tz-naive datetime
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        return df
    except Exception:
        return pd.DataFrame()


# ── Filter state (persists within a session) ─────────────────────────────────

_filters = {
    "mode":        "both",    # "live", "preview", "both"
    "date_from":   None,      # datetime or None
    "date_to":     None,      # datetime or None
    "outcome":     "all",     # "all", "winners", "losers", "stopped"
    "sector":      "all",     # "all" or sector label
}


def _apply_filters(rows: list, source: str = "live") -> list:
    """Apply current _filters to a list of pick rows."""
    if _filters["mode"] == "live"    and source != "live":    return []
    if _filters["mode"] == "preview" and source != "preview": return []

    out = []
    for r in rows:
        # Date range
        try:
            rd = datetime.strptime(r["run_date"][:10], "%Y-%m-%d")
            if _filters["date_from"] and rd < _filters["date_from"]: continue
            if _filters["date_to"]   and rd > _filters["date_to"]:   continue
        except (ValueError, KeyError):
            pass

        # Sector
        if _filters["sector"] != "all":
            if r.get("sector", "").strip() != _filters["sector"]:
                continue

        # Outcome (live only — preview has no outcomes)
        if source == "live" and _filters["outcome"] != "all":
            ret = r.get("outcome_return_pct", "").strip()
            hit = r.get("outcome_hit", "").strip()
            if not ret:
                continue
            try:
                ret_f = float(ret)
            except ValueError:
                continue
            if _filters["outcome"] == "winners" and ret_f <= 0:  continue
            if _filters["outcome"] == "losers"  and ret_f >= 0:  continue
            if _filters["outcome"] == "stopped" and hit != "NO": continue

        out.append(r)
    return out


# ── Colour helpers ────────────────────────────────────────────────────────────

def _pick_colour(row: dict) -> str:
    """Return a matplotlib colour string based on pick outcome."""
    ret = row.get("outcome_return_pct", "").strip()
    if not ret:
        return "#888888"   # pending — grey
    try:
        return "#00cc66" if float(ret) >= 0 else "#ff4444"
    except ValueError:
        return "#888888"


def _fmt_return(row: dict) -> str:
    ret = row.get("outcome_return_pct", "").strip()
    if not ret:
        return "pending"
    try:
        v = float(ret)
        return f"{'+' if v >= 0 else ''}{v:.1f}%"
    except ValueError:
        return "?"


# ── View 1: Week view ─────────────────────────────────────────────────────────

def _ask_toggles() -> dict:
    """Prompt user for chart toggle options."""
    console.print(
        "\n  [dim]Toggle options (press Enter to accept defaults):[/dim]\n"
        "  Show target line?        [Y/n]: ",
        end="",
    )
    show_target  = input().strip().upper() not in ("N", "NO")
    console.print("  Show stop line?          [Y/n]: ", end="")
    show_stop    = input().strip().upper() not in ("N", "NO")
    console.print("  Show trade buy/sell?     [Y/n]: ", end="")
    show_trades  = input().strip().upper() not in ("N", "NO")
    console.print("  Show preview overlays?   [y/N]: ", end="")
    show_preview = input().strip().upper() in ("Y", "YES")
    return {
        "target":  show_target,
        "stop":    show_stop,
        "trades":  show_trades,
        "preview": show_preview,
    }


def run_week_view():
    """Select a live week and plot price trajectories for all 5 picks."""
    if not _ensure_mpl():
        return

    live = _load_live_picks()
    if not live:
        console.print("[yellow]No live picks found.[/yellow]\n")
        return

    # Group by week_date
    weeks = {}
    for r in live:
        weeks.setdefault(r["run_date"], []).append(r)
    week_dates = sorted(weeks.keys(), reverse=True)

    console.print("\n[bold]Select a week:[/bold]\n")
    for i, wd in enumerate(week_dates, 1):
        picks = weeks[wd]
        tickers = ", ".join(r["ticker"] for r in picks)
        console.print(f"  [bold white]{i:>2}.[/bold white]  [yellow]{wd[:10]}[/yellow]  [dim]{tickers}[/dim]")

    console.print()
    while True:
        try:
            raw    = input(f"  Week number (1-{len(week_dates)}): ").strip()
            choice = int(raw)
            if 1 <= choice <= len(week_dates):
                break
            raise ValueError
        except ValueError:
            console.print(f"  [red]Please enter 1-{len(week_dates)}[/red]")

    week_date  = week_dates[choice - 1]
    picks      = weeks[week_date]
    toggles    = _ask_toggles()
    trade_log  = _load_trade_log() if toggles["trades"] else []
    prev_picks = _load_preview_picks() if toggles["preview"] else []

    # Parse week window: Tuesday open → following Monday close
    try:
        pick_dt  = datetime.strptime(week_date[:10], "%Y-%m-%d")
        week_end = pick_dt + timedelta(days=6)
    except ValueError:
        console.print("[red]Could not parse week date.[/red]\n")
        return

    n      = len(picks)
    ncols  = 3
    nrows  = (n + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(16, 4 * nrows))
    fig.patch.set_facecolor("#1a1a2e")
    fig.suptitle(
        f"Week of {week_date[:10]}",
        color="white", fontsize=12, fontweight="bold",
    )

    axes_flat = axes.flatten() if hasattr(axes, "flatten") else [axes]

    for idx, row in enumerate(picks):
        ax      = axes_flat[idx]
        ticker  = row["ticker"]
        colour  = _pick_colour(row)

        ax.set_facecolor("#0d0d1a")

        # Fetch price history
        with console.status(f"[dim]Fetching {ticker}...[/dim]"):
            df = _fetch_price_history(ticker, pick_dt, week_end)

        # For pending picks, derive colour from current price vs entry
        if colour == "#888888" and not df.empty:
            try:
                close_col_tmp = "close" if "close" in df.columns else df.columns[0]
                df_week_tmp   = df[(df.index >= pd.Timestamp(pick_dt)) &
                                   (df.index <= pd.Timestamp(week_end) + pd.Timedelta(days=1))]
                if not df_week_tmp.empty:
                    current_price = df_week_tmp[close_col_tmp].iloc[-1]
                    entry_price   = float(row["price_p"])
                    colour = "#00cc66" if current_price >= entry_price else "#ff4444"
            except (ValueError, KeyError):
                pass

        for spine in ax.spines.values():
            spine.set_edgecolor(colour)
            spine.set_linewidth(2)

        if df.empty:
            ax.text(0.5, 0.5, "Data unavailable", transform=ax.transAxes,
                    ha="center", va="center", color="#888888", fontsize=11)
        else:
            df_week = df[(df.index >= pd.Timestamp(pick_dt)) &
                         (df.index <= pd.Timestamp(week_end) + pd.Timedelta(days=1))]
            if not df_week.empty and "open" in df_week.columns and "close" in df_week.columns:
                # Interleave open and close for each day to double data density.
                # Each day contributes two points: open (09:00) and close (16:30).
                # This shows intraday direction as well as day-to-day movement.
                interleaved_times  = []
                interleaved_prices = []
                for ts, day_row in df_week.iterrows():
                    open_ts  = ts + pd.Timedelta(hours=9)
                    close_ts = ts + pd.Timedelta(hours=16, minutes=30)
                    interleaved_times.append(open_ts)
                    interleaved_prices.append(float(day_row["open"]))
                    interleaved_times.append(close_ts)
                    interleaved_prices.append(float(day_row["close"]))

                ax.plot(interleaved_times, interleaved_prices,
                        color=colour, linewidth=2, label="Price", zorder=3)
                ax.fill_between(interleaved_times, interleaved_prices,
                                alpha=0.08, color=colour)

                # X-axis: one tick per date, positioned at midday
                date_ticks  = [ts + pd.Timedelta(hours=12) for ts in df_week.index]
                date_labels = [ts.strftime("%A") for ts in df_week.index]
                ax.set_xticks(date_ticks)
                ax.set_xticklabels(date_labels, rotation=30, ha="right",
                                   fontsize=7, color="#aaaaaa")

                # Y-axis limits based on full open+close range
                price_min   = min(interleaved_prices)
                price_max   = max(interleaved_prices)
                price_range = max(price_max - price_min, price_max * 0.01)
                ax.set_ylim(price_min - price_range * 0.5,
                            price_max + price_range * 0.5)

        # Horizontal reference lines — prices kept in pence to match yfinance
        try:
            entry_p  = float(row["price_p"])
            target_p = float(row["target_p"])
            stop_p   = float(row["stop_p"])

            ax.axhline(entry_p, color="#4488ff", linewidth=1,
                       linestyle="--", alpha=0.7, label="Mon close (model ref)")
            if toggles["target"]:
                ax.axhline(target_p, color="#00cc66", linewidth=1,
                           linestyle="--", alpha=0.7, label="Target")
            if toggles["stop"]:
                ax.axhline(stop_p, color="#ff4444", linewidth=1,
                           linestyle="--", alpha=0.7, label="Stop")
        except (ValueError, KeyError):
            pass

        # Trade tracker markers
        if toggles["trades"]:
            for t in trade_log:
                if t.get("ticker") == ticker and t.get("week_date", "")[:10] == week_date[:10]:
                    buy_price_raw = t.get("buy_price_p", "").strip()
                    buy_date_raw  = t.get("buy_date", "").strip()
                    if buy_price_raw and buy_date_raw:
                        try:
                            buy_p  = float(buy_price_raw)
                            buy_dt = datetime.strptime(buy_date_raw, "%Y-%m-%d")
                            ax.axhline(buy_p, color="#ffcc00", linewidth=1.5,
                                       linestyle=":", alpha=0.9, label="Actual buy (Tue open)")
                            ax.axvline(pd.Timestamp(buy_dt), color="#ffcc00",
                                       linewidth=1, linestyle=":", alpha=0.5)
                        except (ValueError, KeyError):
                            pass
                    sell_price_raw = t.get("sell_price_p", "").strip()
                    sell_date_raw  = t.get("sell_date", "").strip()
                    if sell_price_raw and sell_date_raw:
                        try:
                            sell_p  = float(sell_price_raw)
                            sell_dt = datetime.strptime(sell_date_raw, "%Y-%m-%d")
                            ax.axhline(sell_p, color="#ff9900", linewidth=1.5,
                                       linestyle=":", alpha=0.9, label="Actual sell")
                            ax.axvline(pd.Timestamp(sell_dt), color="#ff9900",
                                       linewidth=1, linestyle=":", alpha=0.5)
                        except (ValueError, KeyError):
                            pass

        # Preview overlays
        if toggles["preview"]:
            prev_week = [p for p in prev_picks
                         if p.get("ticker") == ticker
                         and p.get("run_date", "")[:10] >= week_date[:10]
                         and p.get("run_date", "")[:10] <= week_end.strftime("%Y-%m-%d")]
            for p in prev_week:
                try:
                    prev_entry = float(p["price_p"]) / 100
                    prev_dt    = datetime.strptime(p["run_date"][:10], "%Y-%m-%d")
                    ax.scatter([pd.Timestamp(prev_dt)], [prev_entry],
                               color="#aaaaff", s=30, zorder=4, alpha=0.6,
                               marker="D", label=f"Preview {p['run_date'][:10]}")
                except (ValueError, KeyError):
                    pass

        # Labels
        score    = row.get("score", "?")
        sector   = row.get("sector", "")
        ret_str  = _fmt_return(row)
        ax.set_title(
            f"{ticker}  |  {sector}  |  score {score}  |  {ret_str}",
            color="white", fontsize=9, pad=6,
        )
        ax.tick_params(colors="#aaaaaa", labelsize=7)
        # x-axis tick formatting handled above when building interleaved series
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, _: f"{x:.0f}p")
        )
        ax.legend(fontsize=6, loc="upper left",
                  facecolor="#1a1a2e", labelcolor="white", framealpha=0.7)

    # Hide unused subplots
    for idx in range(n, len(axes_flat)):
        axes_flat[idx].set_visible(False)

    plt.tight_layout()
    plt.show(block=False)
    console.print(f"[dim]Chart opened for week {week_date[:10]}[/dim]\n")


# ── View 2: Compare weeks ─────────────────────────────────────────────────────

def run_compare_weeks():
    """Bar chart of average weekly return vs FTSE 100/250."""
    if not _ensure_mpl():
        return

    live       = _apply_filters(_load_live_picks(), "live")
    market_log = _load_market_log()

    if not live:
        console.print("[yellow]No live picks match the current filters.[/yellow]\n")
        return

    # Group resolved picks by week
    resolved_weeks = defaultdict(list)
    pending_weeks  = defaultdict(list)

    for r in live:
        ret = r.get("outcome_return_pct", "").strip()
        if ret:
            try:
                resolved_weeks[r["run_date"][:10]].append(float(ret))
            except ValueError:
                pass
        else:
            pending_weeks[r["run_date"][:10]].append(r)

    if not resolved_weeks and not pending_weeks:
        console.print("[yellow]No live picks found.[/yellow]\n")
        return

    sorted_weeks  = sorted(resolved_weeks.keys())
    avg_returns   = [sum(resolved_weeks[w]) / len(resolved_weeks[w]) for w in sorted_weeks]
    f100_returns  = []
    f250_returns  = []

    for w in sorted_weeks:
        mkt = None
        for k, v in market_log.items():
            if k[:10] == w:
                mkt = v
                break
        try:
            f100_returns.append(float(mkt["ftse100_return_pct"]) if mkt and mkt.get("ftse100_return_pct") else None)
            f250_returns.append(float(mkt["ftse250_return_pct"]) if mkt and mkt.get("ftse250_return_pct") else None)
        except (ValueError, TypeError):
            f100_returns.append(None)
            f250_returns.append(None)

    # Calculate pending week current returns from live price vs entry
    pending_label   = None
    pending_return  = None
    pending_f100    = None
    pending_f250    = None
    for pw, rows in sorted(pending_weeks.items()):
        if pw not in resolved_weeks:
            current_returns = []
            for r in rows:
                try:
                    pick_dt  = datetime.strptime(pw, "%Y-%m-%d")
                    week_end = pick_dt + timedelta(days=6)
                    df = _fetch_price_history(r["ticker"], pick_dt, week_end)
                    if df.empty:
                        continue
                    close_col = "close" if "close" in df.columns else df.columns[0]
                    df_week = df[(df.index >= pd.Timestamp(pick_dt)) &
                                 (df.index <= pd.Timestamp(week_end) + pd.Timedelta(days=1))]
                    if df_week.empty:
                        continue
                    current_p = df_week[close_col].iloc[-1]
                    entry_p   = float(r["price_p"])
                    current_returns.append((current_p - entry_p) / entry_p * 100)
                except (ValueError, KeyError):
                    continue
            if current_returns:
                pending_label  = pw[5:]
                pending_return = sum(current_returns) / len(current_returns)

                # Fetch current FTSE index returns for the same window
                try:
                    pick_dt  = datetime.strptime(pw, "%Y-%m-%d")
                    week_end = pick_dt + timedelta(days=6)
                    for idx_ticker, is_100 in (("^FTSE", True), ("^FTMC", False)):
                        idx_df = yf.download(idx_ticker,
                                             start=pick_dt - timedelta(days=1),
                                             end=week_end + timedelta(days=2),
                                             interval="1d", progress=False, auto_adjust=True)
                        if idx_df.empty:
                            continue
                        idx_df.index = pd.to_datetime(idx_df.index).tz_localize(None)
                        if isinstance(idx_df.columns, pd.MultiIndex):
                            idx_df.columns = [c[0].lower() for c in idx_df.columns]
                        else:
                            idx_df.columns = [c.lower() if isinstance(c, str) else str(c).lower()
                                              for c in idx_df.columns]
                        idx_week = idx_df[(idx_df.index >= pd.Timestamp(pick_dt)) &
                                          (idx_df.index <= pd.Timestamp(week_end) + pd.Timedelta(days=1))]
                        if len(idx_week) >= 1:
                            open_p  = float(idx_week["open"].iloc[0])
                            close_p = float(idx_week["close"].iloc[-1])
                            val     = round((close_p - open_p) / open_p * 100, 4)
                            if is_100:
                                pending_f100 = val
                            else:
                                pending_f250 = val
                except Exception:
                    pass
            break  # only show most recent pending week

    # Build x positions — resolved weeks + optional pending bar
    all_labels = [w[5:] for w in sorted_weeks]
    all_x      = list(range(len(sorted_weeks)))

    if pending_label:
        pending_x = len(all_x)
        all_labels.append(f"{pending_label} *")
        all_x.append(pending_x)

    fig, ax = plt.subplots(figsize=(max(10, len(all_x) * 1.2), 6))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#0d0d1a")

    # Resolved bars
    if sorted_weeks:
        bar_colours = ["#00cc66" if r >= 0 else "#ff4444" for r in avg_returns]
        ax.bar(list(range(len(sorted_weeks))), avg_returns,
               color=bar_colours, alpha=0.85, label="Avg pick return", zorder=3)

    # Pending bar — greyed out with hatching
    if pending_label and pending_return is not None:
        pc = "#449966" if pending_return >= 0 else "#994444"
        ax.bar([pending_x], [pending_return], color=pc, alpha=0.4,
               hatch="//", edgecolor="#888888", linewidth=0.5,
               label="Current week (pending)", zorder=3)
        ax.annotate(
            "pending",
            xy=(pending_x, pending_return + (0.05 if pending_return >= 0 else -0.15)),
            ha="center", va="bottom", fontsize=7, color="#888888", style="italic",
        )
        if pending_f100 is not None:
            ax.plot([pending_x], [pending_f100], color="#4488ff",
                    marker="o", markersize=6, zorder=5, alpha=0.6)
        if pending_f250 is not None:
            ax.plot([pending_x], [pending_f250], color="#aa44ff",
                    marker="o", markersize=6, zorder=5, alpha=0.6)

    # FTSE lines (resolved weeks only)
    f100_clean = [(i, v) for i, v in enumerate(f100_returns) if v is not None]
    f250_clean = [(i, v) for i, v in enumerate(f250_returns) if v is not None]
    if f100_clean:
        xi, yi = zip(*f100_clean)
        ax.plot(xi, yi, color="#4488ff", linewidth=2,
                marker="o", markersize=4, label="FTSE 100", zorder=4)
    if f250_clean:
        xi, yi = zip(*f250_clean)
        ax.plot(xi, yi, color="#aa44ff", linewidth=2,
                marker="o", markersize=4, label="FTSE 250", zorder=4)

    ax.axhline(0, color="#555555", linewidth=0.8)

    # Set y-axis limits to encompass all values — bars, FTSE points, and zero
    all_values = avg_returns + [v for v in f100_returns if v is not None] +                  [v for v in f250_returns if v is not None] + [0]
    if pending_return is not None:
        all_values.append(pending_return)
    if pending_f100 is not None:
        all_values.append(pending_f100)
    if pending_f250 is not None:
        all_values.append(pending_f250)
    y_min   = min(all_values)
    y_max   = max(all_values)
    y_range = max(y_max - y_min, 0.5)
    ax.set_ylim(y_min - y_range * 0.15, y_max + y_range * 0.25)

    # Alpha annotations on resolved bars
    for i, (ret, f100) in enumerate(zip(avg_returns, f100_returns)):
        if f100 is not None:
            alpha = ret - f100
            ac    = "#00cc66" if alpha >= 0 else "#ff4444"
            ax.annotate(
                f"{'+' if alpha >= 0 else ''}{alpha:.1f}pp",
                xy=(i, max(ret, 0) + 0.1),
                ha="center", va="bottom", fontsize=7,
                color=ac, fontweight="bold",
            )

    ax.set_xticks(all_x)
    ax.set_xticklabels(all_labels, rotation=45, ha="right",
                       color="#aaaaaa", fontsize=8)
    ax.tick_params(colors="#aaaaaa")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.set_title("Weekly Returns vs Market  (* = pending)", color="white",
                 fontsize=12, fontweight="bold")
    ax.legend(facecolor="#1a1a2e", labelcolor="white", fontsize=9)
    ax.set_ylabel("Return %", color="#aaaaaa")

    plt.tight_layout()
    plt.show(block=False)
    console.print("[dim]Compare weeks chart opened.[/dim]\n")


# ── View 3: Stock view ────────────────────────────────────────────────────────

def run_stock_view():
    """Show all appearances of a ticker across live and preview runs."""
    if not _ensure_mpl():
        return

    ticker_raw = input("\n  Enter ticker (e.g. BARC, VOD): ").strip().upper()
    if not ticker_raw:
        return
    ticker = ticker_raw.replace(".L", "")

    live    = [r for r in _apply_filters(_load_live_picks(),    "live")
               if r["ticker"] == ticker]
    preview = [r for r in _apply_filters(_load_preview_picks(), "preview")
               if r["ticker"] == ticker]
    all_appearances = [("live", r) for r in live] + [("preview", r) for r in preview]

    if not all_appearances:
        console.print(f"[yellow]{ticker} not found in any picks.[/yellow]\n")
        return

    all_appearances.sort(key=lambda x: x[1]["run_date"])

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#0d0d1a")

    for source, row in all_appearances:
        try:
            pick_dt  = datetime.strptime(row["run_date"][:10], "%Y-%m-%d")
            week_end = pick_dt + timedelta(days=6)
        except ValueError:
            continue

        with console.status(f"[dim]Fetching {ticker} ({row['run_date'][:10]})...[/dim]"):
            df = _fetch_price_history(ticker, pick_dt, week_end)

        if df.empty:
            continue

        close_col = "close" if "close" in df.columns else df.columns[0]
        df_week   = df[(df.index >= pd.Timestamp(pick_dt)) &
                       (df.index <= pd.Timestamp(week_end) + pd.Timedelta(days=1))]
        if df_week.empty:
            continue

        # Normalise to % change from first price so all lines are comparable
        first = df_week[close_col].iloc[0]
        norm  = (df_week[close_col] / first - 1) * 100

        if source == "live":
            colour    = _pick_colour(row)
            lw        = 2.0
            alpha     = 0.9
            ret_str   = _fmt_return(row)
            label     = f"{row['run_date'][:10]} (live) {ret_str}"
        else:
            colour    = "#aaaaaa"
            lw        = 1.0
            alpha     = 0.45
            label     = f"{row['run_date'][:10]} (preview)"

        ax.plot(range(len(norm)), norm.values,
                color=colour, linewidth=lw, alpha=alpha, label=label)

    ax.axhline(0, color="#555555", linewidth=0.8, linestyle="--")
    ax.set_title(f"{ticker}  —  All Appearances", color="white",
                 fontsize=13, fontweight="bold")
    ax.set_ylabel("% change from entry", color="#aaaaaa")
    ax.set_xlabel("Days from pick date", color="#aaaaaa")
    ax.tick_params(colors="#aaaaaa")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:+.1f}%"))
    ax.legend(facecolor="#1a1a2e", labelcolor="white", fontsize=8,
              loc="upper left")

    plt.tight_layout()
    plt.show(block=False)
    console.print(f"[dim]{ticker} stock view opened.[/dim]\n")


# ── View 4: Filters ───────────────────────────────────────────────────────────

def run_filters():
    """Set persistent filters applied to all chart views."""
    console.print("\n[bold]Current filters:[/bold]\n")
    console.print(f"  Mode:       {_filters['mode']}")
    console.print(f"  Date from:  {_filters['date_from'].strftime('%Y-%m-%d') if _filters['date_from'] else 'none'}")
    console.print(f"  Date to:    {_filters['date_to'].strftime('%Y-%m-%d')   if _filters['date_to']   else 'none'}")
    console.print(f"  Outcome:    {_filters['outcome']}")
    console.print(f"  Sector:     {_filters['sector']}\n")

    console.print("  [dim]Leave blank to keep current value. Enter 'clear' to reset.[/dim]\n")

    # Mode
    console.print("  Mode (live / preview / both): ", end="")
    raw = input().strip().lower()
    if raw == "clear":
        _filters["mode"] = "both"
    elif raw in ("live", "preview", "both"):
        _filters["mode"] = raw

    # Date from
    console.print("  Date from (YYYY-MM-DD): ", end="")
    raw = input().strip()
    if raw == "clear":
        _filters["date_from"] = None
    elif raw:
        try:
            _filters["date_from"] = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            console.print("  [red]Invalid date — kept unchanged.[/red]")

    # Date to
    console.print("  Date to   (YYYY-MM-DD): ", end="")
    raw = input().strip()
    if raw == "clear":
        _filters["date_to"] = None
    elif raw:
        try:
            _filters["date_to"] = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            console.print("  [red]Invalid date — kept unchanged.[/red]")

    # Outcome
    console.print("  Outcome (all / winners / losers / stopped): ", end="")
    raw = input().strip().lower()
    if raw == "clear":
        _filters["outcome"] = "all"
    elif raw in ("all", "winners", "losers", "stopped"):
        _filters["outcome"] = raw

    # Sector
    sectors = sorted(SECTOR_QUERIES.keys())
    console.print(f"  Sector ({' / '.join(sectors)} / all): ", end="")
    raw = input().strip()
    if raw == "clear" or raw.lower() == "all":
        _filters["sector"] = "all"
    elif raw in sectors:
        _filters["sector"] = raw
    elif raw:
        console.print("  [red]Unknown sector — kept unchanged.[/red]")

    console.print("\n[green]Filters updated.[/green]\n")
    console.print(f"  Mode: {_filters['mode']}  |  "
                  f"Dates: {_filters['date_from'] or 'any'} → {_filters['date_to'] or 'any'}  |  "
                  f"Outcome: {_filters['outcome']}  |  "
                  f"Sector: {_filters['sector']}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def run_charts():
    """Main entry point for Graphs mode — loops until user quits."""
    while True:
        # Show active filters if any non-default
        filter_note = ""
        if any([
            _filters["mode"] != "both",
            _filters["date_from"],
            _filters["date_to"],
            _filters["outcome"] != "all",
            _filters["sector"] != "all",
        ]):
            filter_note = (f"\n  [dim]Active filters: mode={_filters['mode']}  "
                           f"outcome={_filters['outcome']}  "
                           f"sector={_filters['sector']}[/dim]")

        console.print(
            f"\n[bold cyan]Graphs[/bold cyan]{filter_note}\n"
            "[dim]  [W] Week view     -- price trajectories for a selected week\n"
            "  [C] Compare weeks -- weekly returns vs FTSE 100/250\n"
            "  [S] Stock view    -- all appearances of a single ticker\n"
            "  [F] Filters       -- set filters applied to all views\n"
            "  [Q] Quit[/dim]\n"
        )
        raw = input("  Choose (W / C / S / F / Q): ").strip().upper()
        if raw in ("W", "WEEK"):
            run_week_view()
        elif raw in ("C", "COMPARE"):
            run_compare_weeks()
        elif raw in ("S", "STOCK"):
            run_stock_view()
        elif raw in ("F", "FILTER", "FILTERS"):
            run_filters()
        elif raw in ("Q", "QUIT", "EXIT"):
            return
        else:
            console.print("  [red]Please enter W, C, S, F, or Q[/red]")
