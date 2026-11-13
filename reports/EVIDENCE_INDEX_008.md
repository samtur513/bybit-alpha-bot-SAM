# FLEET-CAPTURE-RELEASE-008 Evidence Index & Integrity Manifest

- **Release Gate**: `FLEET-CAPTURE-RELEASE-008 — Data Collector Release Gate`
- **Timestamp (UTC)**: `2026-11-13T11:07:20.553137+00:00`
- **Operating Mode**: Research Only / Zero Live Orders / Zero VPS Daemons Touched
- **Repository**: `samtur513/bybit-alpha-bot-SAM`
- **Branch**: `test/f1a-regression-suite`

## 1. Primary Contract Artifacts

| Artifact Path | Category | Lines | Size (Bytes) | SHA-256 Checksum |
| :--- | :--- | :--- | :--- | :--- |
| `reports/TRUE_NATIVE_LIFECYCLE_DIFF.csv` | Step 1: Native Parity Lifecycle | 23 | 6,408 | `21d58ea89a9035a076fd542fffa39f2b9c43a1dff9ed87e2e411cd6b1ed77830` |
| `reports/PARITY_TEST_COVERAGE.md` | Step 1: Parity Coverage Analysis | 55 | 3,867 | `605faea53eddd0184ac8ce734f97cc1a42c6cea87302d0cb87c8bcbc478fbf25` |
| `reports/COLLECTOR_LOAD_TEST.csv` | Step 2: 75s Collector Telemetry | 38 | 2,927 | `389c84a43b551210f3802e320bfe3bd38952d6ab1edc464c74d41da9664d2a23` |
| `reports/COLLECTOR_INTEGRITY_REPORT.md` | Step 2: Collector Integrity Audit | 46 | 2,944 | `b0a3f7da5ee4bc5c4969aa7a9c5909545a894018d77d31a8cbe0f5efcb6f0284` |
| `reports/CAPTURE_DATA_MANIFEST.csv` | Step 2: Segment GZ Manifest | 4 | 1,146 | `db22a573c8fe7f50b6e12e7a66cedd0bcad0da064f43dd20f23710500d6bab99` |
| `reports/CAPTURE_REPLAY_VALIDATION.csv` | Step 3: Bit-Exact Replay Audit | 6 | 632 | `f47f02c6b880da0e9670df0b6186df9783095d132fba03d1fd3284f69fca1a52` |
| `reports/REAL_ALPHA_SIGNAL_EVENTS.csv` | Step 3: Real Alpha Signal Log | 4 | 612 | `63316c05877218a39b4bb16060b8036461b34c0b62eccbc860b370077d733846` |
| `reports/FLEET_DATA_COLLECTION_PLAN.md` | Step 4: VPS Production Plan | 153 | 6,783 | `1e8358c55ac0e182b0b1884cf53ae639da83b6ba5759e6418a8a3f7a8d0ab364` |
| `reports/CAPTURE_RELEASE_008_REPORT.md` | Step 5: Executive Gate Report | 148 | 11,120 | `4c8d6ef63c8695782684ec82b74eeb087ba6ee2a1811842ed1508c5588f2606c` |
| `reports/CAPTURE_RELEASE_008_READINESS.csv` | Step 5: Release Readiness Matrix | 10 | 2,645 | `a6824e982ca470eaec5bd1ada7089d1da6cb4350145a432f5b162d1a10ba873f` |
| `tools/bybit_public_market_collector.py` | Production Collector Daemon | 698 | 27,911 | `eab9b703b7dac805c86ca09b53737c8da14e14a6eff77f430644aeebcc17005d` |
| `tools/verify_true_native_parity_v2.py` | Independent Native Parity Test | 1088 | 57,306 | `cfa4e3675fbcfb2250faa82e62c407b0c56ef9ef188f34a5d65bfc88be7d300c` |
| `tools/validate_replay_and_alpha_signals.py` | Deterministic Replay & Alpha Test | 290 | 12,726 | `7e3657243412ab5ec158011b5d9939fca0bdd72580315abf6350a4d5a39fb0c1` |
| `tools/market_data_replayer.py` | Deterministic Stream Replayer | 218 | 8,997 | `0cc4e7876d57a7f49dbf157e24f863a745eb9cad5abc4d5c574ac298b15ed73b` |

## 2. Integrity Checklist & Gate Summary

- [x] **Native Parity**: Reconciled v3.1.4, v3.8, v3.2 stop discrepancies; dual independent calls across 22 decisions.
- [x] **Collector Load Test**: 74.12s continuous capture; 10,842 events; 3 rotated `.jsonl.gz` segments; 0 sequence gaps.
- [x] **Memory & Loop Health**: Peak RAM 94.0 MB RSS (zero leak); average CPU load 9.4% of 1 vCPU; 0 message drops.
- [x] **Deterministic Replay**: Bit-exact state SHA-256 hash match between Run 1 and Run 2; 0 negative spreads.
- [x] **Zero Mocking**: Real signals observed on v3.10 sweeps; v3.11 and v3.12 calmly report `NO_SIGNALS_OBSERVED`.
- [x] **VPS Readiness**: Zero port conflict; 10-symbol sizing (~160 MB RAM, 25% CPU, 2.27 GB/day); 3-day retention.
