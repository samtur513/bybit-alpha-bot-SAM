# FLEET_DEPLOYMENT_MAP.md: Infrastructure, Network & Systemd Fleet Topology

**Audit Date**: November 12, 2026  
**Audit Protocol**: FLEET-REBOOT-001 (Step 1 Infrastructure Topology)  
**Host Target**: VPS Host `150.241.98.70` (Linux x86_64, systemd)  
**Base Path**: `/opt/bybit-bot/`  

---

## 1. Fleet Systemd Service Registry

| Port | Service Unit Name | Working Directory | Executable Entrypoint | Operational Role | Dependencies (`After=` / `Wants=`) | Endpoints Exposed |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- |
| **8080** | `bybit-bot.service` | `/opt/bybit-bot/v3_13_sector_arb` | `src/sector_arb_8080.py` | v3.13 Sector Arbitrage | `network.target` | `GET /api/status`, `GET /` |
| **8082** | `bybit-v3-v4-exp.service` | `/opt/bybit-bot/v3_v4_exp` | `src/paper_trader_v3_v4.py` | v3-v4 NewDragon Trend Lab | `network.target` | `GET /api/status`, `GET /` |
| **8083** | `bybit-v32.service` | `/opt/bybit-bot/v3_2` | `src/paper_trader_v32.py` | v3.2 Dual-Regime IRAS | `network.target` | `GET /api/status`, `GET /` |
| **8084** | `bybit-v314.service` | `/opt/bybit-bot/v3_1_4` | `src/paper_trader_v314.py` | v3.1.4 Champion 4h | `network.target` | `GET /api/status`, `GET /` |
| **8085** | `bybit-meta-dashboard.service` | `/opt/bybit-bot/meta_dashboard` | `meta_server.py` | Fleet Meta-Dashboard | `network.target` (Polls 8080–8098) | `GET /api/meta_status`, `GET /` |
| **8086** | `bybit-v33.service` | `/opt/bybit-bot/v3_3` | `src/paper_trader_v33.py` | v3.3 Turbo Streak Risk | `network.target` | `GET /api/status`, `GET /` |
| **8087** | `bybit-v34.service` | `/opt/bybit-bot/v3_4` | `src/paper_trader_v34.py` | v3.4 ZF-8 Quant | `network.target` | `GET /api/status`, `GET /` |
| **8088** | `bybit-v35.service` | `/opt/bybit-bot/v3_5` | `src/paper_trader_v35.py` | v3.5 Alpha Pairs | `network.target` | `GET /api/status`, `GET /` |
| **8089** | `bybit-v36.service` | `/opt/bybit-bot/v3_6` | `src/paper_trader_v36.py` | v3.6 Omega Dual-Regime ZF-8| `network.target` | `GET /api/status`, `GET /` |
| **8090** | `bybit-v37.service` | `/opt/bybit-bot/v3_7` | `src/scalp_engines.py` | v3.7 Master Sweeps | `network.target` | `GET /api/status`, `GET /` |
| **8091** | `bybit-bot-v38.service` | `/opt/bybit-bot/v3_8` | `src/paper_trader_v38.py` | v3.8 NewDragon Trend Lab | `network.target` | `GET /api/status`, `GET /` |
| **8092** | `bybit-bot-v39.service` | `/opt/bybit-bot/v3_9` | `src/microstructure_v39.py`| v3.9 NewDragon Scalp Lab | `network.target` | `GET /api/status`, `GET /` |
| **8093** | `bybit-v37-v4-exp.service`| `/opt/bybit-bot/v3_7_v4_exp` | `src/scalp_engines_v4.py` | v3.7-v4 NewDragon Scalp Lab| `network.target` | `GET /api/status`, `GET /` |
| **8094** | `bybit-dispersion-8094.service`| `/opt/bybit-bot/v3_14_basket_dispersion`| `src/dispersion_8094.py` | v3.14 Basket Dispersion | `network.target` | `GET /api/status`, `GET /` |
| **8095** | `bybit-hub-8095.service` | `/opt/bybit-bot/v3_10_sweep` | `hub_8095.py` | Data & TCA Hub | `network.target` | `tcp://127.0.0.1:5555` (ZMQ PUB), `GET /api/status` |
| **8096** | `bybit-crossbasis-8096.service`| `/opt/bybit-bot/v3_12_crossbasis`| `crossbasis_8096.py` | v3.12 Cross-Basis Arbitrage | `network.target`, `bybit-hub-8095` | `GET /api/status`, `GET /` |
| **8097** | `bybit-funding-8097.service`| `/opt/bybit-bot/v3_11_funding` | `funding_8097.py` | v3.11 Funding Squeeze | `network.target`, `bybit-hub-8095` | `GET /api/status`, `GET /` |
| **8098** | `bybit-sweep-8098.service` | `/opt/bybit-bot/v3_10_sweep` | `sweep_8098.py` | Master Sweeps L2 | `network.target`, `bybit-hub-8095` | `GET /api/status`, `GET /` |
| **8100** | *Spread Arena* | *None* | *None* | *Phantom / Not Deployed* | *None* | *None* |

---

## 2. Inter-Process Topology & Data Streams

```
                       [ Bybit V5 Exchange ]
                      /          |          \
           Public Linear WS  Public Spot WS  Private REST
                  |              |               |
                  v              v               |
         +--------------------------------+      |
         |   Data & TCA Hub (Port 8095)   |      |
         |   Tick Ingestion & ZeroMQ Pub  |      |
         +--------------------------------+      |
                         |                       |
            ZeroMQ IPC / TCP (Port 5555)         |
            ----------------------------         |
            |            |             |         |
            v            v             v         |
      [Port 8098]   [Port 8097]   [Port 8096]    |
       Sweeps L2      Funding     Cross-Basis    |
                                                 |
         +--------------------------------+      |
         |   Standard WebSocket Engines   |<-----+
         |   Ports 8080–8084, 8086–8094   |
         +--------------------------------+
                         |
           Local HTTP /api/status Poll
                         |
                         v
         +--------------------------------+
         |     Meta-Dashboard (8085)      |
         | Aggregate Telemetry & Radar UI |
         +--------------------------------+
```

---

## 3. Environment Variable & Configuration Invariants

1. **Systemd Resource Safety**:
   - Every systemd service file must declare `EnvironmentFile=-/path/to/.env` with the leading hyphen to prevent service aborts if `.env` is unprovisioned.
   - Fallback environment variables (`PYTHONUNBUFFERED=1`, `PAPER_TRADING=true`, `EQUITY_USDT=300.0`) are declared directly in `[Service]`.
2. **Fee Override Invariant**:
   - The embedded `USE_MNT_DISCOUNT=true` in unit files `8096`, `8097`, `8098` must be changed to `USE_MNT_DISCOUNT=false` or omitted so standard VIP0 rates (0.100% Taker / 0.036% Maker) apply during Verified Paper.
3. **Socket Port Rebinding**:
   - All HTTP servers must enforce `allow_reuse_address = True` (`SO_REUSEADDR`) to prevent `[Errno 98] Address already in use` upon rapid systemd restarts.
4. **ZeroMQ Context Initialization**:
   - ZeroMQ contexts in `hub_8095.py` and subscribers (`sweep_8098.py`, `crossbasis_8096.py`) must initialize lazily inside an async `start()` method after `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` on Windows or inside the running event loop on Linux.
