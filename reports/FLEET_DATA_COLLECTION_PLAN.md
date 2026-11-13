# Sovereign Quantum Fleet: Production Market Data Collection Plan
**Standard:** FLEET-CAPTURE-RELEASE-008 Step 4  
**Date:** 2026-11-13  
**Status:** READY FOR OPERATIONAL DEPLOYMENT (NON-INTRUSIVE / ZERO FLEET DOWNTIME)

---

## 1. Executive Summary & Architecture

The **Enterprise Bybit Public Market Collector** (`tools/bybit_public_market_collector.py`) captures high-resolution public market microstructure feeds without exchange API credentials or intrusive execution dependencies. It operates exclusively via outbound WebSocket connections, providing a continuous, validated dataset for offline strategy replay, alpha discovery, and true market-neutral paper validation.

### Architecture Highlights
- **Zero Port Conflict**: Pure outbound client connection (`wss://stream.bybit.com/v5/public/linear` and `.../spot`). Binds **zero TCP/UDP listening ports**, ensuring zero interference with existing fleet daemons on ports 8080–8098.
- **Strict Protocol Conformance**: Chunked subscription (max 10 topics per frame), RFC-compliant custom JSON keepalive pinging (`{"op":"ping"}` every 20s), and Windows/Linux selector event loop compatibility.
- **Non-Blocking Backpressure Queue**: Worker thread decoupling network ingestion from disk I/O via `asyncio.Queue(maxsize=10000)` with drop telemetry.
- **Segmented Atomic Rotation**: Rotates files into bounded chunks (e.g. 1-hour in production, 30s in test), automatically verifying raw SHA-256 and compressing into `.jsonl.gz` with an average **~8.0x compression ratio**.
- **Disk Safety Watermark**: Active watchdog that halts file writes if disk free space falls below 1.0 GB.

---

## 2. Resource Utilization & Empirical Sizing Models

Measurements from the empirical 75-second load test across **BTCUSDT, ETHUSDT, SOLUSDT** (10,842 records, 4.46 MB raw JSON, 0.56 MB compressed GZ):

| Metric | 3 Symbols (Measured Empirical Fact) | 10 Symbols (Recommended Core Fleet) | 30 Symbols (Full Universe Screener Max) |
| :--- | :--- | :--- | :--- |
| **Inbound Network Speed** | `38.4 KB/s` (~138 MB / hr) | `128.0 KB/s` (~460 MB / hr) | `384.0 KB/s` (~1.38 GB / hr) |
| **Raw Data Ingestion Rate** | `63.1 KB/s` (~227 MB / hr) | `210.0 KB/s` (~756 MB / hr) | `630.0 KB/s` (~2.27 GB / hr) |
| **Gzip Write Rate (Disk)** | `7.9 KB/s` (**28.4 MB / hr**) | `26.3 KB/s` (**94.7 MB / hr**) | `79.0 KB/s` (**284.4 MB / hr**) |
| **Daily Compressed Storage** | **`682 MB / day`** | **`2.27 GB / day`** | **`6.83 GB / day`** |
| **Process RAM RSS** | `93 – 94 MB` | `~160 MB` | `~320 MB` |
| **CPU Utilization (1 vCPU)** | `~9.4%` | `~22 – 28%` | `~55 – 65%` |
| **Memory Growth / Leaks** | `+1.0 MB` (bounded caches) | Bounded (< 200 MB stable) | Bounded (< 400 MB stable) |

### VPS Feasibility Analysis (VPS Specs: 1 vCPU, 2.0 GB RAM, 30 GB SSD)
1. **CPU Headroom**: At 10 symbols, the collector consumes ~25% of 1 vCPU. Existing daemons operate primarily on 1m/15m/1h/4h cycles with low idle usage (~15-20%), resulting in total system CPU load < 50%.
2. **RAM Headroom**: Fleet daemons consume ~1.2 GB. System buffers/cache use ~200 MB. Allocating 160 MB for the 10-symbol collector leaves >400 MB safe margin.
3. **Disk Capacity & SSD Wear**: With 30 GB SSD (typically ~8–12 GB free space):
   - **Recommended Fleet Configuration**: 10 Core Symbols (`BTCUSDT, ETHUSDT, SOLUSDT, SUIUSDT, DOGEUSDT, AVAXUSDT, NEARUSDT, LINKUSDT, APTUSDT, ADAUSDT`).
   - Generates ~2.27 GB/day of compressed market data.

---

## 3. Storage Retention & Rotation Policy

To prevent disk saturation on the VPS 30 GB SSD:

### Local Retention Schedule
- **Segment Duration**: 3,600 seconds (1 hour per segment, ~95 MB per file for 10 symbols).
- **Local Retention Window**: **3 days (72 hours)** = ~6.8 GB maximum disk footprint.
- **High Watermark Alarm**: If free disk space < 1.5 GB, an emergency purge deletes files older than 24 hours. If free disk space < 1.0 GB, writing halts.

### Automated Cloud Sync & Prune Pipeline (Optional)
If historical archiving is desired:
```bash
# Example rclone sync command (runs every 6 hours via systemd timer or cron)
rclone sync /opt/bybit-data/segments remote-s3:bybit-quantum-fleet-data/segments/ \
    --include "*.jsonl.gz" \
    --transfers 4 \
    --checkers 8 \
    --log-file /var/log/bybit-data-sync.log

# Prune local segments older than 3 days
find /opt/bybit-data/segments -type f -name "*.jsonl.gz" -mtime +3 -delete
```

---

## 4. Production Systemd Service Unit Specification

File: `/etc/systemd/system/bybit-market-collector.service`

```ini
[Unit]
Description=Bybit V5 Enterprise Public Market Data Collector
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bybit-bot
Environment="PYTHONPATH=/opt/bybit-bot"
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=-/opt/bybit-bot/.env

# Non-intrusive invocation: 10 symbols, 1-hour segments, 1.0 GB disk safety watermark
ExecStart=/usr/bin/python3 /opt/bybit-bot/tools/bybit_public_market_collector.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT,SUIUSDT,DOGEUSDT,AVAXUSDT,NEARUSDT,LINKUSDT,APTUSDT,ADAUSDT \
    --segment-duration 3600 \
    --output-dir /opt/bybit-data/segments \
    --min-free-disk-gb 1.0 \
    --queue-size 10000

Restart=always
RestartSec=10s
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal
SyslogIdentifier=bybit-collector

[Install]
WantedBy=multi-user.target
```

---

## 5. Operations Runbook: Deployment, Verification & Rollback

### Safe Staged Deployment Procedure (Zero VPS Fleet Disruption)
1. **Pre-flight Check**:
   ```bash
   df -h /opt
   free -m
   ```
2. **Directory Provisioning**:
   ```bash
   mkdir -p /opt/bybit-data/segments
   chmod 755 /opt/bybit-data/segments
   ```
3. **Install & Reload Systemd Unit**:
   ```bash
   cp bybit-market-collector.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable bybit-market-collector.service
   systemctl start bybit-market-collector.service
   ```
4. **Telemetry Verification**:
   ```bash
   # Check service status
   systemctl status bybit-market-collector.service --no-pager

   # Verify segment rotation and compression
   ls -lh /opt/bybit-data/segments/

   # Inspect live journal logs
   journalctl -u bybit-market-collector.service -n 50 -f
   ```

### Immediate Rollback Procedure
If resource pressure exceeds thresholds:
```bash
# 1. Stop and disable the service immediately
systemctl stop bybit-market-collector.service
systemctl disable bybit-market-collector.service

# 2. Reclaim disk space if needed
rm -rf /opt/bybit-data/segments/*.tmp
find /opt/bybit-data/segments -type f -name "*.jsonl.gz" -mtime +1 -delete

# 3. Verify fleet status
systemctl status bybit-bot.service bybit-v314.service bybit-bot-v38.service --no-pager
```
