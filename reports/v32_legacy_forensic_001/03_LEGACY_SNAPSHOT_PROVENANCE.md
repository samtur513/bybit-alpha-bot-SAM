# Provenance & Forensic Chain-of-Custody Manifest
## Audit Deliverable 03 — V32-LEGACY-FORENSIC-001

- **Collection Timestamp:** 2026-11-14 19:24:20 UTC
- **Collection Mechanism:** Strict Read-Only HTTP GET via `urllib.request` with `User-Agent: Mozilla/5.0 (ForensicAuditor/1.0)`
- **Target Endpoint:** VPS `150.241.98.70:8083`
- **Zero Impact Guarantee:** Zero write requests (`POST`, `PUT`, `DELETE`), zero process restarts, zero database alterations.

---

## 1. Frozen Snapshot Artifacts & Cryptographic Signatures

| Snapshot Artifact | Target Source Endpoint | Payload Type | Size (Bytes) | SHA-256 Checksum |
| :--- | :--- | :--- | :---: | :--- |
| `reports/v32_legacy_forensic_001/snapshot/api_status.json` | `http://150.241.98.70:8083/api/status` | JSON Telemetry | 24,026 | `1576ad439652ddcdf5d3426376c49fb76a07469887f666df77c81882f2a406ab` |
| `reports/v32_legacy_forensic_001/snapshot/api_state.json` | `http://150.241.98.70:8083/api/state` | Persistent State JSON | 20,878 | `31c3afb8d8688f5ff197f714c7213d376b95552740c17b62a4e7070e8cfb1a07` |
| `reports/v32_legacy_forensic_001/snapshot/api_logs.json` | `http://150.241.98.70:8083/api/logs` | Log Ring Buffer JSON | 39,809 | `a419398d0f1518648e05d633f659c0764c31d4dc0b13116b8bd088948272439b` |
| `reports/v32_legacy_forensic_001/snapshot/dashboard_index.html`| `http://150.241.98.70:8083/` | Web Dashboard HTML | 55,227 | `46519e2733bf085f44e7e0dfae03d8fbaee257145e638b85a8d1f030e1fb9b73` |

---

## 2. Safe Reproduction Protocol for Independent Auditors

To reproduce this forensic verification independently:
1. Clone repository `samtur513/bybit-alpha-bot-SAM` and checkout branch `audit/v32-legacy-forensic-001`.
2. Execute automated test suite: `python -m pytest tests/test_v32_legacy_financial_accounting.py -v`.
3. Validate all cryptographic signatures against the manifest above.
4. Confirm zero private keys, API secrets, or passwords exist in any committed artifact.
