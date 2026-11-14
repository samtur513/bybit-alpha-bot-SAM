"""
Fetches historical 1h klines from Bybit Public V5 REST API
for all 28 trades in the v3.2 Legacy Paper Ledger to verify
execution price realism against actual market high/low bounds.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_STATE = REPO_ROOT / "reports" / "v32_legacy_forensic_001" / "snapshot" / "api_state.json"
OUTPUT_CACHE = REPO_ROOT / ".scratch" / "bybit_v32_klines_cache.json"


def fetch_kline_range(symbol: str, start_ms: int, end_ms: int):
    # Buffer +/- 2 hours
    s = start_ms - 2 * 3600 * 1000
    e = end_ms + 2 * 3600 * 1000
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=60&start={s}&end={e}&limit=50"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("retCode") == 0:
                return data.get("result", {}).get("list", [])
            else:
                print(f"Error for {symbol}: {data.get('retMsg')}")
                return []
    except Exception as ex:
        print(f"Exception fetching {symbol}: {ex}")
        return []


def main():
    with open(SNAPSHOT_STATE, "r", encoding="utf-8") as f:
        state = json.load(f)

    closed_trades = state["trend_engine"]["closed_trades"]
    print(f"Loaded {len(closed_trades)} closed trades.")

    cache = {}
    if OUTPUT_CACHE.exists():
        try:
            with open(OUTPUT_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            pass

    results = []
    for i, t in enumerate(closed_trades, 1):
        sym = t["symbol"]
        entry_ts = int(t["entry_time"])
        exit_ts = int(t["exit_time"])
        cache_key = f"{sym}_{entry_ts}_{exit_ts}"

        if cache_key in cache:
            klines = cache[cache_key]
        else:
            print(f"Fetching Bybit klines for trade {i:02d} ({sym})...")
            klines = fetch_kline_range(sym, entry_ts, exit_ts)
            cache[cache_key] = klines
            time.sleep(0.15)  # Pace requests

        # Find entry candle and exit candle
        # Bybit kline: [start, open, high, low, close, volume, turnover]
        entry_candle = None
        exit_candle = None
        for k in klines:
            k_start = int(k[0])
            k_end = k_start + 3600 * 1000
            if k_start <= entry_ts < k_end:
                entry_candle = k
            if k_start <= exit_ts < k_end:
                exit_candle = k

        entry_p = float(t["entry_price"])
        exit_p = float(t["exit_price"])

        entry_valid = False
        exit_valid = False
        entry_bounds = None
        exit_bounds = None

        if entry_candle:
            e_open = float(entry_candle[1])
            e_high = float(entry_candle[2])
            e_low = float(entry_candle[3])
            e_close = float(entry_candle[4])
            entry_bounds = (e_low, e_high)
            entry_valid = e_low * 0.999 <= entry_p <= e_high * 1.001

        if exit_candle:
            x_open = float(exit_candle[1])
            x_high = float(exit_candle[2])
            x_low = float(exit_candle[3])
            x_close = float(exit_candle[4])
            exit_bounds = (x_low, x_high)
            exit_valid = x_low * 0.999 <= exit_p <= x_high * 1.001

        results.append({
            "trade_idx": i,
            "symbol": sym,
            "direction": t["direction"],
            "entry_time": entry_ts,
            "exit_time": exit_ts,
            "entry_price": entry_p,
            "exit_price": exit_p,
            "exit_reason": t["exit_reason"],
            "entry_candle_found": entry_candle is not None,
            "exit_candle_found": exit_candle is not None,
            "entry_bounds": entry_bounds,
            "exit_bounds": exit_bounds,
            "entry_realistic": entry_valid,
            "exit_realistic": exit_valid,
            "klines_count": len(klines),
        })

    with open(OUTPUT_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    print("\nMarket Data Audit Summary:")
    print(f"{'#':<3} {'Symbol':<10} {'EntryP':<12} {'Entry Low-High':<25} {'Entry?':<8} {'ExitP':<12} {'Exit Low-High':<25} {'Exit?':<8}")
    print("-" * 105)
    all_entry_ok = True
    all_exit_ok = True
    for r in results:
        eb = f"[{r['entry_bounds'][0]:.4f}, {r['entry_bounds'][1]:.4f}]" if r['entry_bounds'] else "N/A"
        xb = f"[{r['exit_bounds'][0]:.4f}, {r['exit_bounds'][1]:.4f}]" if r['exit_bounds'] else "N/A"
        e_ok = "PASS" if r['entry_realistic'] else "FAIL"
        x_ok = "PASS" if r['exit_realistic'] else "FAIL"
        if not r['entry_realistic']:
            all_entry_ok = False
        if not r['exit_realistic']:
            all_exit_ok = False
        print(f"{r['trade_idx']:02d} {r['symbol']:<10} {r['entry_price']:<12.6f} {eb:<25} {e_ok:<8} {r['exit_price']:<12.6f} {xb:<25} {x_ok:<8}")

    print(f"\nAll Entry Prices Realistic: {all_entry_ok}")
    print(f"All Exit Prices Realistic: {all_exit_ok}")


if __name__ == "__main__":
    main()
