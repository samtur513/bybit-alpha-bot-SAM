"""
Configuration for v3.14 Funding-Aware Synthetic Basket Dispersion Engine (Port 8094).
Monitors altcoins against a synthetic benchmark (50% BTC + 50% ETH), trading statistical
dispersion (residual mean-reversion) combined with positive funding carry.

Auditor-Hardened Enhancements:
1. Fast 4h Benchmark KER filter (KER <= 0.45) - blocks entries during runaway macro trends.
2. Anti-Adverse Selection gate: 20% confirmed pullback from residual extreme before entering.
3. 2-Stage Smart Exit: 50% TP1 at Z <= 0.50 + move remaining 50% stop to Breakeven (+0.05%).
4. Dynamic dislocation hurdle: max(0.90%, 1.2 * ATR_15m) ensuring >= 3.5x roundtrip fee coverage.
5. Funding Rate differential filter: requires delta FR >= +0.010% aligned with carry direction.
6. Realistic holding horizon: 5 to 45 minutes (max hold time 2700s).
7. Strict concurrency cap: maximum 2 concurrent alt dispersion baskets.
"""

from dataclasses import dataclass
from typing import List

PORT_8094 = 8094
MODULE_ID = "M4_DISPERSION_8094"
INITIAL_CAPITAL_USDT = 300.0

# Synthetic Benchmark Components
BENCHMARK_WEIGHTS = {
    "BTCUSDT": 0.50,
    "ETHUSDT": 0.50,
}

# Monitored Altcoins for Dispersion Trading
DISPERSION_ALTS = [
    "SOLUSDT",
    "SUIUSDT",
    "NEARUSDT",
    "1000PEPEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
]

ALL_MONITORED_SYMBOLS = list(set(DISPERSION_ALTS + list(BENCHMARK_WEIGHTS.keys())))

# Fee Model (With MNT discount: Maker 0.0324%, Taker 0.090%)
FEE_MAKER = 0.000324
FEE_TAKER = 0.000900
# 3-leg basket roundtrip: Maker on Alt + Taker on BTC/ETH hedge
ROUNDTRIP_BASKET_FEE = 2.0 * (FEE_MAKER + FEE_TAKER) # ~0.002448 = 0.2448%

# Signal & Risk Parameters (Auditor-Hardened)
MIN_Z_ENTRY = 2.00
MIN_DISLOCATION_PCT = 0.0090      # 90 bps minimum dislocation hurdle
ATR_MULTIPLIER_HURDLE = 1.20      # Dynamic hurdle based on Alt 15m ATR
PULLBACK_CONFIRM_PCT = 0.20       # 20% confirmed retracement from residual peak
MAX_BENCHMARK_KER_4H = 0.45       # Macro trend filter (blocks trend breakdown/breakout)
MIN_FUNDING_DIFF_PCT = 0.00010    # +1.0 bps per 8h funding differential carry booster

# Position Sizing
BASE_ALT_NOTIONAL_USDT = 45.0     # $45 alt notional per basket
MAX_CONCURRENT_BASKETS = 2        # Maximum 2 concurrent active baskets

# Exit Parameters (2-Stage Smart Exit)
TP1_Z_THRESHOLD = 0.50            # 50% Partial TP when residual Z drops to 0.50
TP2_Z_RUNNER_THRESHOLD = 0.00     # Runner TP at complete residual convergence (Z <= 0.00)
BE_PROFIT_BUFFER_PCT = 0.0005     # +0.05% Breakeven profit floor
SL_Z_DIVERGENCE = 3.60            # Hard divergence stop loss (|Z| >= 3.60)
MAX_HOLD_SECONDS = 2700.0         # 45 minutes maximum time stop

# Daily Circuit Breaker
MAX_DAILY_LOSS_USDT = 6.00        # 2.0% of $300 deposit
