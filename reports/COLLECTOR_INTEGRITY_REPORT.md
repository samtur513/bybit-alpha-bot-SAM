# Enterprise Collector Integrity & Load Test Report

- **Execution Timestamp (UTC)**: `2026-11-13T11:03:02.967702+00:00`
- **Test Duration**: `74.12` seconds (Target: >60s)
- **Target Instruments**: `BTCUSDT, ETHUSDT, SOLUSDT`
- **Streams Captured**: Linear L2 Depth 50, Public Trades, Tickers/Funding, Spot BBO (orderbook.1)
- **Total Records Ingested**: `10,842`
- **Total Raw JSON Written**: `4.46` MB (`4,678,879` bytes)
- **Total Compressed GZ Written**: `0.56` MB (`586,257` bytes)
- **Overall Compression Ratio**: `7.98x`

## 1. Verification of Operational Invariants

| Invariant Check | Requirement | Measured Value | Status |
| :--- | :--- | :--- | :--- |
| **Segment Rotation** | Automatic rotation on duration limit (30s) | `3` segments rotated | `VERIFIED PASS` |
| **Gzip Compression** | Auto-compression `.tmp` -> `.jsonl.gz` | All segments compressed (`7.98x`) | `VERIFIED PASS` |
| **SHA-256 Hashing** | SHA-256 computed on raw & gz | 100% manifest entries hashed | `VERIFIED PASS` |
| **L2 Sequence Continuity** | 0 sequence gaps (`u == last_u + 1`, `seq > last_seq`) | `0` gaps across all books | `VERIFIED PASS` |
| **Memory Stability (RAM)** | No unbounded growth / leak | Start: `93.0` MB, End: `94.0` MB (Delta: `+1.0` MB) | `VERIFIED PASS` |
| **Event Loop Health (CPU)** | Low average CPU load | Avg: `9.4%`, Max: `19.5%` | `VERIFIED PASS` |
| **Ingestion Backpressure** | 0 message drops under normal capacity | `0` dropped (Max Queue Depth: `23`) | `VERIFIED PASS` |
| **Disk Watermark Protection** | Abort safely if disk free < 1.0 GB | Active watchdog, free space verified | `VERIFIED PASS` |

## 2. Orderbook Reconstruction & Sequence Continuity

| Symbol | Snapshots Received | Sequence Gaps | Best Bid | Best Ask | Spread % | Book Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `BTCUSDT` | `1` | `0` | `69269.9` | `69270.0` | `0.0001%` | `HEALTHY` |
| `ETHUSDT` | `1` | `0` | `1949.69` | `1949.7` | `0.0005%` | `HEALTHY` |
| `SOLUSDT` | `1` | `0` | `80.38` | `80.39` | `0.0124%` | `HEALTHY` |

## 3. Storage Manifest Summary

| Segment | Filename | Records | Raw Size | GZ Size | Ratio | Raw SHA-256 | GZ SHA-256 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `1` | `bybit_stream_20261113_105943_seg001.jsonl.gz` | `4,465` | `1,949,107` B | `245,083` B | `7.95x` | `dbe9ffdd387b...` | `c87215785fe8...` |
| `2` | `bybit_stream_20261113_110013_seg002.jsonl.gz` | `4,797` | `2,088,807` B | `264,182` B | `7.91x` | `d99e7a00a909...` | `40d508ef5a81...` |
| `3` | `bybit_stream_20261113_110044_seg003.jsonl.gz` | `1,580` | `640,965` B | `76,992` B | `8.33x` | `43dffd22e8cc...` | `ce981d768553...` |

## 4. Live Telemetry Profiles

- **Avg Network Ingestion**: `38.4` KB/s (Peak: `69.0` KB/s)
- **Avg Disk Write Throughput**: `61.0` KB/s
- **Peak Queue Depth**: `23` items (Buffer capacity: 10,000)
- **Peak Process Memory**: `94.0` MB RSS
