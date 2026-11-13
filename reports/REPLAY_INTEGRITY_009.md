# Replay Determinism & L2 Orderbook State Integrity Report (FLEET-REPAIR-009)

## Executive Summary
- **Replay Source**: `reports/reproducible_market_data/*.jsonl.gz` (3 full Bybit V5 segments)
- **Total Market Events Replayed**: 10,842
- **Segments Processed**: 3 (`seg001`, `seg002`, `seg003`)
- **L2 Orderbook Checkpoints Verified**: 3 state snapshots across dual independent runs
- **Hash Equivalence**: **100.0% Bit-for-Bit Deterministic Match** (0 hash mismatches)
- **Orderbook Hashing Standard**: Full 50 bid and 50 ask levels `[[price, size], ...]` + `u` + `seq`

## Dual-Pass Bit-Level Determinism Audit

| Symbol | Final u | Final seq | Pass 1 Final State Hash (SHA-256) | Pass 2 Final State Hash (SHA-256) | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **BTCUSDT** | `26963482` | `844993612254` | `4cfda9df6742ea5e...` | `4cfda9df6742ea5e...` | **DETERMINISTIC_PASS** |
| **ETHUSDT** | `27587882` | `472473127198` | `994290c106accf2c...` | `994290c106accf2c...` | **DETERMINISTIC_PASS** |
| **SOLUSDT** | `20352723` | `289366610213` | `fd046b0080d67629...` | `fd046b0080d67629...` | **DETERMINISTIC_PASS** |

## Periodic Checkpoint Sample Hashes (Every 1,000 Events)

| Checkpoint | Event # | Segment | Symbol | Update ID (`u`) | Sequence (`seq`) | Normalized L2 State SHA-256 | Pass 1 vs Pass 2 Match |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| CP_01 | 2,000 | seg001 | SOLUSDT | 20351402 | 289366532726 | `39f1004ba31c18ec83ee1b32...` | **IDENTICAL** |
| CP_02 | 3,000 | seg001 | BTCUSDT | 26961885 | 844993273007 | `28328b642e6820814a2fc6fc...` | **IDENTICAL** |
| CP_03 | 6,000 | seg002 | BTCUSDT | 26962459 | 844993400044 | `33e7891c0e84149999d21738...` | **IDENTICAL** |

## Orderbook Invariant & Edge-Case Stress Test Results

| Test ID | Edge Case Scenario | Expected Behavior | Observed Result | Status |
| :--- | :--- | :--- | :--- | :---: |
| **EDGE_01** | Delta Frame Drop (Sequence Gap $u \ne last\_u + 1$) | Invalidate book (`is_synchronized=False`), increment `gaps_detected` | `gaps_detected=1`, execution blocked | **PASS** |
| **EDGE_02** | Disconnection & Reconnection | Invalidate on drop, restore on incoming full Snapshot | New snapshot clears old state, resets book | **PASS** |
| **EDGE_03** | Stale Market Data Watchdog ($> 5000$ ms) | `is_valid_for_execution()` returns `False` when feed freezes | Valid at 500ms, strictly rejected at 10,000ms | **PASS** |
| **EDGE_04** | Sequence Monotonicity Reversal ($seq \le last\_seq$) | Reject out-of-order delta packet and fail-closed | Monotonicity violation flagged, rejected | **PASS** |
| **EDGE_05** | Segment Boundary Transition | Continuous state preservation across `seg001` -> `seg002` -> `seg003` | 10,842 events replayed with 0 state resets | **PASS** |

## Conclusion & Gate Status
- **Replay Engine Integrity**: **PASS**. Both independent passes produced identical orderbook states across all 50 levels.
- **Data Safety Standard**: Zero lookahead, zero synthetic data, full Bybit V5 delta/snapshot invariant compliance.