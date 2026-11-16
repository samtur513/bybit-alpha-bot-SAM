# TORG_BOT Trading Control Center v1.0 — Performance & Load Benchmark Report

**Benchmark Executed:** 2026-11-16T12:07:38Z  
**Platform:** Windows NT / Python 3.12 (Multi-core)  
**Database Scale Tested:**
- Native: 15,000 journal events, full portfolio state
- Shadow: 100,000 saves in `shadow_state_journal`, 1,800 dedup events
- R2: 25,000 events in `events` (SNAPSHOT, POSITION_MATRIX, HEARTBEAT)

---

## 1. Data Retrieval Latency (Cold Start against Large SQLite DBs)

| Component | Target SLA | Measured Cold Latency | Status |
| :--- | :--- | :--- | :--- |
| **Scanner Evaluation** | < 250 ms | **48.38 ms** | PASS |
| **Positions Data** | < 100 ms | **9.1 ms** | PASS |
| **Trade Journal** | < 100 ms | **9.84 ms** | PASS |
| **Analytics & Equity** | < 150 ms | **32.61 ms** | PASS |
| **System Health** | < 50 ms | **7.76 ms** | PASS |
| **Total Cold Data Pipeline** | < 500 ms | **107.68 ms** | **EXCELLENT** |

---

## 2. HTML Rendering Performance (All 6 Tabs)

- **Cold HTML Render (un-cached):** `19.56 ms` (Target: < 500 ms)
- **Warm / Cached Render:** `16.43 ms` (Target: < 150 ms)
- **Rendered Payload Size:** `69.56 KB`

---

## 3. Concurrency & Throughput Stress Test

| Metric | Concurrency = 10 | Concurrency = 50 |
| :--- | :--- | :--- |
| **Total Requests** | 100 | 200 |
| **Throughput (QPS)** | **60.6 req/sec** | **44.8 req/sec** |
| **Mean Latency** | 153.72 ms | 795.95 ms |
| **Median (P50)** | 141.89 ms | 605.48 ms |
| **95th Percentile (P95)**| 290.97 ms | 2193.91 ms |
| **Max Latency** | 409.51 ms | 2944.65 ms |

---

## 4. Memory Stability & Resource Leak Assessment

- **Process Memory Before Test:** `1434.14 KB`
- **Process Memory After 500 Consecutive Cycles:** `1441.12 KB`
- **Net RAM Delta:** `6.97 KB`
- **Peak Traced RAM:** `11761.08 KB`
- **Memory Leak Status:** `NO LEAK DETECTED (STABLE)`

---

## 5. Conclusion
Trading Control Center v1.0 comfortably exceeds all throughput and latency targets even when operating over 100,000+ historical records. The read-only URI connections and in-memory caches protect VPS resources from exhaustion.
