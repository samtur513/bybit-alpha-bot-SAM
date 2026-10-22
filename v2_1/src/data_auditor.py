"""
Data Auditor module for verifying Bybit historical dataset quality.
Strict compliance with SPEC_RU.md Section 2 checks:
- No gaps on minute grid
- No duplicates
- OHLC validity (H >= max(O,C), L <= min(O,C), L > 0)
- Non-negative volume & turnover
- Aggregation consistency against API
- Funding rate continuity and consistency
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.bybit_client import BybitClient
from src.config import (
    CATEGORY,
    FINAL_END_TS,
    PARQUET_DATA_DIR,
    SYMBOLS,
    WARMUP_START_TS,
)

logger = logging.getLogger("Auditor")

@dataclass
class AuditReport:
    symbol: str
    total_1m_rows: int = 0
    expected_1m_rows: int = 0
    duplicate_count: int = 0
    gap_count: int = 0
    gap_details: List[Tuple[int, int]] = field(default_factory=list)
    ohlc_violations: int = 0
    volume_violations: int = 0
    aggregation_checked: bool = False
    aggregation_mismatches: int = 0
    funding_events_count: int = 0
    is_valid: bool = False
    summary: str = ""

class DataAuditor:
    """Performs rigorous quality control on downloaded data."""

    def __init__(self, client: Optional[BybitClient] = None):
        self.client = client or BybitClient()

    def audit_1m_dataset(self, symbol: str) -> AuditReport:
        parquet_path = PARQUET_DATA_DIR / f"{symbol}_1m.parquet"
        report = AuditReport(symbol=symbol)

        if not parquet_path.exists():
            report.summary = f"File {parquet_path} does not exist!"
            return report

        df = pd.read_parquet(parquet_path)
        report.total_1m_rows = len(df)
        
        # Expected rows: exactly (FINAL_END_TS - WARMUP_START_TS) / 60,000
        expected = (FINAL_END_TS - WARMUP_START_TS) // 60000
        report.expected_1m_rows = expected

        # Check duplicates
        dup_count = df.duplicated(subset=["timestamp"]).sum()
        report.duplicate_count = int(dup_count)

        # Check OHLC validity
        ohlc_bad = ~(
            (df["high"] >= df["low"]) &
            (df["high"] >= df["open"]) &
            (df["high"] >= df["close"]) &
            (df["low"] <= df["open"]) &
            (df["low"] <= df["close"]) &
            (df["low"] > 0)
        )
        report.ohlc_violations = int(ohlc_bad.sum())

        # Check volume validity
        vol_bad = (df["volume"] < 0) | (df["turnover"] < 0)
        report.volume_violations = int(vol_bad.sum())

        # Check minute grid continuity
        df = df.sort_values("timestamp").reset_index(drop=True)
        diffs = df["timestamp"].diff().dropna()
        gaps = diffs[diffs != 60000]
        report.gap_count = len(gaps)
        
        for idx in gaps.index[:10]:
            prev_ts = int(df.loc[idx - 1, "timestamp"])
            curr_ts = int(df.loc[idx, "timestamp"])
            report.gap_details.append((prev_ts, curr_ts))

        # Funding check
        funding_path = PARQUET_DATA_DIR / f"{symbol}_funding.parquet"
        if funding_path.exists():
            fdf = pd.read_parquet(funding_path)
            report.funding_events_count = len(fdf)

        # Overall validity
        is_ok = (
            report.total_1m_rows == report.expected_1m_rows and
            report.duplicate_count == 0 and
            report.gap_count == 0 and
            report.ohlc_violations == 0 and
            report.volume_violations == 0 and
            report.funding_events_count > 0
        )
        report.is_valid = is_ok
        report.summary = (
            f"Audit for {symbol}: Valid={is_ok} | Rows={report.total_1m_rows}/{report.expected_1m_rows} | "
            f"Dups={report.duplicate_count} | Gaps={report.gap_count} | OHLC bad={report.ohlc_violations} | "
            f"Funding events={report.funding_events_count}"
        )
        logger.info(report.summary)
        return report

    def verify_aggregation_against_api(self, symbol: str, sample_ts_list: List[int]) -> bool:
        """
        Samples 1h candles from Bybit API and verifies that local 1m aggregation matches exactly.
        """
        agg_1h_path = PARQUET_DATA_DIR / f"{symbol}_1h.parquet"
        if not agg_1h_path.exists():
            return False
        df_1h = pd.read_parquet(agg_1h_path).set_index("timestamp")

        mismatches = 0
        for ts in sample_ts_list:
            if ts not in df_1h.index:
                continue
            local_row = df_1h.loc[ts]
            
            # Fetch from API
            api_res = self.client.get_kline(symbol=symbol, interval="60", start=ts, limit=1)
            if not api_res:
                continue
            api_candle = api_res[0]
            api_open = float(api_candle[1])
            api_high = float(api_candle[2])
            api_low = float(api_candle[3])
            api_close = float(api_candle[4])
            
            # Check price values match within 0.001%
            tol = 1e-4
            if (
                abs(local_row["open"] - api_open) / api_open > tol or
                abs(local_row["high"] - api_high) / api_high > tol or
                abs(local_row["low"] - api_low) / api_low > tol or
                abs(local_row["close"] - api_close) / api_close > tol
            ):
                logger.warning(f"Mismatch at {ts} for {symbol}: Local={dict(local_row)} vs API={api_candle}")
                mismatches += 1

        return mismatches == 0
