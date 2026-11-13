# Bybit Public Market Data Capture Smoke Test Report

**Execution Timestamp**: 2026-11-13 07:00:46 UTC  
**Auditor Protocol**: FLEET-ALPHA-VERIFY-005 Smoke Gate  
**Duration**: 20.42 seconds  
**Log File**: `bybit_live_smoke.jsonl` (3997 entries, 1736.6 KB)

---

## 1. Executive Summary

| Parameter | Observed Value | Verification Target | Status |
| :--- | :--- | :--- | :--- |
| **Connection Protocol** | Bybit V5 Public WS (Linear + Spot) | Zero-credentials public feed | **PASS** |
| **Topic Subscription** | `orderbook.50`, `publicTrade`, `tickers`, `orderbook.1` | Complete L2, Trades, Funding, Spot BBO | **PASS** |
| **Total Events Captured** | **3997 events** | >= 50 events in 20s | **PASS** |
| **Topic Breakdown** | Orderbook: 2178, Trades: 893, Tickers: 513, Spot: 885 | Non-zero across all channels | **PASS** |
| **Latency Mean** | **77.8 ms** (Median: 50.0 ms, P95: 213.0 ms) | Network transit + processing <= 500 ms | **PASS** |
| **Orderbook Integrity** | All 3 symbols synchronized, 0 sequence gaps | 100% snapshot/delta consistency | **PASS** |
| **Deterministic Replay** | 100 events replayed into Execution Adapter | Penny-perfect conservation | **PASS** |

---

## 2. Latency & Network Transmission Metrics

Latency is calculated rigorously as `local_recv_ts - exchange_ts`:

- **Minimum Latency**: 21.0 ms
- **Mean Latency**: 77.8 ms
- **Median Latency (P50)**: 50.0 ms
- **95th Percentile (P95)**: 213.0 ms
- **Maximum Latency**: 1222.0 ms

---

## 3. Real-Time Orderbook L2 Depth Reconstruction

| Symbol | Synchronized | Bid Levels | Ask Levels | Best Bid | Best Ask | Spread | Gaps Detected |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **BTCUSDT** | True | 50 | 50 | 69096.5 | 69096.6 | 0.1000 | 0 |
| **ETHUSDT** | True | 50 | 50 | 1939.49 | 1939.5 | 0.0100 | 0 |
| **SOLUSDT** | True | 50 | 50 | 80.2 | 80.21 | 0.0100 | 0 |

---

## 4. Spot BBO Cross-Feed Verification

| Symbol | Spot Bid | Spot Ask | Spot Mid | Perp Mid | Basis (Perp - Spot) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **BTCUSDT** | 69130.3 | 69130.4 | 69130.3500 | 69096.5500 | -33.8000 |
| **ETHUSDT** | 1940.41 | 1940.42 | 1940.4150 | 1939.4950 | -0.9200 |
| **SOLUSDT** | 80.25 | 80.26 | 80.2550 | 80.2050 | -0.0500 |

---

## 5. Sample Raw Event Log Entries

```json
{
  "exchange_ts": 1794553227445,
  "recv_ts": 1794553227624,
  "latency_ms": 179.0,
  "topic": "orderbook.1.BTCUSDT",
  "raw": {
    "topic": "orderbook.1.BTCUSDT",
    "ts": 1794553227445,
    "type": "snapshot",
    "data": {
      "s": "BTCUSDT",
      "b": [
        [
          "69112",
          "0.249901"
        ]
      ],
      "a": [
        [
          "69112.1",
          "0.075372"
        ]
      ],
      "u": 7918992,
      "seq": 117350406553
    },
    "cts": 1794553227443
  }
}
{
  "exchange_ts": 1794553227358,
  "recv_ts": 1794553227625,
  "latency_ms": 267.0,
  "topic": "orderbook.1.ETHUSDT",
  "raw": {
    "topic": "orderbook.1.ETHUSDT",
    "ts": 1794553227358,
    "type": "snapshot",
    "data": {
      "s": "ETHUSDT",
      "b": [
        [
          "1940.15",
          "15.83406"
        ]
      ],
      "a": [
        [
          "1940.16",
          "5.72973"
        ]
      ],
      "u": 7307811,
      "seq": 186856491821
    },
    "cts": 1794553227356
  }
}
{
  "exchange_ts": 1794553227426,
  "recv_ts": 1794553227625,
  "latency_ms": 199.0,
  "topic": "orderbook.1.SOLUSDT",
  "raw": {
    "topic": "orderbook.1.SOLUSDT",
    "ts": 1794553227426,
    "type": "snapshot",
    "data": {
      "s": "SOLUSDT",
      "b": [
        [
          "80.23",
          "127.71"
        ]
      ],
      "a": [
        [
          "80.24",
          "259.8783"
        ]
      ],
      "u": 4479214,
      "seq": 168609149047
    },
    "cts": 1794553227419
  }
}
```

---

## 6. Deterministic Replay & Financial Verification

- **Replayed Records**: 100
- **Simulated Orders Executed**: 2
- **Financial Conservation Invariant**: `True`
- **Discrepancy**: `0.0` (Penny-perfect)

**Conclusion**: The Bybit V5 public market data collector is fully operational, establishes public WebSocket feeds without exchange credentials, reconstructs L2 orderbooks without gaps, and deterministically feeds the Shadow Paper execution engine.
