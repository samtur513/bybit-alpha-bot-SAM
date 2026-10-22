"""
Data Downloader for Bybit V5 Market Data.
Implements resumable downloading, SHA-256 tracking, raw JSON logging,
SQLite metadata recording, and Parquet storage according to SPEC_RU.md Section 2.
"""
import concurrent.futures
import datetime
import gzip
import hashlib
import json
import logging
import sqlite3
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from src.config import (
    CATEGORY,
    DATA_DIR,
    DB_PATH,
    FINAL_END_TS,
    PARQUET_DATA_DIR,
    RAW_DATA_DIR,
    SYMBOLS,
    WARMUP_START_TS,
)
from src.bybit_client import BybitClient, BybitAPIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Downloader")

_db_lock = threading.Lock()

def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize SQLite tables for data provenance and metadata tracking."""
    with _db_lock:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            query_params TEXT NOT NULL,
            download_time_utc TEXT NOT NULL,
            start_ts INTEGER,
            end_ts INTEGER,
            row_count INTEGER NOT NULL,
            sha256_hash TEXT NOT NULL,
            raw_file_path TEXT NOT NULL,
            status TEXT NOT NULL,
            error_msg TEXT
        );
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_raw_downloads_type_sym 
        ON raw_downloads(data_type, symbol, start_ts);
        """)
        conn.commit()
        conn.close()

class DataDownloader:
    """Manages full history downloading and archival according to SPEC_RU.md."""

    def __init__(self, client: Optional[BybitClient] = None, max_workers: int = 12):
        self.client = client or BybitClient()
        self.max_workers = max_workers
        init_db()

    def _save_raw_chunk(
        self,
        data_type: str,
        symbol: str,
        params: Dict[str, Any],
        raw_text: str,
        rows: List[Any],
        start_ts: Optional[int],
        end_ts: Optional[int],
    ) -> str:
        """Saves raw JSON response compressed and records metadata in SQLite."""
        sha256_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        download_time_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        filename = f"{data_type}_{symbol}_{start_ts or 'none'}_{end_ts or 'none'}_{sha256_hash[:8]}.json.gz"
        raw_path = RAW_DATA_DIR / filename
        
        with gzip.open(raw_path, "wt", encoding="utf-8") as f:
            f.write(raw_text)

        with _db_lock:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO raw_downloads 
            (data_type, symbol, query_params, download_time_utc, start_ts, end_ts, row_count, sha256_hash, raw_file_path, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data_type,
                symbol,
                json.dumps(params, sort_keys=True),
                download_time_utc,
                start_ts,
                end_ts,
                len(rows),
                sha256_hash,
                str(raw_path),
                "SUCCESS",
                None
            ))
            conn.commit()
            conn.close()
        return sha256_hash

    def download_instruments_info(self) -> Dict[str, Any]:
        """Downloads instrument parameters and rules for linear BTCUSDT & ETHUSDT."""
        logger.info("Downloading instruments info...")
        results = {}
        for sym in SYMBOLS:
            params = {"category": CATEGORY, "symbol": sym}
            url = f"{self.client.base_url}/v5/market/instruments-info"
            res = self.client.session.get(url, params=params, timeout=self.client.timeout)
            res.raise_for_status()
            raw_text = res.text
            res_json = res.json()
            if res_json.get("retCode") != 0:
                raise BybitAPIError(res_json.get("retCode"), res_json.get("retMsg"), url)
            
            items = res_json.get("result", {}).get("list", [])
            self._save_raw_chunk("instruments_info", sym, params, raw_text, items, None, None)
            if items:
                results[sym] = items[0]
                
        # Save summary JSON
        with open(DATA_DIR / "instruments_info.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        logger.info(f"Instruments info saved for {list(results.keys())}")
        return results

    def download_funding_history(self, symbol: str) -> pd.DataFrame:
        """Downloads complete funding rate history for evaluation and warmup range."""
        logger.info(f"Downloading funding history for {symbol}...")
        url = f"{self.client.base_url}/v5/market/funding/history"
        
        all_records = []
        curr_end = FINAL_END_TS
        
        while curr_end > WARMUP_START_TS:
            params = {
                "category": CATEGORY,
                "symbol": symbol,
                "endTime": curr_end,
                "limit": 200,
            }
            res = self.client.session.get(url, params=params, timeout=self.client.timeout)
            res.raise_for_status()
            raw_text = res.text
            res_json = res.json()
            if res_json.get("retCode") != 0:
                raise BybitAPIError(res_json.get("retCode"), res_json.get("retMsg"), url)
            
            items = res_json.get("result", {}).get("list", [])
            if not items:
                break
                
            min_ts = min(int(x["fundingRateTimestamp"]) for x in items)
            max_ts = max(int(x["fundingRateTimestamp"]) for x in items)
            
            self._save_raw_chunk("funding", symbol, params, raw_text, items, min_ts, max_ts)
            all_records.extend(items)
            
            if min_ts <= WARMUP_START_TS:
                break
            curr_end = min_ts - 1

        df = pd.DataFrame(all_records)
        if df.empty:
            logger.warning(f"No funding records returned for {symbol}!")
            return df

        df["fundingRateTimestamp"] = df["fundingRateTimestamp"].astype(int)
        df["fundingRate"] = df["fundingRate"].astype(float)
        df = df.drop_duplicates(subset=["fundingRateTimestamp"]).sort_values("fundingRateTimestamp").reset_index(drop=True)
        
        parquet_path = PARQUET_DATA_DIR / f"{symbol}_funding.parquet"
        df.to_parquet(parquet_path, index=False)
        logger.info(f"Saved {len(df)} funding records to {parquet_path}")
        return df

    def _fetch_kline_chunk(self, symbol: str, start_ts: int, limit: int = 1000) -> Tuple[int, str, List[List[str]]]:
        """Fetches a single chunk of 1m klines starting from start_ts."""
        url = f"{self.client.base_url}/v5/market/kline"
        params = {
            "category": CATEGORY,
            "symbol": symbol,
            "interval": "1",
            "start": start_ts,
            "limit": limit,
        }
        for attempt in range(1, 6):
            try:
                res = self.client.session.get(url, params=params, timeout=self.client.timeout)
                res.raise_for_status()
                raw_text = res.text
                res_json = res.json()
                ret_code = res_json.get("retCode", -1)
                if ret_code != 0:
                    if ret_code in [10002, 10006, 10016, 10018] and attempt < 5:
                        time.sleep(1.0 * (2 ** attempt))
                        continue
                    raise BybitAPIError(ret_code, res_json.get("retMsg", ""), url)
                items = res_json.get("result", {}).get("list", [])
                return start_ts, raw_text, items
            except Exception as e:
                if attempt == 5:
                    raise
                time.sleep(1.0 * (2 ** attempt))

    def download_1m_klines(self, symbol: str, batch_size: int = 50) -> pd.DataFrame:
        """
        Downloads complete 1-minute klines from WARMUP_START_TS to FINAL_END_TS
        using concurrent chunk requests, raw archiving, and Parquet storage.
        """
        logger.info(f"Downloading 1m klines for {symbol} from {WARMUP_START_TS} to {FINAL_END_TS}...")
        
        chunk_span_ms = 1000 * 60 * 1000
        all_starts = []
        curr = WARMUP_START_TS
        while curr < FINAL_END_TS:
            all_starts.append(curr)
            curr += chunk_span_ms

        logger.info(f"Total chunks for {symbol}: {len(all_starts)}")
        
        with _db_lock:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            cursor.execute("SELECT start_ts FROM raw_downloads WHERE data_type='kline_1m' AND symbol=? AND status='SUCCESS'", (symbol,))
            done_starts = set(r[0] for r in cursor.fetchall())
            conn.close()
        
        pending_starts = [s for s in all_starts if s not in done_starts]
        logger.info(f"Already completed: {len(done_starts)}, pending: {len(pending_starts)}")

        all_rows = []
        # If there were already completed chunks, read them first
        if done_starts:
            logger.info(f"Loading {len(done_starts)} existing cached chunks from database...")
            with _db_lock:
                conn = sqlite3.connect(str(DB_PATH))
                cursor = conn.cursor()
                cursor.execute("SELECT raw_file_path FROM raw_downloads WHERE data_type='kline_1m' AND symbol=? AND status='SUCCESS' ORDER BY start_ts ASC", (symbol,))
                cached_files = [r[0] for r in cursor.fetchall()]
                conn.close()
            for fpath in cached_files:
                try:
                    with gzip.open(fpath, "rt", encoding="utf-8") as f:
                        j = json.load(f)
                        all_rows.extend(j.get("result", {}).get("list", []))
                except Exception as e:
                    logger.error(f"Error reading cached chunk {fpath}: {e}")

        # Concurrently fetch pending chunks in batches
        for i in range(0, len(pending_starts), batch_size):
            batch = pending_starts[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(pending_starts) + batch_size - 1) // batch_size
            logger.info(f"Fetching batch {batch_num}/{total_batches} ({len(batch)} chunks) for {symbol}...")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_start = {executor.submit(self._fetch_kline_chunk, symbol, s): s for s in batch}
                for future in concurrent.futures.as_completed(future_to_start):
                    s_ts, raw_text, items = future.result()
                    all_rows.extend(items)
                    end_ts = s_ts + chunk_span_ms - 60000
                    params = {"category": CATEGORY, "symbol": symbol, "interval": "1", "start": s_ts, "limit": 1000}
                    self._save_raw_chunk("kline_1m", symbol, params, raw_text, items, s_ts, end_ts)

        logger.info(f"Assembling normalized Parquet dataset for {symbol} ({len(all_rows)} raw records)...")

        # Bybit kline list: [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
        df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = df["timestamp"].astype(int)
        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = df[col].astype(float)

        # Filter strictly within [WARMUP_START_TS, FINAL_END_TS)
        df = df[(df["timestamp"] >= WARMUP_START_TS) & (df["timestamp"] < FINAL_END_TS)]
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        parquet_path = PARQUET_DATA_DIR / f"{symbol}_1m.parquet"
        df.to_parquet(parquet_path, index=False)
        logger.info(f"Successfully assembled {len(df)} 1m candles for {symbol} into {parquet_path}")
        return df

    def aggregate_candles(self, df_1m: pd.DataFrame, interval_hours: int) -> pd.DataFrame:
        """
        Aggregates 1m candles into 1h or 4h closed candles according to exact rules.
        """
        df = df_1m.copy()
        df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        
        freq = f"{interval_hours}h"
        grouped = df.groupby(pd.Grouper(key="dt", freq=freq, closed="left", label="left"))
        
        agg_df = grouped.agg({
            "timestamp": "first",
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "turnover": "sum",
        }).dropna().reset_index(drop=True)
        
        agg_df["timestamp"] = agg_df["timestamp"].astype(int)
        return agg_df

    def build_all_datasets(self) -> None:
        """Downloads and builds all required datasets for BTCUSDT and ETHUSDT."""
        self.download_instruments_info()
        for sym in SYMBOLS:
            self.download_funding_history(sym)
            df_1m = self.download_1m_klines(sym)
            
            # Aggregate 1h and 4h
            df_1h = self.aggregate_candles(df_1m, 1)
            df_4h = self.aggregate_candles(df_1m, 4)
            
            df_1h.to_parquet(PARQUET_DATA_DIR / f"{sym}_1h.parquet", index=False)
            df_4h.to_parquet(PARQUET_DATA_DIR / f"{sym}_4h.parquet", index=False)
            logger.info(f"Saved {len(df_1h)} 1h candles and {len(df_4h)} 4h candles for {sym}")
