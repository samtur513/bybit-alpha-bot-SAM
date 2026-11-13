# Cryptographic Data & Artifact Hashes (ECONOMIC-GATE-010R)

- **Generated (UTC)**: `2026-11-13T18:15:00+00:00`
- **Repository**: `samtur513/bybit-alpha-bot-SAM`
- **Branch**: `research/economic-gate-010r`
- **Base SHA**: `f9125aade0e89a2c04d4cd19ddfcddc963b7cdb7`

---

## 1. Gate 010R Forensic & Verification Reports

| File | SHA-256 Checksum | Description |
| :--- | :--- | :--- |
| `reports/ECONOMIC_010_FORENSIC_DISCREPANCIES.csv` | `7bd2b1f4de7af899551f68ea48da34d44dc0a44754ca848d174f69b5ddc26036` | 184 programmatic discrepancies identified in Gate 010 |
| `reports/ECONOMIC_010_FORENSIC_REPORT.md` | `7e65526f336861bf9c9f6dc99a280c05367a5cc02c1478fe19d380f0ebe9aaca` | Forensic audit, root causes, retag of 7 surrogate strategies |
| `reports/NATIVE_DECISION_PARITY_010R.csv` | `85c99c509512ad140cba7ef5f921ad04bbf08e3a6ee5953c8c8c2725248881d9` | Native decision parity (10/10 TRUE_PARITY_PASS) |
| `reports/CAPITAL_LEDGER_INVARIANTS_010R.md` | `2b0bbc825e2fc884336a3effe745d703503320b36beaaf14896498abbcc35706` | Finite capital ledger specification & solvency invariants |
| `reports/NATIVE_ECONOMIC_010R_SCOREBOARD.csv` | `c393a954b7ae0cbead8289c7b8077bfd4cc88b188966dcb9a6cb43d50cc73eb6` | 45-run economic matrix (v3.1.4, v3.8, v3.2 across 5 periods) |
| `reports/NATIVE_ECONOMIC_010R_TRADES.csv` | `ddab3e4577b0f7c71768caea70d77d2e7ad2e413bfa7b50751227e40f3e50597` | 12,924 individual executed trade lifecycle records |
| `reports/NATIVE_ECONOMIC_010R_EQUITY.csv` | `44636833dcab9ba3ae042b38f024f9d4c0a2051c67b15a9b3932aaf957bc2791` | Continuous mark-to-market equity curves (10,008 rows) |
| `reports/NATIVE_ECONOMIC_010R_COSTS.csv` | `8acd8bc7de20868c9f98e7269d9d3672a8542328ed76d6211704e6c63cf2ec70` | Friction attribution (fees, slippage, funding cashflows) |

---

## 2. Core Source Code & Test Assets

| File | SHA-256 Checksum | Description |
| :--- | :--- | :--- |
| `src/capital_ledger_010r.py` | `0e930d142e7068a4d74b9d1c1eeee75163d1c236a708a465fd129d727f79613a` | Finite isolated capital ledger with margin & solvency floor |
| `src/native_economic_010r_runner.py` | `00c46d139b571f77ef8b12f0b22e664513e6cb047c3f9cdbc5df3e2f73a3a172` | Causal zero-lookahead runner for v3.1.4, v3.8, v3.2 |
| `tests/test_capital_ledger_010r.py` | `b6356410655843c6fb49d5a9532858f87267249839c6a2e98e34651f243c64d9` | 8/8 passing unit tests for capital invariants |
| `tools/verify_native_decision_parity_010r.py` | `ab8ad3fdd9891d8ae78cc7d519378ebdb5b813f9fe197eb8f2ab5ea999f97578` | Parity verification script for native engine classes |
| `tools/audit_gate_010_discrepancies.py` | `e336aa2d3c4437199decfde38dbeb7ead08fadc48dabd851cc1a4df283b595b3` | Programmatic auditor of Gate 010 defect patterns |
| `tools/run_native_economic_010r.py` | `aff71dadb615083d2660b2e3d1de5b584d798bd3ec1121b1fa843a2b231ac29f` | Automated orchestrator for 45-run matrix |
