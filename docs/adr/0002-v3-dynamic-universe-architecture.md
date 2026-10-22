# ADR 0002: Multi-Alpha Engine v3 Architecture with Dynamic Universe Screener

- **Status**: Accepted (Production Freeze)
- **Date**: 2026-10-22
- **Deciders**: Quantitative Architecture Team, Pair Programming Agents
- **Verification**: `PROMPT_NEWS_BYBIT_REPLAY_TEST_v2` protocol passed (`edge есть`, SHA-256: `7da36f3504eb76a6f40b710aa806cc1867cfc5739ead388f9050c5f43ed93c04`).

## Context
Version 2.1 fixed trade accounting (weighted average entry, basket cash breakeven), transitioned to 4h Donchian trend following, and integrated the News Core defensive shield. However, the trading universe was still fixed to 18 static coins. A static universe suffers from hindsight bias, misses high-momentum emerging trends, and wastes capacity on coins entering long-term low-volatility chop.

## Decision
1. **Dynamic Universe Screener (`universe_screener.py`)**:
   - Programmatically scan the entire Bybit USDT Linear Perpetual universe (400+ coins).
   - Apply a 4-pillar gate:
     - Liquidity Gate: 24h Turnover $\ge \$25\text{M}$ USDT, Open Interest $\ge \$5\text{M}$ USDT.
     - Volatility Gate: Normalized ATR $\ge 2.0\%$.
     - Trend Quality Gate: ADX $\ge 16.0$ or Kaufman Efficiency Ratio $\ge 0.30$.
     - Relative Strength Gate: Momentum relative to BTC benchmark ($\text{Ret}_{\text{coin}} - \text{Ret}_{\text{BTC}}$).
     - Funding Rate Gate: $|\text{Funding}| \le 0.05\% / 8\text{h}$.
   - Rank by Composite Momentum Score into a **Top-25 Dynamic Watchlist**.
2. **Smooth Universe Rotation**:
   - Re-screen periodically.
   - If an active position drops out of the Top-25, it is **never** prematurely closed; it continues to be tracked until its normal Donchian trailing stop or news shield triggers an exit.
3. **Dedicated Deployment (Port 8082)**:
   - Run v3 as an independent service on Port `8082`, allowing simultaneous A/B/C performance comparison against v2.0 (8080) and v2.1 (8081).
4. **Resilient Persistence**:
   - Atomic JSON state updates with exponential backoff retries (`persistence.py`) to eliminate Windows OS file locking contention (`WinError 5`). In-memory cache for high-frequency REST reads.

## Consequences
- Eliminates survivorship and hindsight bias from static coin lists.
- Captures new breakout momentum across all 400+ perpetuals on Bybit.
- Preserves 100% of mathematical accounting invariants and News Core defensive shields.
- 30 out of 30 unit and invariant tests pass (100% pass).
