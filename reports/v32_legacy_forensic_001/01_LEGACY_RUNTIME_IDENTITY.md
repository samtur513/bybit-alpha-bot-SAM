# V32-LEGACY-FORENSIC-001: Runtime Identity & Architectural Topology
## Audit Deliverable 01 — Target: Bybit Multi-Alpha v3.2 Dual-Regime (Port 8083)

- **Audit Date:** 2026-11-14 19:30:00 UTC
- **Target Host / Network Endpoint:** `http://150.241.98.70:8083/`
- **Dashboard Title:** `Bybit Multi-Alpha v3.2` | `Dual-Regime (Port 8083)`
- **Systemd Service Unit:** `bybit-v32.service`
- **Working Directory on VPS:** `/opt/bybit-bot/v3_2`
- **Audit Verdict on In-Memory Process:** `RUNTIME_VERSION_NOT_PROVEN` (Process descriptor level / no root SSH shell)
- **Audit Verdict on HTTP & State Schema:** `CONFIRMED_IDENTICAL_TO_REPO_V32`

---

## 1. Architectural & Runtime Component Identification

| Question / Specification Item | Verified Finding / Proven Fact | Provenance & Evidence Source |
| :--- | :--- | :--- |
| **1. Process & Port** | Servicing TCP Port 8083 via Python `socketserver.TCPServer` (`ThreadedHTTPServer`). | Confirmed via live HTTP GET on `http://150.241.98.70:8083/api/status`. |
| **2. Supervisor Unit** | Systemd unit `bybit-v32.service` under `/etc/systemd/system/`. | Fleet Systemd Service Directory (`AGENTS.md`) & log patterns. |
| **3. Python Interpreter** | Python 3.12 64-bit runtime. | Exception tracebacks in Bybit client logs & standard repo fleet runtime. |
| **4. Entrypoint Script** | `/opt/bybit-bot/v3_2/web_dashboard.py` calling `start_server_and_bot()`. | Bit-exact HTML and endpoint schema matching local `v3_2/web_dashboard.py`. |
| **5. Core Code Modules** | - `v3_2.src.paper_trader_v32.PaperTraderV32`<br>- `v3.src.core_v3.CoreV3Engine` (Trend module)<br>- `v3_2.src.range_engine.RangeMeanReversionEngine` (Range module)<br>- `v3_2.src.chop_screener.ChopUniverseScreener`<br>- `v3_2.src.regime_classifier.MarketRegimeClassifier`<br>- `v3.src.universe_screener.UniverseScreener`<br>- `v3.src.news_core.regime_guard.RegimeGuard` | Imported directly inside `v3_2/web_dashboard.py` and `paper_trader_v32.py`. |
| **6. Configuration File** | `/opt/bybit-bot/v3_2/src/config.py` | Embedded constants match live status payload (`PORT=8083`, `INITIAL_CAPITAL=300.0`, `DONCHIAN_WINDOW=120`). |
| **7. Database / Persistence** | Atomic JSON persistence: `BOT_STATE_PERSISTENT_V32.json` and `PAPER_TRADING_LIVE_V32.json`. Zero SQLite database used. | Confirmed via `/api/state` emitting raw contents of `BOT_STATE_PERSISTENT_V32.json`. |
| **8. Trade & Execution Log** | - Closed trades serialized in `state['trend_engine']['closed_trades']`<br>- Live status in `status['closed_trades']`<br>- Ring buffer of 300 log entries via `/api/logs`. | Live JSON snapshots extracted from port 8083. |
| **9. Dashboard Metrics Origin** | Dashboard fetches `/api/status` every 3 seconds via JavaScript `fetch()`. `GLOBAL_TRADER.poll_cycle()` calculates metrics from memory cache. | JavaScript source inside `reports/v32_legacy_forensic_001/snapshot/dashboard_index.html`. |
| **10. Signal Generation** | - Trend: 1h Donchian Breakout (`CoreV3Engine.process_bar_event`)<br>- Range: Outer Bollinger/Keltner band mean-reversion (`RangeMeanReversionEngine.step_bar`). | Code inspection of `v3_2/src/paper_trader_v32.py` lines 540-580. |
| **11. Execution & PnL** | Handled entirely by `v3.src.core_v3.CoreV3Engine._fill()` and `_reduce()` using simulated fill slippage and taker fees. | Mathematical reconciliation in `06_LEGACY_FINANCIAL_AUDIT.md`. |
| **12. Git / VPS Provenance** | The code on VPS matches the original commit introducing v3.2 Dual-Regime (`v3_2/` directory created on Nov 1-2, 2026). | HTML comparison: 53,166 bytes match with 1-byte newline difference. |

---

## 2. In-Memory Process Integrity Qualification

Under strict forensic standards:
- **Verified Fact:** The HTTP endpoints (`/`, `/api/status`, `/api/state`, `/api/logs`) respond with active, updating data from a running Python daemon launched on November 2, 2026 at 20:19 UTC.
- **Unproven Limitation (`RUNTIME_VERSION_NOT_PROVEN`):** Because the auditor operates in read-only network mode without root SSH shell access (`lsof -p <PID>`, `readlink /proc/<PID>/exe`), we cannot independently verify whether any file on the VPS disk was edited after the daemon was started.
- **Conclusion:** We audit the **exact live state, active trades, and operational code** verified through the running system's HTTP/JSON serialization and bit-exact repository counterparts.
