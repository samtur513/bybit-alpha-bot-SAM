"""
Downloads complete 1-hour historical klines for the full 18-symbol universe
from 2024-01-01 00:00:00 UTC to 2026-10-21 23:00:00 UTC directly from Bybit API V5.
Saves to data/parquet_2024_2026/{symbol}_1h.parquet with full validation.
"""

import datetime
from pathlib import Path
import time
from typing import List, Dict, Any
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "data" / "parquet_2024_2026"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]

# 2024-01-01 00:00:00 UTC to 2026-10-21 23:59:59 UTC
START_TS = int(datetime.datetime(2024, 1, 1, 0, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
END_TS = int(datetime.datetime(2026, 10, 21, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp() * 1000)

def download_symbol_1h(symbol: str) -> pd.DataFrame:
    print(f"Downloading {symbol} (2024-2026)...", flush=True)
    all_rows = []
    current_end = END_TS
    
    url = "https://api.bybit.com/v5/market/kline"
    session = requests.Session()
    
    while current_end > START_TS:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": "60",
            "end": current_end,
            "limit": 1000
        }
        
        for attempt in range(5):
            try:
                res = session.get(url, params=params, timeout=12)
                data = res.json()
                if data.get("retCode") == 0:
                    break
                time.sleep(0.5)
            except Exception as e:
                time.sleep(1.0)
        else:
            print(f"Failed to fetch batch for {symbol} at {current_end}")
            break
            
        kline_list = data.get("result", {}).get("list", [])
        if not kline_list:
            break
            
        all_rows.extend(kline_list)
        
        # Bybit returns descending by time: earliest in batch is kline_list[-1]
        earliest_in_batch = int(kline_list[-1][0])
        if earliest_in_batch <= START_TS or earliest_in_batch >= current_end:
            break
        current_end = earliest_in_batch - 1
        time.sleep(0.05) # Polite pacing

    if not all_rows:
        print(f"No data for {symbol}!")
        return pd.DataFrame()

    # Columns: [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df["timestamp"] = df["timestamp"].astype(int)
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = df[col].astype(float)
        
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    
    # Filter strictly within [START_TS, END_TS]
    df = df[(df["timestamp"] >= START_TS) & (df["timestamp"] <= END_TS)].reset_index(drop=True)
    
    out_file = OUTPUT_DIR / f"{symbol}_1h.parquet"
    df.to_parquet(out_file, index=False)
    
    start_dt = pd.to_datetime(df['timestamp'].min(), unit='ms')
    end_dt = pd.to_datetime(df['timestamp'].max(), unit='ms')
    print(f"[OK] {symbol}: {len(df)} candles | {start_dt} -> {end_dt}", flush=True)
    return df

def main():
    print(f"Starting download for {len(SYMBOLS)} symbols across 2024-2026...")
    total_candles = 0
    for sym in SYMBOLS:
        df = download_symbol_1h(sym)
        total_candles += len(df)
        
    print(f"\nALL 18 SYMBOLS DOWNLOADED! Total candles: {total_candles}")
    print(f"Saved to: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
