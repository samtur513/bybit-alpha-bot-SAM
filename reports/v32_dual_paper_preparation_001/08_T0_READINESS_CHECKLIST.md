# 08_T0_READINESS_CHECKLIST.md
## Pre-Flight Verification & T0 Go/No-Go Gate

- **Audit Standard:** TORG-FLEET-SAFE-STOP-001 + V32-DUAL-PAPER-START-001
- **Target Moment:** Synchronized $T_0$ UTC
- **Evaluator:** Quant Systems Verification Lead

---

### 1. Mandatory Pre-Flight Verification Gates

| Gate # | Gate Name | Requirement | Evidence / Verification Method | Status |
| :---: | :--- | :--- | :--- | :---: |
| **G-01** | **Code Freeze & Parity** | Signal generation logic in System A and B must be mathematically identical. | Byte-for-byte matching `calc_indicators()` and `process_candle()` rules. | **READY** |
| **G-02** | **Pre-Reset Archive** | Original v3.2 state fully backed up before any reset action. | `V32_ORIGINAL_PRE_RESET_ARCHIVE.tar.gz` verified & sealed (SHA256 recorded). | **READY** |
| **G-03** | **Zero Real Orders** | Confirmed 0 live exchange API keys and 0 live orders. | Bybit API keys configured in read-only / paper mode. | **READY** |
| **G-04** | **Initial Capital $300** | Initial realized equity set to exactly 300.000000 USDT on both. | State files initialized with `realized_equity: 300.0`, `capital: 300.0`. | **READY** |
| **G-05** | **Zero Initial Exposure**| Zero open positions and zero pending orders at $T_0$. | `open_positions: {}`, `pending_orders: {}` in state JSON. | **READY** |
| **G-06** | **Port Availability** | Ports 8083 (or 8081) and 8084 verified bound and listening. | `SO_REUSEADDR` enforced; zero port collisions. | **READY** |
| **G-07** | **Bybit Feed Health** | Public WebSocket ping/pong keepalive task active (20s interval). | Bybit V5 public linear stream receiving live trade ticks. | **READY** |
| **G-08** | **PIT Signal Alignment**| First signal evaluated strictly on the first closed 1H bar after $T_0$. | `shift(1)` enforcement verified by automated test. | **READY** |
| **G-09** | **Ledger Persistence** | Atomic file writes with exponential backoff retries. | Windows & Linux atomic persistence protocol active. | **READY** |
| **G-10** | **Telemetry UI** | Dashboards on 8083/8084 exposing `/api/status` with T0 timestamp. | Web dashboard renders Native vs Shadow KPIs. | **READY** |

---

### 2. Go / No-Go Decision Rule

- If all 10 gates are **READY**: **`GO FOR SYNCHRONIZED T0 START`**.
- If any gate fails: **`NO-GO`**. Execution halted; state restored from verified backup.
