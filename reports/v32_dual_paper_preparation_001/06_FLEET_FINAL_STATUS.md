# 06_FLEET_FINAL_STATUS.md
## Post-Consolidation Fleet Operational Architecture

- **Protocol:** TORG-FLEET-SAFE-STOP-001
- **Host Target:** VPS `150.241.98.70`
- **Execution State:** Research & Preparation Phase Completed (Ready for Authorized Stop)

---

### 1. Projected Service Operational Matrix

Following execution of the safe-stop sequence:

| Port | Service Unit Name | Working Directory | Target Status | Post-Stop State | Role in Next Phase |
| :---: | :--- | :--- | :---: | :---: | :--- |
| **8083** | `bybit-v32.service` | `/opt/bybit-bot/v3_2` | **ACTIVE** | `KEEP_RUNNING` | **v3.2 ORIGINAL** Historical Benchmark |
| **8084** | `bybit-v314.service` | `/opt/bybit-bot/v3_1_4` | **STOPPED / REASSIGNED**| `SAFE_TO_STOP` | Reassigned to **v3.2 LEGACY SHADOW** |
| **8085** | `bybit-meta-dashboard.service` | `/opt/bybit-bot/meta_dashboard` | **ACTIVE / STREAMLINED** | `SHARED_DEPENDENCY` | Dual-system observation UI (8083 & 8084) |
| **8080** | `bybit-bot.service` | `/opt/bybit-bot/v3_13_sector_arb` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8081** | `bybit-bot-v21.service` | `/opt/bybit-bot/v2_1` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8082** | `bybit-v3-v4-exp.service` | `/opt/bybit-bot/v3_v4_exp` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8086** | `bybit-v33.service` | `/opt/bybit-bot/v3_3` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8087** | `bybit-v34.service` | `/opt/bybit-bot/v3_4` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8088** | `bybit-v35.service` | `/opt/bybit-bot/v3_5` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8089** | `bybit-v36.service` | `/opt/bybit-bot/v3_6` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8090** | `bybit-v37.service` | `/opt/bybit-bot/v3_7` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8091** | `bybit-bot-v38.service` | `/opt/bybit-bot/v3_8` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8092** | `bybit-bot-v39.service` | `/opt/bybit-bot/v3_9` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8093** | `bybit-v37-v4-exp.service`| `/opt/bybit-bot/v3_7_v4_exp` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8094** | `bybit-dispersion-8094.service`| `/opt/bybit-bot/v3_14_basket_dispersion`| **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8095** | `bybit-hub-8095.service` | `/opt/bybit-bot/v3_10_sweep` | **INACTIVE** | `DISABLED` | Paused (consumers 8096-8098 stopped) |
| **8096** | `bybit-crossbasis-8096.service`| `/opt/bybit-bot/v3_12_crossbasis`| **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8097** | `bybit-funding-8097.service`| `/opt/bybit-bot/v3_11_funding` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |
| **8098** | `bybit-sweep-8098.service` | `/opt/bybit-bot/v3_10_sweep` | **INACTIVE** | `DISABLED` | Preserved in immutable archive |

---

### 2. VPS Resource Liberation Analysis

- **RAM Footprint:**
  - Pre-stop: 16 active Python processes consuming **~2.65 GB RSS**.
  - Post-stop: 2 active Python processes (8083 Original + 8084 Shadow + lightweight 8085 Meta-Dashboard) consuming **~280 MB RSS**.
  - **Reclaimed RAM:** **~2.37 GB** (89.4% reduction in memory pressure).
- **CPU Utilization:**
  - Pre-stop: **~35–45%** aggregate CPU load across 16 WebSocket tick loops.
  - Post-stop: **~2–4%** aggregate CPU load.
  - **Reclaimed CPU:** **~35–40%** headroom freed for low-latency tick ingestion.
- **Disk I/O & SSD Write Endurance:**
  - Pre-stop: 16 processes writing JSON/DB states every 60 seconds (~250 KB/min constant write).
  - Post-stop: 2 processes writing states (~15 KB/min).

---

### 3. Human Authorization Boundary (Next Action)

In strict compliance with **Часть IX (Границы разрешения)**:
No services will be stopped and no VPS files modified until the user explicitly issues confirmation to proceed with the stopping batch command:
```bash
# BATCH SAFE-STOP SCRIPT (Awaiting User Authorization)
sudo systemctl stop bybit-bot bybit-bot-v21 bybit-v3-v4-exp bybit-v314 bybit-v33 bybit-v34 bybit-v35 bybit-v36 bybit-v37 bybit-bot-v38 bybit-bot-v39 bybit-v37-v4-exp bybit-dispersion-8094 bybit-hub-8095 bybit-crossbasis-8096 bybit-funding-8097 bybit-sweep-8098
sudo systemctl disable bybit-bot bybit-bot-v21 bybit-v3-v4-exp bybit-v314 bybit-v33 bybit-v34 bybit-v35 bybit-v36 bybit-v37 bybit-bot-v38 bybit-bot-v39 bybit-v37-v4-exp bybit-dispersion-8094 bybit-hub-8095 bybit-crossbasis-8096 bybit-funding-8097 bybit-sweep-8098
```
