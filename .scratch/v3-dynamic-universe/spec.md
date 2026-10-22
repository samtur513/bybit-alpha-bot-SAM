# Specification: Multi-Alpha Growth Engine v3 with Dynamic Universe Selection & News Defensive Shield

- **Status**: completed (production frozen)
- **Author**: Pair Programming Agents
- **Date**: 2026-10-22
- **Target Release**: `v3.0`
- **Audit Verification**: Passed `PROMPT_NEWS_BYBIT_REPLAY_TEST_v2` protocol (Verdict: `edge есть`, SHA-256: `7da36f3504eb76a6f40b710aa806cc1867cfc5739ead388f9050c5f43ed93c04`).

## 1. Problem Statement & Motivation

In versions v2.0 and v2.1, the trading universe was restricted to a static hardcoded list of 18 coins selected in hindsight. This created two quantitative inefficiencies:
1. **Missed Opportunities**: When emerging altcoins ignite sustained parabolic trends, the bot cannot capture them.
2. **Dead Capital Drag**: When listed coins enter multi-month low-volatility consolidation ("crab" regimes), the bot continues scanning them, incurring false breakout stop-outs.

## 2. Requirements & Verification

### 2.1 Functional Requirements
- **FR1 (Dynamic Universe Screening)**: Programmatically scans all active Bybit USDT Linear Perpetuals (400+ coins) using 4 quantitative filters:
  - Liquidity / Execution: 24h Turnover $\ge \$25\text{M}$ USDT, Open Interest $\ge \$5\text{M}$ USDT.
  - Volatility: Normalized ATR ($\text{ATR} / \text{Price}) \ge 2.0\%$.
  - Trend Efficiency: Kaufman Efficiency Ratio $\ge 0.30$ or ADX $\ge 16$.
  - Relative Strength (RS): Momentum relative to BTC ($\text{Ret}_{\text{coin}} - \text{Ret}_{\text{BTC}}$).
  - Funding Rate Sanitization: $|\text{Funding}| \le 0.05\% / 8\text{h}$.
- **FR2 (Periodic Re-Ranking & Smooth Rotation)**: Screen and rank the universe into a `TOP-25 Dynamic Watchlist`. Active open positions are **never** discarded or prematurely closed due to universe rotation.
- **FR3 (Unified v3 Engine Core)**: Integrated:
  - 4h Donchian Breakout (30 bars / 120 hours) with zero-lookahead `shift(1)`.
  - Triple Trend Gate: ADX $\ge 18.0$, EMA50 > EMA200, BTC Delta Trend filter.
  - Risk Management: Half-Kelly 2.0% risk sizing, 3.5x gross portfolio leverage cap, 15% soft DD throttling, 30% hard DD halt, 3-loss streak kill-switch.
  - Pyramiding: Anti-martingale +50% add at +2 ATR with exact weighted average entry price and basket cash breakeven trailing stop.
  - Defensive News Shield: News Core v1.1 (Regime Guard macro embargo $\pm 15$m around FOMC/CPI/NFP + stablecoin depeg guard, Emergency Eject, Squeeze Escape, Runner Protection for $R \ge 1.5R$).
- **FR4 (Independent Web Dashboard & Deployment)**:
  - Dedicated web dashboard on Port `8082` with dynamic universe radar, live metrics, and webhook endpoint `POST /api/news_event`.
  - Automated deployment script `v3/setup_v3.sh`.

### 2.2 Non-Functional Requirements
- **NFR1 (Zero Future Lookahead)**: Zero price leakage across all indicators and screening calculations (`shift(1)` on closed bars).
- **NFR2 (Trade Accounting Integrity)**: Penny-perfect conservation between closed trades net PnL and realized equity.
- **NFR3 (100% Test Pass)**: All 30 unit, accounting invariant, and news defense tests pass with 100% success rate.
