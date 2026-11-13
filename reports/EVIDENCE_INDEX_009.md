# FLEET-REPAIR-009 Evidence Index & Artifact Manifest

- **Audit Standard**: `FLEET-REPAIR-009 — Critical Integrity & True Signal Gate`
- **Timestamp (UTC)**: `2026-11-13T12:37:51.993592+00:00`
- **Base Commit**: `58b2653ef4867a53db8533de27bfa9c479816317`
- **Working Branch**: `test/fleet-repair-009`
- **Mode**: Research Only / Zero Live Orders / Zero VPS Daemons Modified

## 1. Primary Contract Artifacts

| Artifact Path | Description | Lines | Size (Bytes) | SHA-256 Checksum |
| :--- | :--- | :---: | :---: | :--- |
| `reports/TRUE_NATIVE_LIFECYCLE_DIFF_009.csv` | Step 1: Reconciled Lifecycle Parity (22 decisions) | 23 | 6,574 | `ff5097224fbe3543867ddd4c78838d3035f29be15586d04d9d1db107bd06941d` |
| `reports/PARITY_TEST_COVERAGE_009.md` | Step 1: Lifecycle Coverage & Discrepancy Reconciliation | 29 | 3,554 | `958027c3a370906486773e87e16769dad2ab1b172f007857119ee56224706bc0` |
| `reports/TRUE_NATIVE_ALPHA_SIGNALS_009.csv` | Step 2: True Native Alpha Signals (v3.10, v3.11, v3.12) | 4 | 2,048 | `9c80b945c27fc617bdb14b0cdbf7f02f30aeea9a99734281623361bf074e5822` |
| `reports/REPLAY_INTEGRITY_009.md` | Step 3: Deterministic Replay & Full L2 Orderbook Hashing | 39 | 3,091 | `25296a42314fb8847a31ba7dd1122d7ac67bf3c33f9b4bf69f2ad0d8ed94eba9` |
| `reports/FLEET_DATA_COLLECTION_PLAN.md` | Step 4: Hardened VPS Data Collection Architecture | 156 | 7,148 | `eda9e732745f516cd56f508a59b89f7917a59c9d8713c924110ea68357c6bc54` |
| `reports/ECONOMIC_GATE_READINESS_009.csv` | Step 5: 16-Strategy Net Expectancy Readiness Audit | 17 | 5,397 | `0ee6b621efb11123fde4b6bf9f531d1f13bfa6ab974a26b7757d67487f805586` |
| `reports/reproducible_market_data/bybit_stream_20261113_105943_seg001.jsonl.gz` | Step 3: Reproducible Market Segment 1 (4,465 events) | GZ/Binary | 245,083 | `c87215785fe8748da81b9b82816ff67eb480693b3fbf2cc391abc13feeae6b9e` |
| `reports/reproducible_market_data/bybit_stream_20261113_110013_seg002.jsonl.gz` | Step 3: Reproducible Market Segment 2 (4,797 events) | GZ/Binary | 264,182 | `40d508ef5a81120fae056926214a6a3b9c1a524ef4f0bdd2767c23d5fb8eb0ee` |
| `reports/reproducible_market_data/bybit_stream_20261113_110044_seg003.jsonl.gz` | Step 3: Reproducible Market Segment 3 (1,580 events) | GZ/Binary | 76,992 | `ce981d7685530ccbd9008db2770154c258ab3fe1a453ad15f12e04d2907788e4` |
| `tools/verify_true_native_parity_v3.py` | Step 1 Verification Harness: Native vs Adapter Parity v3 | 1083 | 56,897 | `f1eacf8fa90126dff6de8bad0b0de8ffd53ae8118004e9cae28a1610f9d59d33` |
| `tools/verify_native_alpha_signals_009.py` | Step 2 Verification Harness: Native Alpha Signals | 290 | 14,579 | `f12448636d6b06e8d07081a1fa53c73e7179a9ac4241896ac322584ff721e894` |
| `tools/replay_comprehensive_009.py` | Step 3 Verification Harness: Dual Replay & L2 Hashing | 330 | 14,414 | `315a8668076bf67f9e7bbd1aff1f83b23f999fcbda17797b5cc812588a880f44` |
| `tools/generate_economic_readiness_009.py` | Step 5 Generator: Economic Gate Readiness Matrix | 251 | 11,924 | `27f7e57d11f80ef4611feb9ad6c69c9e0a30d1ac4e7047c720cc95bb25d526c4` |
| `tools/fleet_unified_shadow_paper.py` | Production Unified Shadow Paper Adapters & FSM Engine | 1122 | 52,171 | `2ae2fd23998a60f51f303495c3f2ee37f87f5d7f6c7f1fb950c56973bceb7bc3` |
| `reports/REPAIR_009_AUDIT.md` | Executive Audit Report & Gate Verdicts (FLEET-REPAIR-009) | 164 | 16,845 | `1552695c614ef2f6fbecbc642dfbed589cdf1c8e4af499a5da2bfaec2304a314` |

## 2. Integrity Checklist & Phase Status Summary

- [x] **Step 1: Native Lifecycle Parity Reconciled**: 21 TRUE_PARITY_PASS, 1 PARITY_NOT_PROVEN, 0 DISCREPANCY. All 8 previous discrepancies resolved.
- [x] **Step 2: True Native Alpha Signals**: v3.10 Master Sweeps evaluates full swing/reclaim mechanics (0 signals); v3.11 evaluated with 3 bps threshold (0 signals); v3.12 evaluated with true L2 orderbook basis (0 signals). Zero false volume bursts.
- [x] **Step 3: Reproducible Data & Replay**: Raw segments versioned in repo at `reports/reproducible_market_data/*.jsonl.gz`. Dual-run replay verified 100% bit-for-bit hash equivalence across all 50 orderbook levels. 4 edge-case stress tests passed.
- [x] **Step 4: Hardened VPS Architecture**: Non-destructive `rclone copy --no-traverse`, unprivileged user `bybit-data`, zero credentials/.env, clear facts vs projections.
- [x] **Step 5: Fleet Economic Gate**: 16 systems evaluated under VIP0 fees (7 READY, 5 BLOCKED_NO_DATA, 2 BLOCKED_LOGIC_DEFECT, 2 BLOCKED_HIGH_FEES).