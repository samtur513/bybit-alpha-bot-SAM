# PAPER_EXECUTION_CONTRACT.md: Universal Fleet Execution & Ledger Protocol

**Protocol Version**: FLEET-EXEC-2.0 (Verified Paper Specification)  
**Applicability**: Universal standard for all Bybit Perpetuals & Spot bots across Ports 8080–8098  
**Architecture Principle**: Minimal Invasive Adapter (Deep Module Design, Zero Strategy Alpha Mutation)  

---

## 1. Unified Event Pipeline

Every trading decision across the fleet must strictly follow the one-way causality sequence:

```
[ Market Event ] (Closed Bar or Orderbook L1 Tick)
       |
       v
[ Alpha Strategy ] (Preserved Original Indicators & Logic)
       |
       v
[ Order Intent ] (Immutable record of strategy intention & signal mid-price)
       |
       v
[ Execution Adapter ] (Realistic crossing prices, conservative fees, slippage)
       |
       v
[ Execution Fill ] (Confirmed fill event with execution ID and provenance)
       |
       v
[ Position Slice ] (Pro-rata entry fee, volume-weighted average entry price)
       |
       v
[ Fleet Ledger ] (Idempotent SQLite WAL database with ACID transactions)
       |
       v
[ Verified Telemetry ] (Penny-perfect MTM equity curve, multi-def win rate, PF)
```

---

## 2. Event Schemas & Data Contract

### 2.1 Order Intent (`order_intents` Table)
Represents the unadulterated output of the alpha strategy before market interaction:
- `intent_id` (TEXT, PK): UUID `INT_<uuid>`
- `run_id` (TEXT): Unique paper trading session identifier
- `module_id` (TEXT): e.g. `v3.1.4`, `v3.8`, `M1_CROSSBASIS_8096`
- `strategy_version` (TEXT): e.g. `3.1.4`, `3.8.0`
- `symbol` (TEXT): Bybit linear perpetual symbol (e.g. `BTCUSDT`)
- `side` (TEXT): `Buy` | `Sell`
- `order_type` (TEXT): `Market` | `LimitMaker` | `StopMarket`
- `requested_qty` (REAL): Target contract quantity (strictly > 0)
- `limit_price` (REAL, Nullable): Specified limit price (if LimitMaker)
- `signal_mid_price` (REAL): Orderbook mid-price at the exact moment of signal generation
- `signal_ts_ms` (INTEGER): UTC millisecond timestamp of candle close or signal trigger
- `data_received_ts_ms` (INTEGER): Timestamp when data was consumed by the bot
- `status` (TEXT): `SUBMITTED` | `ACCEPTED` | `REJECTED` | `TIMED_OUT`
- `rejection_reason` (TEXT, Nullable): Diagnostic rejection code

### 2.2 Execution Fill (`execution_fills` Table)
Represents the confirmed execution event with full fee and provenance tracking:
- `fill_id` (TEXT, PK): Unique fill identifier `FILL_<uuid>`
- `intent_id` (TEXT, FK): Pointer to originating `OrderIntent`
- `order_id` (TEXT): Exchange order link or simulated order ID
- `symbol` (TEXT): Trading instrument
- `side` (TEXT): `Buy` | `Sell`
- `filled_qty` (REAL): Executed quantity
- `fill_price` (REAL): Effective fill price (including crossing spread drag)
- `is_maker` (INTEGER): `1` if confirmed Maker rebate/fee, `0` if Taker crossing
- `fee_rate` (REAL): Explicit fee fraction (e.g. `0.00100` Taker, `0.00036` Maker)
- `fee_amount` (REAL): Cash fee in USDT (`filled_qty * fill_price * fee_rate`)
- `fill_ts_ms` (INTEGER): Exact execution timestamp
- `mode` (TEXT): Execution classification mode
- `provenance` (TEXT): e.g. `BYBIT_V5_ORDERBOOK_L1`, `HISTORICAL_REPLAY`

### 2.3 Position Slice (`position_slices` Table)
Enforces granular tracking of individual trade slices, enabling flawless Partial Take-Profit accounting:
- `slice_id` (TEXT, PK): `SLICE_<uuid>`
- `parent_pos_id` (TEXT): Parent position container
- `symbol` (TEXT): Trading pair
- `direction` (INTEGER): `1` (Long) or `-1` (Short)
- `entry_ts` (INTEGER): Entry execution millisecond timestamp
- `entry_price` (REAL): Fill price of the entry
- `entry_fee` (REAL): Pro-rata entry fee allocated to this slice
- `exit_ts` (INTEGER, Nullable): Exit execution timestamp
- `exit_price` (REAL, Nullable): Fill price of the exit
- `exit_fee` (REAL, Nullable): Exit fee paid
- `qty` (REAL): Slice contract volume
- `gross_pnl` (REAL): Realized gross profit `qty * (exit_price - entry_price) * direction`
- `net_pnl` (REAL): Realized net profit `gross_pnl - entry_fee - exit_fee`
- `exit_reason` (TEXT): e.g. `PARTIAL_TP_50`, `TRAILING_STOP`, `STOP_LOSS`, `TIME_STOP`
- `status` (TEXT): `OPEN` | `CLOSED`

---

## 3. Four Execution Classification Modes

Every performance record across the fleet must explicitly declare its execution provenance:

| Execution Mode | Description | Permitted Microstructure Assumptions | Target Usage |
| :--- | :--- | :--- | :--- |
| **`REAL_FILL`** | Authenticated Bybit API execution confirmed via exchange WebSocket. | None (Actual executed fill prices and account fees). | Live Verified Trading. |
| **`LIVE_PAPER_SIMULATED`** | Live forward execution on real-time Bybit WebSocket feeds. | Immediate Taker fill at best L1 Ask/Bid. Limit Maker fills strictly when price crosses through. | Verified Shadow Paper. |
| **`HISTORICAL_REPLAY`** | Point-in-time deterministic backtest on historical parquet data. | Taker execution at bar close + conservative crossing spread. Shift(1) indicator causality. | Strategy Validation. |
| **`SCENARIO_ONLY`** | Diagnostic sensitivity analysis (e.g. E1 Maker Upper Bound). | Stated hypothetical assumptions (e.g. 100% passive limit fill rate). | Theoretical Boundary. |

---

## 4. Multi-Leg Execution & Risk Invariants

For relative-value, pair, and basket strategies (`v3.13`, `v3.14`, `v3.12`):
1. **Asymmetric Order Submission**: Legs must be submitted with sequential atomic confirmation.
2. **Unhedged Beta Timeout Guard**: If Leg 1 fills as Maker or Taker, but Leg 2 fails to fill within `TIMEOUT_MS` (default 10,000 ms), the engine must **immediately liquidate Leg 1 at market** (`UNHEDGED_LEG_EJECTION`) to prevent naked directional exposure.
3. **Multi-Leg Fee Conservation**: The total fee for the trade equals the sum of fees across all constituent legs:
   $$\text{Total Fee}_{pair} = \text{Fee}_{leg1, in} + \text{Fee}_{leg1, out} + \text{Fee}_{leg2, in} + \text{Fee}_{leg2, out}$$

---

## 5. Funding Cashflow Accounting Rules

1. **8-Hour Interval Alignment**: Funding settlements occur strictly at 00:00, 08:00, and 16:00 UTC.
2. **Holding Duration Requirement**: Only positions held *across* the exact settlement timestamp receive/pay funding.
3. **Cashflow Equation**:
   $$\text{Cashflow}_{funding} = - \text{Direction} \times \text{Remaining Qty} \times \text{Mark Price} \times \text{Settlement Rate}$$
   - Long positions (`Direction = +1`) pay positive funding rates (negative cashflow).
   - Short positions (`Direction = -1`) collect positive funding rates (positive cashflow).
4. **Post-TP Scaling**: When a partial take-profit has closed 50% of the position, subsequent funding cashflows must scale strictly to the remaining 50% volume.
