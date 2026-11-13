# Programmatic Data & Point-in-Time Universe Audit (ECONOMIC-GATE-010)

- **Audit Timestamp (UTC)**: `2026-11-13T15:55:02.688348+00:00`
- **Repository**: `samtur513/bybit-alpha-bot-SAM`
- **Branch**: `research/economic-gate-010`
- **Audit Standard**: Zero-Lookahead Forensic Accounting & Point-in-Time Integrity

## 1. Executive Summary & Verification Invariants

All 18 Bybit Linear Perpetual historical datasets in `data/parquet_2024_2026/` were audited programmatically bar by bar:
- **Total Assets Audited**: 18
- **Total Hourly Bars Audited**: 438,004
- **Timestamp Gaps (diff != 3600s)**: `0`
- **Duplicate Timestamps**: `0`
- **OHLC Invariant Violations (High < Open/Close, Low > Open/Close, Vol < 0)**: `0`
- **Point-in-Time Listing Rule**: RENDERUSDT listed on 2024-07-26 08:00 UTC (1721980800000 ms); excluded prior.

## 2. Asset Integrity Matrix

| Symbol | Status | Row Count | Start Date (UTC) | End Date (UTC) | Gaps | Dups | Bad OHLC | Listing Invariant |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `1000PEPEUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `ADAUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `APTUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `ARBUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `AVAXUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `BNBUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `BTCUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `DOGEUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `ETHUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `INJUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `LINKUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `NEARUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `OPUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `RENDERUSDT` | `VERIFIED_PIT` | `19,634` | `2024-07-26 08:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-07-26 08:00:00+00:00` |
| `SOLUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `SUIUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `TIAUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |
| `XRPUSDT` | `VERIFIED_PIT` | `24,610` | `2024-01-01 00:00:00+00:00` | `2026-10-22 09:00:00+00:00` | `0` | `0` | `0` | `2024-01-01 00:00:00+00:00` |

## 3. Dynamic Universe & Lookahead Constraints

1. **RENDERUSDT Listing Invariant**: RENDERUSDT was listed on Bybit on **2024-07-26 08:00:00 UTC** (19,634 bars). All strategies enforce that RENDERUSDT cannot be evaluated, screened, or traded prior to 2024-07-26.
2. **Dynamic Screener Historical Proxy (v3.0)**:
   - The production dynamic screener design targets 400+ Bybit Linear Perpetuals.
   - However, the repository historical parquet dataset contains 18 assets.
   - Therefore, the 400+ asset claim is officially flagged as `BLOCKED_DATA` for retrospective backtesting. The strategy is audited strictly as an 18-asset dynamic momentum allocation proxy.
