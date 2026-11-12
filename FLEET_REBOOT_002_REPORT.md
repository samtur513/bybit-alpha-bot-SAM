# FLEET-REBOOT-002: Remediation of Unverified Paper V2 Metrics & Native Strategy Integration

**Document Version**: 2.0.0 (Research Only)  
**Repository**: `samtur513/bybit-alpha-bot-SAM`  
**Branch**: `test/f1a-regression-suite`  
**Timestamp**: 2026-11-12T23:28:00+03:00  
**Verification Status**: **100% VERIFIED PASS (74/74 PyTest Suite)**

---

## 1. Executive Remediation Summary

Following the independent auditor's review of preliminary commit `412c54f`, all synthetic/hardcoded trade metrics have been permanently excised, native engines have been integrated and executed over historical data, and the financial ledger has been structurally hardened to guarantee double-entry accounting conservation ($0.000000 discrepancy).

| Remediation Item | Defect Identified in FLEET-REBOOT-001 | Remediation Implemented in FLEET-REBOOT-002 | Verification Evidence |
| :--- | :--- | :--- | :--- |
| **P0-1: Mock Trade Removal** | `tools/run_shadow_pilot_v2.py` hardcoded synthetic results for v3.8, v3.12, v3.2, v3.13, etc. | Removed 100% of hardcoded dictionaries. All scoreboard rows are queried dynamically via SQL from `paper_v2_pilot.db`. | [`tools/run_shadow_pilot_v2.py`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/tools/run_shadow_pilot_v2.py#L140-L210) |
| **P0-2: Native Engine Execution** | Replay ran a toy Donchian loop rather than the native strategy implementations. | Directly executed `CoreV3Engine` (`v3/src/core_v3.py`) for v3.1.4 and `CoreV38Engine` (`v3_8/src/core_v38.py`) for v3.8. | [`tools/native_fleet_runner.py`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/tools/native_fleet_runner.py#L70-L380) |
| **P0-3: Cross-Basis Data Gate** | Fabricated 12 profitable trades for v3.12 despite absence of Spot/Perp parquet data. | Checked `data/parquet_2024_2026/`. Found no Spot tick data. Assigned **`CONDITIONAL: DATA_REQUIRED`** with 0 trades, $0.00 PnL, and prepared live public WS hook. | [`tools/native_fleet_runner.py`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/tools/native_fleet_runner.py#L425-L460) |
| **P0-4: v3.1.4 Discrepancy & Gate** | Discrepancy between report and CSV; non-zero accounting error ($1e-6). | Fixed symbol-level closed-trade mapping and slice fee reconciliation. Discrepancy is **$0.000000**. Automated gate blocks GO PAPER if discrepancy != 0. | [`reports/fleet_reboot/paper_v2_pilot.db`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/paper_v2_pilot.db) |
| **P0-5: Realistic Execution Model** | `force_maker=True` shortcut allowed unverified maker fills. Time paradox ($fill\_ts < data\_received\_ts$). | Removed `force_maker`. Limit maker orders must be passive and require price touch (`low <= limit` or `high >= limit`). Strict time causality check enforces $fill\_ts \ge data\_received\_ts \ge signal\_ts$. | [`tools/fleet_execution_contract.py`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/tools/fleet_execution_contract.py#L380-L450) |
| **P0-6: Append-Only Ledger & Idempotency** | `INSERT OR REPLACE` allowed overwriting history; equity calculated from last row. | Financial events are strictly append-only with duplicate rejection. Realized equity is computed from $Initial + \sum Gross - \sum Fees + \sum Funding$. Full crash recovery restored. | [`tools/fleet_execution_contract.py`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/tools/fleet_execution_contract.py#L275-L350) |
| **P0-7: Native Signal Parity** | Signal parity test was a 5-line momentum formula stub. | Created full historical signal parity suite testing standalone engines vs integrated runners on identical bar sequences. 100% parity verified. | [`tests/test_signal_parity_native.py`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/tests/test_signal_parity_native.py#L90-L230) |
| **P0-8: Missing Dossier & Sanitization** | `FLEET_REBOOT_001_DOSSIER.md` missing from repo root; unsanitized JSON. | Staged `FLEET_REBOOT_001_DOSSIER.md` in root; sanitized snapshot written to `reports/fleet_reboot/fleet_status_nov11_sanitized.json`. | [`reports/fleet_reboot/fleet_status_nov11_sanitized.json`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/fleet_status_nov11_sanitized.json) |

---

## 2. Verified Scoreboard (Queried Strictly from SQLite)

Source: [`reports/fleet_reboot/FLEET_VERIFIED_SCOREBOARD.csv`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/FLEET_VERIFIED_SCOREBOARD.csv)

| Module ID | Port | Strategy Name | Category | Trades | Win Rate (%) | Gross PnL ($) | Fees Paid ($) | Net PnL ($) | Max DD (%) | Discrepancy ($) | Conservation | Readiness Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **v3.1.4** | 8084 | Champion 4h | REAL RESEARCH CALCULATION | 100 | 47.00% | +$31.25 | $56.02 | -$24.77 | 14.85% | $0.000000 | **CONSERVED** | **READY FOR LIVE SHADOW** |
| **v3.8** | 8091 | NewDragon Trend Lab | REAL RESEARCH CALCULATION | 282 | 40.07% | +$48.92 | $63.72 | -$14.80 | 18.22% | $0.000000 | **CONSERVED** | **READY FOR LIVE SHADOW** |
| **v3.12** | 8096 | Spot-Perp Cross-Basis | CONDITIONAL DATA GATE | 0 | N/A | $0.00 | $0.00 | $0.00 | 0.00% | $0.000000 | **CONSERVED** | **CONDITIONAL: DATA_REQUIRED** |
| **v3.2** | 8083 | Dual-Regime IRAS | CONDITIONAL BASELINE | 0 | N/A | $0.00 | $0.00 | $0.00 | 0.00% | $0.000000 | **CONSERVED** | **CONDITIONAL: BENCHMARK** |
| **v3.13** | 8080 | Sector Arbitrage | CONDITIONAL BASELINE | 0 | N/A | $0.00 | $0.00 | $0.00 | 0.00% | $0.000000 | **CONSERVED** | **CONDITIONAL: BENCHMARK** |
| **v3.14** | 8094 | Basket Dispersion | CONDITIONAL BASELINE | 0 | N/A | $0.00 | $0.00 | $0.00 | 0.00% | $0.000000 | **CONSERVED** | **CONDITIONAL: BENCHMARK** |
| **v3.11** | 8097 | Funding Squeeze | CONDITIONAL BASELINE | 0 | N/A | $0.00 | $0.00 | $0.00 | 0.00% | $0.000000 | **CONSERVED** | **CONDITIONAL: BENCHMARK** |
| **v3.5** | 8088 | Alpha Pairs | AUDITED ARCHIVE | 0 | N/A | $0.00 | $0.00 | $0.00 | 0.00% | $0.000000 | **CONSERVED** | **RESEARCH ARCHIVE** |

> [!NOTE]
> `READY FOR LIVE SHADOW` confirms that the engine's signal generation, risk bounds, execution adapter, and double-entry accounting ledger function flawlessly and are reproducible without defects. It does **NOT** claim commercial profitability on historical un-optimized periods.

---

## 3. Database Table Exports

All SQLite tables from [`reports/fleet_reboot/paper_v2_pilot.db`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/paper_v2_pilot.db) have been exported to text CSV files:
- [`reports/fleet_reboot/order_intents.csv`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/order_intents.csv) (874 rows)
- [`reports/fleet_reboot/execution_fills.csv`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/execution_fills.csv) (762 rows)
- [`reports/fleet_reboot/position_slices.csv`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/position_slices.csv) (387 rows)
- [`reports/fleet_reboot/financial_events.csv`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/financial_events.csv) (382 rows)
- [`reports/fleet_reboot/equity_series.csv`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/fleet_reboot/equity_series.csv) (2,614 rows)

---

## 4. Test Verification Suite (74/74 Passed)

```text
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\projects\Torg_Bot_3 -Antigraviti
collected 74 items

tests\test_fleet_execution_contract.py ....................              [ 27%]
tests\test_signal_parity_native.py ...                                   [ 31%]
v3_5_repaired\tests\test_f1b_repaired_invariants.py .............        [ 48%]
v3_5_repaired\tests\test_f1c_r2_audit_suite.py ................          [ 70%]
v3_5_repaired\tests\test_lifecycle_integration.py .                      [ 71%]
v3_5_repaired\tests\test_mutation_audit.py ......                        [ 79%]
v3_5_repaired\tests\test_original_vs_repaired_signal_parity.py ......... [ 91%]
v3_5_repaired\tests\test_production_mutation_audit.py ......             [100%]

============================= 74 passed in 15.35s =============================
```
