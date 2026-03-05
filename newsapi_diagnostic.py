"""
newsapi_diagnostic.py
---------------------
Tests the NewsAPI key and connection directly.
Run from LSE_Stock_Analyser/ with: python3 newsapi_diagnostic.py
"""

import os
import requests

# ── Step 1: Load .env manually ────────────────────────────────────────────────
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
print(f"Looking for .env at: {env_path}")
print(f".env exists: {os.path.isfile(env_path)}")

api_key = ""
if os.path.isfile(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("NEWSAPI_KEY"):
                api_key = line.split("=", 1)[1].strip()
                break

if api_key:
    print(f"API key found: {api_key[:8]}...{api_key[-4:]}  (length: {len(api_key)})")
else:
    print("ERROR: API key not found in .env")
    exit(1)

# ── Step 2: Test /v2/top-headlines (works on all tiers) ───────────────────────
print("\n--- Testing /v2/top-headlines ---")
try:
    r = requests.get(
        "https://newsapi.org/v2/top-headlines",
        params={"country": "gb", "pageSize": 3, "apiKey": api_key},
        timeout=10,
    )
    data = r.json()
    print(f"Status code:    {r.status_code}")
    print(f"Response status: {data.get('status')}")
    if data.get("status") == "ok":
        print(f"Articles found: {data.get('totalResults')}")
        for a in data.get("articles", [])[:2]:
            print(f"  - {a.get('title', '')[:80]}")
    else:
        print(f"Error code:    {data.get('code')}")
        print(f"Error message: {data.get('message')}")
except Exception as e:
    print(f"Exception: {e}")

# ── Step 3: Test /v2/everything (requires Developer tier or above) ─────────────
print("\n--- Testing /v2/everything ---")
try:
    r = requests.get(
        "https://newsapi.org/v2/everything",
        params={"q": "BP oil", "language": "en", "pageSize": 3, "apiKey": api_key},
        timeout=10,
    )
    data = r.json()
    print(f"Status code:    {r.status_code}")
    print(f"Response status: {data.get('status')}")
    if data.get("status") == "ok":
        print(f"Articles found: {data.get('totalResults')}")
        for a in data.get("articles", [])[:2]:
            print(f"  - {a.get('title', '')[:80]}")
    else:
        print(f"Error code:    {data.get('code')}")
        print(f"Error message: {data.get('message')}")
except Exception as e:
    print(f"Exception: {e}")

print("\nDone.")
