# FINAL-START-BLOCKERS-009: VERIFICATION REPORT & VPS PREFLIGHT PLAN

**Repository:** `samtur513/bybit-alpha-bot-SAM`  
**Branch:** `fix/final-start-blockers-009`  
**Base Commit:** `07433bf197460093df7fc0aa21651b237b03960a`  
**Status:** `READY_FOR_VPS_PREFLIGHT`  
**Target T0 (UTC):** `2026-11-17T00:00:00+00:00`  
**Initial Capital (All Systems):** `300.000000 USDT`  

---

## 1. Executive Summary

This protocol conclusively resolves the remaining architectural and execution blockers identified by the independent audit of commit `b9fbaf6`, establishing verifiable technical readiness across all three independent Paper-trading systems:

1. **v3.2 Native Paper** (`BYBIT_LINEAR_PERPETUAL`):
   - **Unified $300 Account**: Real realized cash, equity, fees, funding, and open positions from both Trend (Donchian Breakout) and Range (Mean Reversion) engines are accounted as ONE unified research account of $300.000000 USDT.
   - **Leverage & Risk Budget Guard**: Total gross notional across both engines is constrained by the 3.5x leverage cap (`gross_notional <= combined_equity * 3.5`), and mutual exclusion prevents both engines from holding the same symbol concurrently.
   - **Atomic Tick Exit Persistence**: Single-transaction SQLite commit across `native_trades`, `native_journal`, and `native_state_journal` during `update_quotes()`. A process crash immediately after commit cannot resurrect closed positions.
   - **Engine-Level T0 Protection**: `schedule_active=False` or `bar_close_ms < t0_ms` strictly blocks execution, charges $0.00 fees, and preserves initial capital.
   - **Historical Gap PIT Execution**: Historical catch-up candles execute strictly at candle close (`Decimal(str(bar["close"]))`), completely ignoring live ticker prices.
2. **v3.2 Realistic Shadow** (`BYBIT_LINEAR_PERPETUAL`):
   - **L2 Orderbook Freshness**: Validated against execution timestamp; if any held position lacks confirmed orderbook depth within 60s, telemetry reports `STALE` instead of `FRESH`.
   - **Strict 8-Hour Funding**: Accrued strictly at 00:00, 08:00, 16:00 UTC boundaries with `(symbol, window_id)` deduplication, eliminating skips and double counts.
   - **VIP0 Retail Fees**: Standardized to retail tier: 0.100% taker, 0.036% maker without unconfirmed MNT discounts.
3. **CryptoLab R2-SHADOW** (`BYBIT_SPOT_LONG_CASH_NO_LEVERAGE`):
   - **Authentic Observer Runtime**: Commit `313a908782271a6e1fffaee83be7ea1a616337cb` verified via frozen manifest (`verify_frozen()`).
   - **Real Recovery Export**: `recovery_scenario.experiment()` updates all 24 profile accounts offline, exporting authentic `telemetry.json` consumed strictly read-only by `R2TelemetryConsumer`.
4. **Three-System Comparative Dashboard** (Port 8099):
   - Physical separation between Linear Perpetuals (Systems A & B) and Spot Cash (System C).
   - Localhost-only binding (`127.0.0.1:8099`), zero pooling of PnL, truthful reporting of stale or unvalued data.

All 42 test cases across 5 test suites passed 100% (including all 12 criteria in `tests/test_final_start_blockers_009.py`).

---

## 2. Independent Audit Patch Remediations (Commit `b9fbaf6` Follow-up)

### 2.1 Unified Trend + Range Accounting in Native
* **Problem:** Native previously tracked Trend and Range submodules with disparate realized equity counters, risking capital double-spending and uncoordinated leverage.
* **Remediation:**
  - Implemented `get_unified_cash()`, `get_unified_unrealized()`, and `get_unified_equity()` in `NativePaperRunner`:
    $$\text{Cash}_{\text{unified}} = \$300.00 + \text{NetPnL}_{\text{trend}} + \text{NetPnL}_{\text{range}}$$
  - Implemented `get_total_gross_notional()` enforcing gross notional $\le \text{Equity}_{\text{unified}} \times 3.5$.
  - Enforced mutual exclusion: before opening a position in one submodule, the runner verifies the symbol is not held or pending in the other submodule.
  - Telemetry exports unified cash, equity, fees, funding, and merged `positions_detail`.

### 2.2 Atomic State Saving on Tick Exits (Anti-Resurrection)
* **Problem:** Tick-level exits in `update_quotes()` updated in-memory state but left an asynchronous window before disk JSON write. A crash caused closed positions to resurrect on restart.
* **Remediation:**
  - `update_quotes()` now performs a synchronous atomic commit across `native_trades`, `native_journal`, and `native_state_journal` in a single SQLite transaction (`with conn:`).
  - On restart, `load_state()` checks `native_state_journal` and restores the authoritative closed position state even if the JSON file is stale or unwritten.
  - Verified via fault injection `fault_injection_point="after_tick_commit_before_json"`.

### 2.3 VIP0 Fee Alignment & Strict 8-Hour Funding Accrual
* **Problem:** Fees were using VIP1/MNT discounted rates (0.055% / 0.020%) without confirmed exchange tier, and funding was evaluated arbitrarily on every bar.
* **Remediation:**
  - Aligned all fee constants to Bybit Retail VIP0:
    * Taker Fee: `0.00100` (0.100%)
    * Maker Fee: `0.00036` (0.036%)
  - Funding calculation restricted strictly to 8-hour intervals (`timestamp_ms % 28_800_000 == 0` for 00:00, 08:00, 16:00 UTC).
  - Added `settled_funding_windows` set deduplicating by `(symbol, window_id)`.

### 2.4 Honest Disclosure: Remaining Differences of Native from Original Dual-Regime v3.2
We explicitly record that Native Paper v3.2 is an autonomous research implementation and does **NOT** claim 100% full live decision parity with the original production bot on Port 8083 prior to live side-by-side decision logging. The specific architectural differences are:
1. **Universe Screener Rotation**: Original v3.2 uses dynamic universe screening across 400+ pairs rotating periodically; Native Paper currently operates on a defined multi-symbol universe (e.g. 18 canonical pairs or configured subset) to guarantee deterministic test repeatability.
2. **Candle Timeframes & Intervals**: Original v3.2 evaluates 1h Donchian Breakout with 4h macro trend filters and 15m range mean reversion; Native Paper standardizes internal candle ingest to 1h bars with tick-level orderbook accompanying.
3. **Execution Semantics**: Original v3.2 routes via live Bybit private WebSocket/REST API with Post-Only limit retests; Native Paper simulates zero-lookahead fills at bar close or discrete L2 orderbook ladders.

### 2.5 Authentic CryptoLab Recovery Export Integration
* **Problem:** Integration tests previously read static `telemetry.example.json` instead of dynamic runtime export.
* **Remediation:**
  - Integrated `recovery_scenario.experiment()` directly into `test_5`:
    * Verified frozen manifest `FROZEN_MANIFEST.json` (source commit `815d6884980791309cb37770ac96a9a93a2c48c0`).
    * Generated real `observer.sqlite` across 24 profile accounts with zero network requests.
    * Exported authentic `telemetry.json` via `telemetry.export()`.
    * Verified `R2TelemetryConsumer` parses the real 4.3 KB exported file with 0 errors.

### 2.6 Historical Network Gap PIT Execution & L2 Freshness
* **Problem:** In Native, historical catch-up bars were checking `self.latest_prices` (live price) for stop/target checks instead of historical candle close.
* **Remediation:**
  - Added `is_historical_catchup: bool = False` flag to `ingest_closed_bar()`.
  - When `is_historical_catchup=True`, execution price defaults strictly to `Decimal(str(bar["close"]))`.
  - In Shadow, L2 freshness verifies that all currently held positions have confirmed orderbook snapshots $\le 60$s old before reporting `FRESH`.

---

## 3. Comprehensive Verification Test Results

Test suite: `tests/test_final_start_blockers_009.py`

| # | Criterion | Test Case | Status |
| :---: | :--- | :--- | :---: |
| 1 | All 3 accounts start at 300 USDT | `test_1_all_three_accounts_start_at_300_usdt` | **PASSED** |
| 2 | Zero trades before T0, equity invariant $300.00 across restarts | `test_2_zero_trades_before_t0_and_equity_preserved` | **PASSED** |
| 3 | Closed candle at/after T0 automatically triggers decisions | `test_3_candle_at_or_after_t0_triggers_auto_decisions` | **PASSED** |
| 4 | Native & Shadow accompany positions & update MTM on ticks | `test_4_live_ticks_accompany_positions_and_update_equity` | **PASSED** |
| 5 | CryptoLab authentic Observer updates 24 profiles & exports real telemetry | `test_5_authentic_cryptolab_observer_and_24_profiles` | **PASSED** |
| 6 | Native crash after SQLite commit before JSON recovers state | `test_6_native_crash_after_sqlite_commit_before_json_recovers_state` | **PASSED** |
| 7 | Network gap > 3 hours catches up without fake L2 depth | `test_7_network_gap_over_3_hours_recovers_chronologically` | **PASSED** |
| 8 | Dashboard displays 3 systems with Spot/Perp separation & VIP0 fees | `test_8_three_system_dashboard_spot_perp_isolation_and_telemetry` | **PASSED** |
| 9 | Unified Trend + Range accounting in Native ($300 base, 3.5x leverage cap) | `test_9_unified_trend_range_accounting_and_risk_budget_cap` | **PASSED** |
| 10 | Atomic state saving on tick exit prevents position resurrection | `test_10_atomic_state_saving_on_tick_exit_no_resurrection` | **PASSED** |
| 11 | VIP0 fee alignment (0.100% / 0.036%) and 8h funding deduplication | `test_11_vip0_fee_alignment_and_8h_funding_dedup` | **PASSED** |
| 12 | Historical gap execution uses candle close and detects stale L2 | `test_12_historical_gap_execution_uses_candle_close_and_l2_stale` | **PASSED** |

### Full Test Suite Summary
```text
tests/test_final_start_blockers_009.py       12 PASSED (100%)
tests/test_forward_live_data_gate_008.py      6 PASSED (100%)
tests/test_real_engine_wiring_007.py          7 PASSED (100%)
tests/test_r2_telemetry_consumer.py          11 PASSED (100%)
tests/test_three_system_dashboard.py          6 PASSED (100%)
======================== 42 passed in 59.35s ========================
```

---

## 4. Safe Read-Only VPS Preflight Tools & Checklist

Two dedicated read-only verification utilities have been prepared:
1. `tools/vps_preflight_readonly_check.py`: Pure Python audit utility checking resources, services, latency, ports, SQLite integrity, and codebase immutability.
2. `tools/vps_preflight_readonly.sh`: Non-destructive companion bash script for operator execution on VPS `150.241.98.70`.

### Strict Read-Only Preflight Guardrails
1. **Zero Fleet Interference**:
   - Historical production v3.2 on Port 8083 (`bybit-v32.service`) is strictly protected and left untouched.
   - Zero `systemctl stop`, `systemctl disable`, or kill signals targeting any running fleet services.
2. **Zero Modifying Commands**:
   - Zero POST/PUT requests, zero database write operations (`file:...?...mode=ro` enforced on all SQLite checks).
3. **Localhost-Only Probes**:
   - Non-destructive TCP `connect_ex` probes on ports 8080-8098.

### Operator Execution on VPS `150.241.98.70`
```bash
# Run non-destructive read-only preflight audit
bash tools/vps_preflight_readonly.sh

# Or directly via Python:
python3 tools/vps_preflight_readonly_check.py
```

---

## 5. Formal Gate Verdict

```
STATUS: READY_FOR_VPS_PREFLIGHT
OVERALL VERDICT: GO_FOR_VPS_PREFLIGHT
COHORT_ID: TORG_THREE_SYS_20261115_001
CANDIDATE_T0_UTC: 2026-11-17T00:00:00+00:00
TOTAL_TESTS_PASSING: 42/42 (100%)
PRODUCTION_PORT_8083_INTEGRITY: UNTOUCHED & PROTECTED
VPS_ACTION: AWAITING HUMAN AUTHORIZATION FOR READ-ONLY PREFLIGHT EXECUTION
```
