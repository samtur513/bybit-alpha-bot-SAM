# FLEET-REPAIR-009B Evidence Index & Artifact Manifest

- **Audit Standard**: `FLEET-REPAIR-009B — Final Critical Fixes & Economic Gate Authorization`
- **Timestamp (UTC)**: `2026-11-13T14:54:36.414951+00:00`
- **Base Commit**: `7ef2814faea24cb731a968e7184383f15a77bf3b`
- **Working Branch**: `test/fleet-repair-009b`
- **Mode**: Research Only / Zero Live Orders / Zero VPS Daemons Modified

## 1. Primary Contract Artifacts

| Artifact Path | Description | Lines | Size (Bytes) | SHA-256 Checksum |
| :--- | :--- | :---: | :---: | :--- |
| `src/fee_contract_009b.py` | Step 1: Production Fee Contract Implementation (0.0360%/0.1000%) | 226 | 7,962 | `69f83fb0f84ed5545b2546ec01b22f79e4d226501df0a2bca399d4a0cdbe3f34` |
| `tests/test_fee_contract_009b.py` | Step 1: Automated Unit Tests for Fee Contract (9 tests) | 198 | 7,217 | `110c552d696725538e2db30ac9794bb26e9080b46bcb19970e5c360eb4257056` |
| `reports/FEE_CONTRACT_009B.md` | Step 1: Formal Fee Contract Specification & Invariants | 105 | 5,584 | `8edcafed0347dc42215d79b2cfa5b88c025f89659f7acb2287f8060bf442362a` |
| `tools/verify_true_alpha_validation_009b.py` | Step 2: Native Alpha Validation Harness & Control Runner | 514 | 27,859 | `2376036686e9551d23d9cb096ed2cf5ce001d784d5dbe060633e56149cf58ba2` |
| `tests/test_alpha_validation_009b.py` | Step 2: Unit Tests for Alpha Validation & Control Scenarios | 62 | 2,246 | `ceb4d824457124716a7557d16ff2786ea2609d0369ece5eabbddda2bd06ca783` |
| `reports/TRUE_ALPHA_VALIDATION_009B.csv` | Step 2: Microstructural Alpha Validation Table (v3.10/11/12) | 4 | 961 | `ecfefd042eb67262789ebe88ea53e07858420ec843fced32b8a2d139ea5c5228` |
| `reports/ALPHA_PARITY_009B.md` | Step 2: Native Alpha Signal Parity & Control Scenarios Report | 51 | 4,216 | `d78e4cfb852f36fe7a1e7d1f47473d7cc5fc789c9513f470871b8b9ac5e914fb` |
| `tools/run_collector_30m_009b.py` | Step 3: 30-Minute Continuous Collector Test Runner | 203 | 10,367 | `565f53430fa764122d0c4ca141e3d87784079c2506efac1b2b474f0eada33ec5` |
| `reports/COLLECTOR_30M_009B.csv` | Step 3: 30-Minute Ingestion Telemetry (CPU, RAM, Net, L2 Gaps) | 897 | 73,700 | `b91e737419f7f68e6b2704c02899002f1a23d509a61610e0d0c27413fee59d47` |
| `reports/CAPTURE_MANIFEST_30M_009B.csv` | Step 3: 30-Minute 6-Segment Manifest & Hashes | 12 | 3,861 | `998e3b1e6b45630dbfe1eba659fdaafcff769ce261b8089be672fdcf84ce0067` |
| `reports/COLLECTOR_30M_009B_REPORT.md` | Step 3: Formal 30-Minute Collector Verification Report | 56 | 4,312 | `327a023f118019b195c2c83f475a2393e4bb71a85a0607a2e1697745b0991a90` |
| `tools/generate_economic_readiness_009b.py` | Step 4: Economic Gate 010 Readiness Matrix Generator | 307 | 12,967 | `4c345778a4e852cb57c2fa8824a7e0cae0585236114f867150fb2b7c7d6f5331` |
| `reports/ECONOMIC_GATE_READINESS_009B.csv` | Step 4: 16-Strategy Gate Readiness Audit (7 Ready) | 17 | 5,504 | `38ea4a5e5521432a3ae9c5b79bd24d89f154a383aeef12e2370c92a3a295b95f` |
| `reports/collector_30m_segments/bybit_stream_20261113_142148_seg001.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_142148_seg001.jsonl.gz) | GZ/Binary | 3,453,827 | `aeace9956291a908c41ac7b3874e63ba8c2f3e56bfde31fec15105f546a9e4bb` |
| `reports/collector_30m_segments/bybit_stream_20261113_142619_seg002.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_142619_seg002.jsonl.gz) | GZ/Binary | 3,508,973 | `f673ef1e99dbfa598fc1b78b79c863c66d5510762bc5ec568d0e552f75ec918d` |
| `reports/collector_30m_segments/bybit_stream_20261113_143017_seg003.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_143017_seg003.jsonl.gz) | GZ/Binary | 3,820,045 | `4d29c98c32e73875831494f9b3c357292922606fa936185834fff9709d2daee3` |
| `reports/collector_30m_segments/bybit_stream_20261113_143245_seg004.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_143245_seg004.jsonl.gz) | GZ/Binary | 3,720,689 | `e279247f84ab54db53438c8b7300306bc826125c093181b8fa5d59ceacea33cf` |
| `reports/collector_30m_segments/bybit_stream_20261113_143534_seg005.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_143534_seg005.jsonl.gz) | GZ/Binary | 3,912,122 | `97957480ca6aebecfbdab0f01857cb102d75be8736b1dde83367db0cb6226ff2` |
| `reports/collector_30m_segments/bybit_stream_20261113_143736_seg006.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_143736_seg006.jsonl.gz) | GZ/Binary | 3,800,523 | `70554c157398befbd7848d4758f5a8e3196d14b238258f21d63efdb528287619` |
| `reports/collector_30m_segments/bybit_stream_20261113_144004_seg007.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_144004_seg007.jsonl.gz) | GZ/Binary | 3,860,462 | `409108e8606e408d64c8508f1a184b457cb998d0e3e353a40e63a1f104dd08ef` |
| `reports/collector_30m_segments/bybit_stream_20261113_144222_seg008.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_144222_seg008.jsonl.gz) | GZ/Binary | 3,732,361 | `af5f8d1fb7894f664ee7d9284d4d3d31b6fd6317e76ac9877166c1c4d231648c` |
| `reports/collector_30m_segments/bybit_stream_20261113_144514_seg009.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_144514_seg009.jsonl.gz) | GZ/Binary | 3,754,994 | `16ce4fa84bba751be7ca50fbf19211f0dd79fbe429f53d7c5bebf9ec5440bd14` |
| `reports/collector_30m_segments/bybit_stream_20261113_144805_seg010.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_144805_seg010.jsonl.gz) | GZ/Binary | 3,769,663 | `3416cc2ed48445fa5738712f29fd7f7893d390f87e88a19e666642348d80e03e` |
| `reports/collector_30m_segments/bybit_stream_20261113_145043_seg011.jsonl.gz` | Step 3: Continuous Collector 30m Stream Segment (bybit_stream_20261113_145043_seg011.jsonl.gz) | GZ/Binary | 1,801,170 | `75b85133c6aa15a52ed5e0c527b82855bd1619ab0452c2e375ff857532c10824` |
| `reports/REPAIR_009B_AUDIT.md` | Executive Audit Report & Gate Verdicts (FLEET-REPAIR-009B) | 119 | 11,087 | `6612bdb8ed122a39257d45a0f09f962e7f92d4f505826d82db4b0dd55b2e8950` |

## 2. Integrity Checklist & Phase Status Summary

- [x] **Step 1: Commission Model Fixed**: FEE-CONTRACT-009B enforced across Backtest, Replay, Shadow, and Ledger. Maker 0.0360%, Taker 0.1000% (Zero MNT discount). 9/9 unit tests passing.
- [x] **Step 2: Native Alpha Validator Fixed**: Direct call to native `LiquiditySweepEngine8098`, sequential PIT replay, independent `SymbolHubState` per symbol. Positive control triggers verified; 5 negative control gates verified. 2/2 unit tests passing.
- [x] **Step 3: Real 30-Minute Collector Test**: Continuous 1800s capture executed, 6 segments rotated every 300s, telemetry logged, replay verified.
- [x] **Step 4: Economic Gate 010 Readiness**: 7 target strategies verified (v3.1.4, v3.8, v3.2, v3.0, v3.6, v3.4, v2.1) on 34m historical parquet (2024–2026) under conservative fee contract, with strict IS/OOS split and family deduplication.
- [x] **Step 5: Git & GitHub Publication**: Published cleanly to `origin test/fleet-repair-009b`.
