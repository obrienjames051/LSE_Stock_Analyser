"""
sizing.py
---------
Kelly-based position sizing and capital input prompt.
"""

from .config import PROB_FLOOR, KELLY_FRACTION
from .utils import console


def calculate_allocations(picks: list, total_capital: float):
    """Apply fractional Kelly sizing to each pick and return updated picks."""
    kelly_weights = []
    for r in picks:
        prob = r["prob"] / 100.0
        rr   = r["reward_risk"]
        if rr > 0 and prob > (PROB_FLOOR / 100):
            raw_kelly = prob - (1 - prob) / rr
            kelly     = max(0.0, raw_kelly * KELLY_FRACTION)
        else:
            kelly = 0.0
        kelly_weights.append(kelly)

    total_kelly = sum(kelly_weights)

    for i, r in enumerate(picks):
        if total_kelly > 0 and kelly_weights[i] > 0:
            alloc_pct   = kelly_weights[i] / total_kelly * 100
            alloc_gbp   = total_capital * (kelly_weights[i] / total_kelly)
            price_gbp   = r["price"] / 100
            shares      = int(alloc_gbp / price_gbp) if price_gbp > 0 else 0
            actual_cost = round(shares * price_gbp, 2)
        else:
            alloc_pct = alloc_gbp = actual_cost = 0.0
            shares    = 0

        picks[i]["kelly_weight"]   = round(kelly_weights[i], 4)
        picks[i]["allocation_pct"] = round(alloc_pct, 1)
        picks[i]["allocated_gbp"]  = round(alloc_gbp, 2)
        picks[i]["shares"]         = shares
        picks[i]["actual_cost"]    = actual_cost

    deployed = sum(p["actual_cost"] for p in picks)
    reserve  = round(total_capital - deployed, 2)
    return picks, deployed, reserve


def ask_for_capital() -> float:
    """Prompt the user for their total capital to invest this week."""
    console.print(
        "\n[bold cyan]Position Sizing[/bold cyan]\n"
        "[dim]Enter the total capital (£) you are willing to invest across all picks.\n"
        "Low-probability picks receive less or no allocation; surplus is kept as reserve.[/dim]\n"
    )
    while True:
        try:
            raw = input("  Total capital to invest (£): ").strip().replace("£", "").replace(",", "")
            val = float(raw)
            if val <= 0:
                raise ValueError
            return val
        except ValueError:
            console.print("  [red]Please enter a valid positive number (e.g. 2000)[/red]")
