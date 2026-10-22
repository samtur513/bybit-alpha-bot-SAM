# Issue 01: Implement Dynamic Universe Screener Module

Status: completed
Type: task

## Goal
Build `v3/src/universe_screener.py` implementing institutional 4-pillar universe selection:
1. Liquidity filter (24h Turnover >= $25M, OI >= $5M).
2. Volatility filter (Normalized ATR >= 2.0%).
3. Trend Quality filter (ADX >= 16 or Kaufman Efficiency Ratio >= 0.30).
4. Relative Strength vs BTC.
5. Funding Rate sanitization (|rate| <= 0.05% / 8h).

Composite scoring formula:
$$Score = 0.35 \cdot Trend + 0.30 \cdot RS + 0.20 \cdot VolumeExp + 0.15 \cdot NormATR$$

Output: Top-25 ranked universe for active trade scanning with smooth position retention.
Completed and verified with 7 unit tests.
