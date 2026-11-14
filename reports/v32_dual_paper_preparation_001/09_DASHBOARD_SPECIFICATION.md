# 09_DASHBOARD_SPECIFICATION.md
## Telemetry & Web Dashboard Specification: Original vs Shadow v3.2

- **Target Architecture:** Dual-Engine Observation Interface
- **Original Dashboard:** Port 8083 (Historical UI preserved)
- **Shadow Dashboard:** Port 8084 (Enhanced Reality & Discrepancy UI)
- **Meta Aggregator:** Port 8085 (Comparative Side-by-Side View)

---

### 1. Port Allocation & UI Layout

```
+-----------------------------------------------------------------------------------+
|                        VPS Host 150.241.98.70 Telemetry                           |
+-----------------------------------------------------------------------------------+
|  [Port 8083: v3.2 ORIGINAL]            |  [Port 8084: v3.2 LEGACY SHADOW]         |
|  - Classic Clean Minimalist UI         |  - Advanced Forensic Telemetry           |
|  - Initial Capital: $300.00            |  - Initial Capital: $300.00              |
|  - Native Paper Equity Curve           |  - Dual Equity Curve (Native vs Shadow)  |
|  - Open Positions & PnL                |  - Realized Slippage & Fee Drag Metrics  |
|  - Donchian Channel Status             |  - Rejected Orders & Missed Intent Log   |
|  - Archive Tab: Historical 28 Trades   |  - Microsecond Event Ledger Feed         |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|  [Port 8085: Meta-Dashboard Aggregator]                                           |
|  - Side-by-Side Real-Time Alpha Spread Comparison                                 |
|  - Correlation & Execution Discrepancy Tracking: |Equity_Orig - Equity_Shadow|    |
+-----------------------------------------------------------------------------------+
```

---

### 2. Shadow Dashboard (Port 8084) Mandatory Display Metrics

In strict accordance with **Часть VI (Веб-панели)**:
1. **Header Banner:**
   - Strategy Name: `Bybit Multi-Alpha v3.2 Legacy Shadow`
   - Initial Capital: `300.000000 USDT`
   - Common Start Moment: `$T_0$ UTC`
   - Data Stream Status: `Bybit V5 Linear WebSocket (ONLINE)`
2. **Key Metric Tiles:**
   - **Native Paper Equity:** Theoretical equity calculated with original paper rules.
   - **Realistic Shadow Equity:** True net equity including 0.100% taker fees and book slippage.
   - **Alpha Discrepancy:** $\Delta = 	ext{Equity}_{	ext{Shadow}} - 	ext{Equity}_{	ext{Native}}$.
   - **Net Return:** Percentage change from 300.00 USDT.
   - **Win Rate & Profit Factor:** Live updated on closed trades.
3. **Execution Realism Audit Panel:**
   - **Cumulative Fee Drag:** Total taker fees paid ($0.100\%$ per turn).
   - **Cumulative Funding Carry:** Net funding paid/received on 8h Bybit intervals.
   - **Average Realized Slippage:** In basis points ($bps$).
   - **Rejected Executions:** Count of signals rejected due to spread $> 0.08\%$ or insufficient orderbook depth.
4. **Active Trades & Cascade Take Profit Monitor:**
   - Symbol, Direction, Entry Price, Size.
   - Current Price, Floating PnL.
   - Stop Loss & Current Trailing Level.
   - Cascade TP1 Status ($1.5R$ ATR, $30\%$ trim).
   - Cascade TP2 Status ($3.0R$ ATR, $40\%$ trim).
5. **Historical Archive Viewer:**
   - Dedicated interactive table rendering the pre-$T_0$ historical test of Original v3.2 (28 closed trades, $+17.99$ USDT).

---

### 3. API Telemetry Contracts

Both engines expose standard JSON endpoints:
- `GET /api/status`: Engine health, stream status, equity, open positions.
- `GET /api/trades`: Trade-by-trade closed history with fee and slippage breakdown.
- `GET /api/events`: Event log recording every tick event, partial exit, and stop move.
