# AGENTS.md

Instructions, conventions, and configuration for AI agents working in this repository.

## Project Overview

- **Project**: Bybit Linear Perpetuals Multi-Alpha Quantitative Trading Engine (v3.0 Production Freeze) with Dynamic Universe Screener and News Core Defensive Shield.
- **Runtime**: Python 3.12, Bybit Linear Perpetuals WebSocket/REST, PyTest.
- **Architecture**:
  - **v3.0 (Production Freeze, Port 8082)**: Dynamic Universe Screener (400+ Bybit Linear Perpetuals filtered by Liquidity, Normalized ATR, Kaufman Efficiency Ratio, Relative Strength, and Funding Rate), 4h Donchian Breakout, triple trend filter (ADX >= 18.0, EMA50 > EMA200, BTC Delta), anti-martingale pyramiding with penny-perfect weighted average entry price, News Core v1.1 defensive shields (Regime Guard, Emergency Eject, Squeeze Escape, Runner Protection), and Half-Kelly risk management.
  - **v2.1 (Port 8081)**: 4h Donchian Breakout with static 18-coin universe and News Core defensive shield.
  - **v2.0 (Port 8080)**: 1h Breakout baseline engine.
- **Key Directories**:
  - `v3/src/`: Production engine (`core_v3.py`), dynamic screener (`universe_screener.py`), paper trader daemon (`paper_trader_v3.py`), indicators (`indicators.py`), risk manager (`kelly_sizing.py`), pyramiding engine (`pyramiding_engine.py`), Bybit market stream (`market_stream.py`), and configuration (`config.py`).
  - `v3/src/news_core/`: Self-contained News Core v1.1 module (models, classifier, market confirmation, position matrix, regime guard, replay engine).
  - `v3/tests/`: Comprehensive unit, accounting invariant, and news defense test suites (30 tests, 100% pass).
  - `v3/reports/`: Full quantitative backtest, walk-forward validation (WFV), Bybit Replay protocol, and Monte Carlo audit reports.
  - `docs/adr/`: Architecture Decision Records (`0001`, `0002`).
  - `docs/agents/`: Agent conventions and tracker configurations.
  - `.scratch/`: Local markdown task and feature specifications.

## Agent skills

### Issue tracker

Local markdown issues tracked in `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical 5-role triage vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context domain documentation (`GLOSSARY.md` + `docs/adr/`). See `docs/agents/domain.md`.

## Engineering Principles

1. **Trade Accounting Integrity**: Never compute pyramiding profit from the original entry price; always use mathematically rigorous weighted average entry price. Maintain penny-perfect conservation between closed trades net PnL and realized equity.
2. **Zero-Lookahead Point-in-Time (PIT)**: Never leak future prices or indicators. Donchian channels must use `shift(1)` on closed bars. Bar $t$ closes before bar $t+1$ executes.
3. **Deep Module Design**: Keep interfaces thin and implementations deep. Changes in execution or risk modules must not bleed into signal generation.
4. **Test-Driven Verification**: Every new feature or fix must be accompanied by unit tests and invariant checks. Never mark a task complete without 100% test pass.
5. **Windows Concurrency & Persistence**: File writes must be atomic with exponential backoff retries (`persistence.py`) to prevent Windows OS file lock collisions (`WinError 5`). Web API endpoints must serve live telemetry from memory cache.
6. **Linux Systemd & Service Daemon Standards**:
   - In systemd unit files, ALWAYS prefix optional environment files with a hyphen: `EnvironmentFile=-/path/to/.env` so systemd does not abort with `Result: resources` / `Failed to load environment files` when `.env` is absent.
   - ALWAYS embed fallback default environment variables (`PYTHONUNBUFFERED=1`, `PAPER_TRADING=true`, `USE_MNT_DISCOUNT=true`, `EQUITY_USDT=...`) directly inside the `[Service]` block.
   - For all HTTP, TCP, and ZeroMQ servers, ALWAYS enforce `SO_REUSEADDR` (`allow_reuse_address = True`) to eliminate `[Errno 98] Address already in use` upon rapid service restarts.
7. **Bybit V5 Exchange Protocol & Execution Invariants**:
   - **WebSocket Batching Limit**: A single Bybit V5 WebSocket `subscribe` frame must never contain more than 10 topics. Always chunk topic lists into batches of 10.
   - **Hedge Mode Stop-Loss Protection**: Never use order-level or position-level aggregate `stopLoss` fields in Hedge Mode. Always deploy independent conditional market stop orders with `reduceOnly=True`, `closeOnTrigger=False`, and distinct `orderLinkId` to protect sub-strategy isolation.
   - **Fee Integrity & Post-Only Execution**: Protect against taker fee bleed ($0.090\%$) by routing retest entries via Post-Only maker limits ($0.0324\%$). Unfilled orders must expire via timeout and log opportunity costs to the Missed Intent Ledger.
   - **Two-Sided Execution Symmetry**: Every quantitative module must implement full mathematical symmetry for both Long and Short directions (direction-aware limit fills, stop triggers, and MAE/MFE tracking).
8. **Python 3.12, Asyncio & ZeroMQ Lifecycle Invariants**:
   - **No uvloop with pyzmq**: Never mix `uvloop` with `pyzmq` on Python 3.12. Standard `asyncio` is battle-tested, handles thousands of msgs/sec, and prevents `RuntimeError: There is no current event loop in thread 'MainThread'`.
   - **Lazy ZeroMQ Socket Initialization**: Never instantiate `zmq.asyncio.Context()` or bind/connect sockets in synchronous class `__init__`. Always initialize ZeroMQ lazily inside an async method (e.g. `init_zmq()`) after `asyncio.run()` has established the running loop.
   - **Windows Selector Policy**: On Windows (`win32`), always invoke `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())` to support pyzmq selectors.
   - **Explicit PYTHONPATH in Systemd**: Always declare `Environment="PYTHONPATH=/opt/bybit-bot:/opt/bybit-bot/<module>"` in service definitions to guarantee clean cross-directory imports.
9. **Bybit V5 WebSocket Keepalive Invariant**:
   - Bybit V5 public linear and spot WebSockets do NOT send RFC 6455 pong control frames. Never use `ping_interval=20` with default python `websockets.connect()`, as it times out and crashes with code 1011 (`keepalive ping timeout`).
   - ALWAYS configure: `websockets.connect(url, ping_interval=None, ping_timeout=None, max_queue=4096, close_timeout=5)` and spawn a concurrent asyncio background task that sends application JSON `{"op": "ping"}` every 20 seconds.
10. **Anti-Chop Cooldown & Re-Entry Isolation Invariants**:
    - **Post-Exit Cooldown**: Never allow immediate re-entry on the same asset or pair. Always enforce an explicit cooldown (`cooldown_until = now + 300s` on normal exit, `600s` on Stop Loss / Time Stop) to eliminate machine-gun stop-loss churn.
    - **Relative Statistical Stops**: Stop-loss conditions on $Z$-score or spread dislocation must always be relative to entry ($Z \ge \max(SL\_Z, Z_{entry} + \Delta Z)$ for Short Spread; $Z \le \min(-SL\_Z, Z_{entry} - \Delta Z)$ for Long Spread), preventing instant 0.1s stop outs when entering on high-variance extremes.
    - **Test Isolation**: Wrap disk state loading and saving with `if "pytest" not in sys.modules:` to ensure unit tests remain 100% isolated and never pollute live state files.

## Fleet Systemd Service Directory (VPS `150.241.98.70`)

| Port | Strategy / Role | Systemd Service Unit Name | Working Directory |
| :--- | :--- | :--- | :--- |
| **8080** | v3.13 Sector Arbitrage | `bybit-bot.service` | `/opt/bybit-bot/v3_13_sector_arb` |
| **8082** | v3-v4 NewDragon Trend Lab | `bybit-v3-v4-exp.service` | `/opt/bybit-bot/v3_v4_exp` |
| **8083** | v3.2 Dual-Regime IRAS | `bybit-v32.service` | `/opt/bybit-bot/v3_2` |
| **8084** | v3.1.4 Champion 4h | `bybit-v314.service` | `/opt/bybit-bot/v3_1_4` |
| **8085** | Meta-Dashboard | `bybit-meta-dashboard.service` | `/opt/bybit-bot/meta_dashboard` |
| **8086** | v3.3 Turbo Streak Risk | `bybit-v33.service` | `/opt/bybit-bot/v3_3` |
| **8087** | v3.4 ZF-8 Quant | `bybit-v34.service` | `/opt/bybit-bot/v3_4` |
| **8088** | v3.5 Alpha Pairs | `bybit-v35.service` | `/opt/bybit-bot/v3_5` |
| **8089** | v3.6 Omega ZF-8 | `bybit-v36.service` | `/opt/bybit-bot/v3_6` |
| **8090** | v3.7 Master Sweeps | `bybit-v37.service` | `/opt/bybit-bot/v3_7` |
| **8091** | v3.8 NewDragon Trend Lab | `bybit-bot-v38.service` | `/opt/bybit-bot/v3_8` |
| **8092** | v3.9 NewDragon Scalp Lab | `bybit-bot-v39.service` | `/opt/bybit-bot/v3_9` |
| **8093** | v3.7-v4 NewDragon Scalp Lab | `bybit-v37-v4-exp.service` | `/opt/bybit-bot/v3_7_v4_exp` |
| **8094** | v3.14 Basket Dispersion | `bybit-dispersion-8094.service`| `/opt/bybit-bot/v3_14_basket_dispersion` |
| **8095** | Data & TCA Hub | `bybit-hub-8095.service` | `/opt/bybit-bot/v3_10_sweep` |
| **8096** | v3.12 Spot-Perp Cross-Basis | `bybit-crossbasis-8096.service`| `/opt/bybit-bot/v3_12_crossbasis` |
| **8097** | v3.11 Funding Squeeze | `bybit-funding-8097.service` | `/opt/bybit-bot/v3_11_funding` |
| **8098** | Master Sweeps L2 | `bybit-sweep-8098.service` | `/opt/bybit-bot/v3_10_sweep` |
