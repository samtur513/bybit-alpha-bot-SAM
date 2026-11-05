# v3.10 Pod: Data & TCA Hub (:8095) & Liquidity Sweep Reversion (:8098)

## Overview
- **Port 8095 (`hub_8095.py`)**: Data & TCA Hub. Ingests Bybit Linear L2 Orderbook + Public Trades, computes 15m structural metrics with Anti-Compression indicators, and broadcasts via ZeroMQ PUB (`tcp://127.0.0.1:5555`).
- **Port 8098 (`sweep_8098.py`)**: Liquidity Sweep Reversion Engine. Listens to ZeroMQ PUB, detects false breakouts of 15m structural extremes, checks Anti-Compression, places Post-Only limit orders on Reclaim, and executes independent conditional Stop-Loss orders (`reduceOnly=True`) to safeguard Bybit Hedge Mode.
- **`tca_ledger.py`**: SQLite WAL Shadow Sub-Ledger decomposing every closed trade into 4 parts:
  $$\text{Net PnL} = \text{Pure Alpha} + \text{Execution Spread Capture} + \text{Funding Cashflow} + \text{Fees Paid}$$
  Also tracks unfulfilled limit order Opportunity Cost over a 60-second window to calculate true **Capture Efficiency**.

## Mathematics & Noise Gates
1. **Anti-Compression Gate**:
   - Rejects sweep entries if touch count $> 3$ (level weakened, breakout probability rises).
   - Rejects sweep entries if compression ratio $< 1.8 \times \text{ATR}_{15m}$ (price hugging the level tightly indicates pre-breakout accumulation).
2. **Absorption Gate**:
   - Aggressive trade volume at the level must exceed historical expectation by $\ge 2.5\times$.
3. **CVD Flip Gate**:
   - LONG: $TFI_{3s} \ge +0.30$ and $OBI_{10} \ge +0.15$.
   - SHORT: $TFI_{3s} \le -0.30$ and $OBI_{10} \le -0.15$.
4. **Execution Protocol**:
   - Reclaim Limit entry (Post-Only) with 15s timeout.
   - Independent conditional Market Stop-Loss (`triggerDirection=2` for Long, `1` for Short, `reduceOnly=True`).

## Endpoints
- **Hub 8095**:
  - UI: `http://localhost:8095/`
  - JSON: `http://localhost:8095/status`
- **Sweep 8098**:
  - UI: `http://localhost:8098/`
  - JSON: `http://localhost:8098/status`
