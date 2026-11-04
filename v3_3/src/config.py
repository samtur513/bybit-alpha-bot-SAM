"""
Configuration parameters for Multi-Alpha Growth Engine v3.3 Turbo.
Features:
- Timeframe: 1h ("60" in Bybit linear API)
- Donchian Window: 120 bars (120 hours = 5 days)
- Bar Interval: 3,600,000 ms (1 hour)
- Dedicated Port: 8086
- Dynamic Universe Screener (Top 25 Bybit Linear Perpetuals)
- News Core v1.2 Defensive Shield (Pre-Macro 30% Trim + Asymmetric Lockout [-45m : +30m])
- Streak-Based Adaptive Risk & Dynamic R:R (1:3 -> 1:4 -> 1:5):
  * Gear 1 (Base/Protective): Risk 1.5%, Target R:R 1:3.0 - 1:4.0, Pyramiding OFF
  * Gear 2 (Momentum): Risk 2.2%, Target R:R 1:3.5 - 1:4.5, Pyramiding +50%
  * Gear 3 (Hot Streak): Risk 3.2%, Target R:R 1:4.0 - 1:5.5+, Pyramiding +75%
  * Smooth Symmetrical Stepdown: -1 gear on loss, scratch neutral.
"""

import os
from decimal import Decimal
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V33.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V33.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V33.md"

# Server Port (Dedicated for v3.3 Turbo)
PORT = 8086
VERSION_NAME = "v3.3 Turbo (1h Streak Risk)"

# Base Universe Fallback (Top Liquid Assets)
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]
SYMBOLS = DEFAULT_SYMBOLS

# Dynamic Universe Screener Configuration
ENABLE_DYNAMIC_UNIVERSE = True
UNIVERSE_TOP_N = 25                    # Keep active top 25 high-momentum coins
SCREENER_INTERVAL_HOURS = 1            # Refresh screener every 1h aligned with 1h bars
MIN_24H_TURNOVER = 25_000_000.0        # $25M min 24h turnover
MIN_OPEN_INTEREST = 5_000_000.0        # $5M min open interest
MAX_FUNDING_RATE_ABS = 0.0005          # |Funding| <= 0.05% per 8h
MIN_NORM_ATR = 0.015                   # Min Normalized ATR (1.5%)

# Candle Timeframe & Bar Interval (1-Hour Resolution)
CANDLE_INTERVAL = "60"                 # 1-hour candles ("60" in Bybit API)
BAR_INTERVAL_MS = 3600 * 1000          # 3,600,000 ms
DONCHIAN_WINDOW = 120                  # 120 bars (120 hours / 5 days Donchian channel)

# Core Capital & Risk Baseline
INITIAL_CAPITAL = Decimal("300.0")
BASE_RISK_PCT = Decimal("0.010")       # 1.0% initial base risk (Gear 1, scaled for 4 slots)
MOMENTUM_RISK_PCT = Decimal("0.016")   # 1.6% moderate risk (Gear 2)
TURBO_MAX_RISK_PCT = Decimal("0.024")  # 2.4% maximum aggressive risk (Gear 3)
MAX_CONCURRENT_POSITIONS = 4           # Maximum 4 concurrent active positions

MAX_LEVERAGE_CAP = Decimal("4.0")      # Hard 4.0x position leverage cap
PORTFOLIO_LEVERAGE = Decimal("4.0")    # Hard 4.0x aggregate portfolio cap
DRAWDOWN_LIMIT_PCT = Decimal("30.0")   # Automated trading halt at 30% DD
CIRCUIT_BREAKER_DD_PCT = Decimal("10.0") # Drops to Gear 1 if DD >= 10.0%
ATR_STOP_MULT = Decimal("2.5")         # 2.5x ATR initial stop-loss

# Dynamic R:R Cascade Take-Profit Targets
ENABLE_CASCADE_TP = True               # Active Cascade Take-Profit in v3.3
GEAR1_TP1_R = Decimal("3.0")           # Gear 1: TP1 at +3.0R (35% size) -> Stop to BE
GEAR1_TP2_R = Decimal("4.0")           # Gear 1: TP2 at +4.0R (35% size) -> Stop to +1.5R
GEAR2_TP1_R = Decimal("3.5")           # Gear 2: TP1 at +3.5R (35% size) -> Stop to BE
GEAR2_TP2_R = Decimal("4.5")           # Gear 2: TP2 at +4.5R (35% size) -> Stop to +2.0R
GEAR3_TP1_R = Decimal("4.0")           # Gear 3: TP1 at +4.0R (30% size) -> Stop to BE
GEAR3_TP2_R = Decimal("5.5")           # Gear 3: TP2 at +5.5R (35% size) -> Stop to +2.5R

# Pyramiding Ratios per Gear
GEAR1_PYRAMID_RATIO = Decimal("0.0")   # Gear 1: Pyramiding OFF
GEAR2_PYRAMID_RATIO = Decimal("0.50")  # Gear 2: +50% position size at +2.0 ATR
GEAR3_PYRAMID_RATIO = Decimal("0.75")  # Gear 3: +75% position size at +2.0 ATR
PYRAMID_TRIGGER_ATR = Decimal("2.0")   # Add-on at +2.0 ATR profit

# Trend Regime Filter
MIN_ADX_TREND = 18.0                   # ADX >= 18.0 catches trend ignition and blocks chop
REENTRY_COOLDOWN_BARS = 3              # 3-bar (3-hour) cooldown after closing a position
ENABLE_BTC_DELTA_FILTER = True         # Require BTC bullish trend for altcoin longs

# Fast-Scratch Defense
ENABLE_FAST_SCRATCH_EXIT = True        # Early cut if adverse excursion >= 0.5 ATR by bar 3
FAST_SCRATCH_BARS = 3
FAST_SCRATCH_MFE_R = Decimal("0.3")

# Bybit Non-VIP Execution Friction
BASE_TAKER_FEE = Decimal("0.0009")     # 0.09% (with MNT 10% fee discount)
BASE_SLIPPAGE = Decimal("0.0002")      # 0.02% conservative execution slippage
