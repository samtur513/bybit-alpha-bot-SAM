# 30-Minute Continuous Enterprise Collector Verification Report (FLEET-REPAIR-009B)

- **Verification Timestamp (UTC)**: `2026-11-13T14:53:38.856178+00:00`
- **Test Standard**: `FLEET-REPAIR-009B Step 3 — Continuous Real Market Data Ingestion`
- **Status Verdict**: **`PASS`**
- **Target Instruments**: `BTCUSDT, ETHUSDT, SOLUSDT` (Bybit V5 Linear & Spot)
- **Actual Continuous Duration**: `1801.12` seconds (30.02 minutes)
- **Continuous Requirement**: `>= 1800.0` seconds (30.0 minutes)
- **Total Records Ingested**: `527,294`
- **Total Raw JSON Written**: `262.23` MB (`274,972,614` bytes)
- **Total Compressed GZ Written**: `37.32` MB (`39,134,829` bytes)
- **Overall Gzip Compression Ratio**: `7.03x`

## 1. Operational Invariants & Telemetry Summary

| Telemetry / Invariant Metric | Measured Value | Standard / Limit | Status |
| :--- | :---: | :---: | :---: |
| **Collection Duration** | `1801.1s` | `>= 1800.0s` | `PASS` |
| **Segment Count (Rotation)** | `11` segments | `>= 6` segments | `PASS` |
| **Dropped Messages** | `0` | `0` | `PASS` |
| **WebSocket Reconnections** | `0` | `<= 2` | `PASS` |
| **Orderbook L2 Sequence Gaps** | `0` | `0` | `PASS` |
| **Peak Ingestion Queue Depth** | `688` | `<= 10,000` | `PASS` |
| **Average CPU Usage** | `20.4%` (Max `79.7%`) | `< 30%` | `PASS` |
| **Average RAM RSS** | `96.9 MB` (Max `100.5 MB`) | `< 512 MB` | `PASS` |
| **Average Network Ingress** | `100.7 KB/s` (Peak `412.3 KB/s`) | Scalable | `PASS` |
| **Average Disk Write Speed** | `148.8 KB/s` | Scalable | `PASS` |

## 2. Segments Manifest (Rotated Gzip Archives)

| Segment File | Records | Raw Bytes | Compressed Bytes | Ratio | SHA-256 Checksum |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `bybit_stream_20261113_142148_seg001.jsonl.gz` | 56,823 | 26,271,535 | 3,453,827 | 7.61x | `aeace9956291a908c41ac7b3874e63ba8c2f3e56bfde31fec15105f546a9e4bb` |
| `bybit_stream_20261113_142619_seg002.jsonl.gz` | 55,696 | 26,270,290 | 3,508,973 | 7.49x | `f673ef1e99dbfa598fc1b78b79c863c66d5510762bc5ec568d0e552f75ec918d` |
| `bybit_stream_20261113_143017_seg003.jsonl.gz` | 48,642 | 26,263,274 | 3,820,045 | 6.88x | `4d29c98c32e73875831494f9b3c357292922606fa936185834fff9709d2daee3` |
| `bybit_stream_20261113_143245_seg004.jsonl.gz` | 50,541 | 26,265,091 | 3,720,689 | 7.06x | `e279247f84ab54db53438c8b7300306bc826125c093181b8fa5d59ceacea33cf` |
| `bybit_stream_20261113_143534_seg005.jsonl.gz` | 45,965 | 26,260,534 | 3,912,122 | 6.71x | `97957480ca6aebecfbdab0f01857cb102d75be8736b1dde83367db0cb6226ff2` |
| `bybit_stream_20261113_143736_seg006.jsonl.gz` | 48,859 | 26,264,465 | 3,800,523 | 6.91x | `70554c157398befbd7848d4758f5a8e3196d14b238258f21d63efdb528287619` |
| `bybit_stream_20261113_144004_seg007.jsonl.gz` | 47,950 | 26,262,417 | 3,860,462 | 6.8x | `409108e8606e408d64c8508f1a184b457cb998d0e3e353a40e63a1f104dd08ef` |
| `bybit_stream_20261113_144222_seg008.jsonl.gz` | 51,081 | 26,267,344 | 3,732,361 | 7.04x | `af5f8d1fb7894f664ee7d9284d4d3d31b6fd6317e76ac9877166c1c4d231648c` |
| `bybit_stream_20261113_144514_seg009.jsonl.gz` | 50,432 | 26,265,290 | 3,754,994 | 6.99x | `16ce4fa84bba751be7ca50fbf19211f0dd79fbe429f53d7c5bebf9ec5440bd14` |
| `bybit_stream_20261113_144805_seg010.jsonl.gz` | 48,944 | 26,263,686 | 3,769,663 | 6.97x | `3416cc2ed48445fa5738712f29fd7f7893d390f87e88a19e666642348d80e03e` |
| `bybit_stream_20261113_145043_seg011.jsonl.gz` | 22,361 | 12,318,688 | 1,801,170 | 6.84x | `75b85133c6aa15a52ed5e0c527b82855bd1619ab0452c2e375ff857532c10824` |

## 3. Orderbook State & Replay Consistency

- **BTCUSDT L2 Book Integrity**: Snapshot synced, 0 sequence gaps detected across continuous 30-minute stream.
- **ETHUSDT L2 Book Integrity**: Snapshot synced, 0 sequence gaps detected across continuous 30-minute stream.
- **SOLUSDT L2 Book Integrity**: Snapshot synced, 0 sequence gaps detected across continuous 30-minute stream.
- **Replay Determinism**: 100% of 527,294 messages parsed and compressed without exception.

## 4. Conclusion & Audit Sign-Off

The 30-minute continuous load test completed with `1801.12` seconds of uninterrupted ingestion.
Ingested 527,294 messages across BTCUSDT, ETHUSDT, and SOLUSDT with zero drops and zero L2 sequence gaps across all 11 rotated segments.
Status: **`PASS`**.
