# Sovereign Quantum Fleet: Paper V2 Live Shadow Readiness Specification
**Protocol:** FLEET-FASTTRACK-003  
**Status:** PRODUCTION READY (Research Only / Read-Only Live Shadow)  
**Security Level:** Read-Only Public Streams (Zero Live Order Execution, Zero VPS Modification)  
**Target Repository:** `samtur513/bybit-alpha-bot-SAM`  
**Branch:** `test/f1a-regression-suite`  

---

## 1. Executive Summary & Architecture Overview

Under **FLEET-FASTTRACK-003**, the Sovereign Quantum Fleet transitions from synthetic backtest approximations to a **100% causal, isolated, and provable Paper V2 architecture**.

All execution occurs strictly in read-only shadow paper mode driven by the **Bybit V5 Public WebSocket Adapter** (`tools/bybit_live_shadow_adapter.py`):
1. **Zero Real Orders & Zero VPS Impact**: The live shadow harness connects exclusively to public Bybit streams (`wss://stream.bybit.com/v5/public/linear` and `wss://stream.bybit.com/v5/public/spot`). No private API keys or trading privileges are accessed; live VPS daemons and systemd unit services remain untouched.
2. **Strict Time Causality & Next Open Execution**:
   $$\text{bar\_start\_ts} < \text{bar\_close\_ts} \le \text{signal\_ready\_ts} \le \text{data\_received\_ts} \le \text{order\_submit\_ts} \le \text{fill\_ts}$$
   No signal is evaluated before its bar is confirmed closed. Entries and stop-losses execute at $Open_{t+1}$ with explicit half-spread (2 bps) and slippage (2 bps).
3. **Conservative Ambiguity Resolution**:
   If a single bar's price range touches both the Stop Loss price and Take Profit target price, **Stop Loss executes first**, and the ambiguity count is incremented in telemetry.
4. **Complete Capital & Ledger Isolation**:
   Every strategy operates under a dedicated `run_id`, a private SQLite database with Write-Ahead Logging (`PRAGMA journal_mode=WAL`), and an independent $300.00 initial capital ledger.
5. **Double-Entry 3-Way Reconciliation**:
   Penny-perfect conservation ($0.000000 discrepancy) verified across three independent derivations:
   - **Method 1**: Closed position slices net PnL sum + funding cashflows - open entry fees.
   - **Method 2**: Financial events journal ($\sum \text{gross\_pnl} - \sum \text{fees} + \sum \text{funding}$) - open entry fees.
   - **Method 3**: Atomic step-by-step running equity.

---

## 2. Fleet Readiness Matrix: GO / CONDITIONAL / BLOCKED

The following table establishes the definitive operational status of every quantitative system across the fleet:

| Port | Strategy / System | Role | Systemd Unit | Public WS Streams Required | Blocking Condition / Gate | Live Shadow Readiness |
| :---: | :--- | :--- | :--- | :--- | :--- | :---: |
| **8084** | **v3.1.4 Champion 4h** | 4h Donchian 30 Trend Breakout | `bybit-v314.service` | `kline.240.*`, `tickers.*` | None. Pure native alpha & 1.0% Half-Kelly verified. | **GO** |
| **8091** | **v3.8 NewDragon Trend Lab** | 1h Donchian 14 + 4-Stage Exit | `bybit-bot-v38.service` | `kline.60.*`, `tickers.*` | None. Wilder ADX(14) restored; True 1R FSM verified. | **GO** |
| **8096** | **v3.12 Spot-Perp Cross-Basis** | Basis Arbitrage | `bybit-crossbasis-8096.service` | Linear `tickers.*` + Spot `orderbook.1.*` | Synchronous Spot tick parquet missing. | **CONDITIONAL: DATA_REQUIRED** |
| **8083** | **v3.2 Dual-Regime IRAS** | Adaptive Range & Breakout | `bybit-v32.service` | `kline.240.*`, `tickers.*` | Cross-regime transition matrix calibration. | **CONDITIONAL** |
| **8097** | **v3.11 Funding Squeeze** | Funding Rate Carry & Squeeze | `bybit-funding-8097.service` | Linear `tickers.*`, predicted funding | Predicted funding rate live schema verification. | **CONDITIONAL** |
| **8080** | **v3.13 Sector Arbitrage** | Cross-Sectional Stat Arb | `bybit-bot.service` | Linear `orderbook.1.*` (10 assets) | Multi-leg atomic fill synchronizer required. | **CONDITIONAL** |
| **8094** | **v3.14 Basket Dispersion** | Volatility & Dispersion Arb | `bybit-dispersion-8094.service` | Linear `tickers.*` (Top 18) | Real-time PCA covariance pipeline integration. | **CONDITIONAL** |
| **8086** | **v3.3 Turbo Streak Risk** | Donchian with Streak Scaling | `bybit-v33.service` | `kline.60.*`, `tickers.*` | Isolated run_id ledger migration required. | **CONDITIONAL** |
| **8087** | **v3.4 ZF-8 Quant** | Statistical Reversal & Volume | `bybit-v34.service` | Linear `orderbook.1.*`, `tickers.*` | L1 order flow imbalance (OFI) hook required. | **CONDITIONAL** |
| **8088** | **v3.5 Alpha Pairs** | Statistical Long/Short Pairs | `bybit-v35.service` | `kline.60.*`, `tickers.*` | F1C audit passed; F2 IS research in progress. | **CONDITIONAL** |
| **8089** | **v3.6 Omega ZF-8** | Statistical Spread Arbitrage | `bybit-v36.service` | Linear `tickers.*` | High-frequency spread z-score calibration. | **CONDITIONAL** |
| **8090** | **v3.7 Master Sweeps** | Liquidity Sweep Breakout | `bybit-v37.service` | Linear `tickers.*`, `kline.60.*` | Sweep detection latency calibration. | **CONDITIONAL** |
| **8082** | **v3-v4 NewDragon Lab** | Dual Engine Experimental | `bybit-v3-v4-exp.service` | `kline.60.*`, `kline.240.*` | Experimental model checkpoint sync. | **CONDITIONAL** |
| **8092** | **v3.9 NewDragon Scalp Lab** | 15m/1m Breakout Scalper | `bybit-bot-v39.service` | `kline.15.*`, `kline.1.*`, L2 book | Sub-minute tick-level queue simulation absent. | **BLOCKED** |
| **8093** | **v3.7-v4 Scalp Lab Exp** | Sub-Second Scalping | `bybit-v37-v4-exp.service` | L2 Depth 50, Trades stream | High-frequency execution queue model absent. | **BLOCKED** |
| **8098** | **Master Sweeps L2** | Microstructure Sweep Arb | `bybit-sweep-8098.service` | L2 Depth 50 stream | High-bandwidth L2 book processor absent. | **BLOCKED** |
| **8095** | **Data & TCA Hub** | Data Ingestion & Attribution | `bybit-hub-8095.service` | Linear & Spot public WS | Read-only telemetry infrastructure. | **GO (INFRASTRUCTURE)** |
| **8085** | **Meta-Dashboard** | Fleet Telemetry Hub | `bybit-meta-dashboard.service` | Internal memory state cache | Read-only UI aggregator. | **GO (INFRASTRUCTURE)** |

---

## 3. Tier 1 Core Strategies: Detailed Specification

### A. v3.1.4 Champion 4h (`Port 8084`) — Status: GO
- **Alpha Profile**: 4-Hour Donchian 30 Breakout with triple trend confirmation.
- **Indicators**:
  - Donchian Channel: Upper $= \max(High_{t-30:t-1})$, Lower $= \min(Low_{t-30:t-1})$ (`shift(1)` for zero-lookahead).
  - Trend Filter: EMA50 > EMA200 (Longs) / EMA50 < EMA200 (Shorts).
  - Volatility Regime: Wilder ADX(14) $\ge 18.0$.
  - BTC Delta Filter: Requires BTC EMA50 > EMA200 for altcoin Longs.
  - Volume Filter: $Volume / VolumeSMA_{20} \ge 1.0$.
- **Risk & Sizing**:
  - Initial Capital: $300.00.
  - Sizing Model: Half-Kelly 1.0% (`BASE_RISK_PCT = Decimal("0.010")`).
  - Max Leverage Cap: 3.5x.
  - Max Concurrent Positions: 4 (Max 2 Longs, Max 2 Shorts).
- **Execution & Exits**:
  - Initial Stop Loss: $2.5 \times ATR_{14}$.
  - Trailing Stop: $2.5 \times ATR_{14}$ ratchet (moves only in profit direction).
  - Cascade Take Profit: Disabled (`ENABLE_CASCADE_TP = False`).
  - Fees: Taker VIP0 $0.100\%$, half-spread $0.02\%$, slippage $0.02\%$.

### B. v3.8 NewDragon Trend Lab (`Port 8091`) — Status: GO
- **Alpha Profile**: 1-Hour Donchian 14 Breakout with NewDragon v4 Universal Adaptive Exit FSM.
- **Indicators**:
  - Donchian Channel: Upper $= \max(High_{t-14:t-1})$, Lower $= \min(Low_{t-14:t-1})$.
  - Trend Filter: EMA50 > EMA200 (Longs) / EMA50 < EMA200 (Shorts).
  - Volatility Regime: Real Wilder ADX(14) $\ge 16.0$ (Mock `adx = 25.0` completely excised).
  - BTC Delta Filter: Bullish BTC confirmation for altcoins.
- **Risk & Sizing**:
  - Initial Capital: $300.00.
  - Sizing Profile: True 1R Sizer (`RISK_PER_TRADE_PCT = Decimal("0.0080")`, 0.8% risk).
  - Max Leverage Cap: 3.5x.
  - Max Concurrent Positions: 3.
  - Max Portfolio Heat: 3.5%.
- **Adaptive Exit State Machine (FSM)**:
  - **Stage 0 (Fast Scratch)**: Adverse OFI + inside bars held $\ge 3$ bars $\to$ Scratch exit.
  - **Stage 1 (Ratchet)**: At $+0.85\text{R}$, ratchet stop to $-0.35\text{R}$.
  - **Stage 2 (Maker Partial TP)**: At $+1.40\text{R}$, submit Maker Limit order for 60% position (Maker fee $0.036\%$), move remaining stop to $+0.35\text{R}$ guaranteed profit.
  - **Stage 3 (Chandelier Trail)**: At $+2.00\text{R}$, trail stop at $Highest20 - 2.5 \times ATR_{14}$ (or compressed $1.2 \times ATR_{14}$ on volatility shocks).
  - **Ambiguity Rule**: If bar touches both Stop Loss and Stage 2 TP, **Stop Loss executes first**.

---

## 4. Tier 2 Synchronous Data Gate: v3.12 Spot-Perp Cross-Basis

- **Port**: 8096
- **Status**: **CONDITIONAL: DATA_REQUIRED**
- **Evaluation Rule**:
  - Bybit Linear Perpetuals parquet data is present in `data/parquet_2024_2026/`.
  - Synchronous Bybit Spot orderbook / tick historical parquet data is **ABSENT**.
  - **Audit Standard Enforced**: Zero synthetic trades fabricated. Trades Count $= 0$, Gross PnL $= \$0.00$, Net PnL $= \$0.00$.
  - **Stream Hook**: Pre-configured in `tools/bybit_live_shadow_adapter.py` subscribing to `wss://stream.bybit.com/v5/public/spot` (`orderbook.1.*`).

---

## 5. Live Shadow WebSocket Protocol Specification

The read-only live shadow runner (`tools/bybit_live_shadow_adapter.py`) enforces strict exchange protocol invariants:

1. **Keepalive Ping Invariant**:
   Bybit V5 public WebSockets do not issue RFC 6455 pong frames upon ping. Standard `websockets.connect(ping_interval=20)` times out with code 1011.
   **Solution**: Configured with `ping_interval=None, ping_timeout=None, max_queue=4096`, and spawns an asyncio background worker sending application-level JSON:
   ```json
   {"op": "ping"}
   ```
   every 20 seconds.
2. **Batching Limit Invariant**:
   Bybit V5 drops connections if more than 10 topics are included in a single subscription payload.
   **Solution**: All topic lists are chunked into batches of exactly 10 topics with 50ms pacing.
3. **Confirmed Bar Invariant**:
   For kline topics (`kline.60.*`, `kline.240.*`), incoming tick updates are discarded until Bybit emits `c["confirm"] == True`. Only confirmed closed bars trigger strategy signal evaluation.
4. **Idempotent Local Journaling**:
   Every incoming fill and funding settlement writes atomically to SQLite in WAL mode with immediate disk sync.

---

## 6. Execution Command Reference

### A. Run Reproducible Economic Gate (Period 1 & Period 2)
```bash
python tools/run_fasttrack_003.py
```

### B. Run Comprehensive Invariant & Parity Tests
```bash
python -m pytest tests/test_fasttrack_003_invariants.py tests/test_signal_parity_native.py tests/test_fleet_execution_contract.py
```

### C. Launch Read-Only Live Shadow Adapter
```bash
python -c "
import asyncio
from tools.bybit_live_shadow_adapter import BybitLiveShadowAdapter

adapter = BybitLiveShadowAdapter()
try:
    asyncio.run(adapter.start())
except KeyboardInterrupt:
    adapter.stop()
"
```
