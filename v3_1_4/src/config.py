"""
Configuration parameters for Multi-Alpha Growth Engine v3.1.4 (4-Hour Breakout Champion).
Identical to v3.1.1 in all quantitative and risk logic, but operates on 4-hour candle grid:
- Timeframe: 4h ("240" in Bybit API)
- Donchian Window: 30 bars (30 * 4h = 120h = 5 days)
- Bar Interval: 14,400,000 ms (4 hours)
- Port: 8084
- Dynamic Universe Screener (Top 25 Bybit Linear Perpetuals)
- News Core v1.2 Defensive Shield (Pre-Macro 30% Trim + Asymmetric Lockout [-45m : +30m])
"""

import os
from decimal import Decimal
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V314.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V314.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V314.md"

# Server Port (Dedicated for v3.1.4 4h)
PORT = 8084
VERSION_NAME = "v3.1.4 (4h)"

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
SCREENER_INTERVAL_HOURS = 4            # Refresh screener every 4h aligned with 4h bars
MIN_24H_TURNOVER = 25_000_000.0        # $25M min 24h turnover
MIN_OPEN_INTEREST = 5_000_000.0        # $5M min open interest
MAX_FUNDING_RATE_ABS = 0.0005          # |Funding| <= 0.05% per 8h
MIN_NORM_ATR = 0.015                   # Min Normalized ATR (1.5%)

# Candle Timeframe & Bar Interval (4-Hour Resolution)
CANDLE_INTERVAL = "240"                # 4-hour candles ("240" in Bybit API)
BAR_INTERVAL_MS = 4 * 3600 * 1000      # 14,400,000 ms
DONCHIAN_WINDOW = 30                   # 30 bars (30 * 4h = 120 hours / 5 days Donchian channel)

# Core Risk & Sizing (Champion 2.0% Half-Kelly)
INITIAL_CAPITAL = Decimal("300.0")
BASE_RISK_PCT = Decimal("0.02")        # 2.0% Half-Kelly
CHOPPY_RISK_PCT = Decimal("0.006")     # 0.6% during Kill-Switch
MAX_LEVERAGE_CAP = Decimal("3.5")      # Hard 3.5x position leverage cap
PORTFOLIO_LEVERAGE = Decimal("3.5")    # Hard 3.5x aggregate portfolio cap
SOFT_DRAWDOWN_LIMIT_PCT = Decimal("15.0")  # Halves risk above 15% DD
DRAWDOWN_LIMIT_PCT = Decimal("30.0")   # Automated trading halt at 30% DD
ATR_STOP_MULT = Decimal("2.5")         # 2.5x ATR initial stop-loss
LOSS_STREAK_THRESHOLD = 3              # Reduce risk after 3 consecutive losses
WIN_STREAK_RECOVERY = 2                # Recover risk after 2 consecutive wins

# Trend Regime Filter
MIN_ADX_TREND = 18.0                   # ADX >= 18.0 catches trend ignition and blocks chop
REENTRY_COOLDOWN_BARS = 3              # 3-bar (12-hour) cooldown after closing a position
ENABLE_BTC_DELTA_FILTER = True         # Require BTC bullish trend for altcoin longs

# Auto-Gearbox Configuration
ENABLE_AUTO_GEARBOX = True
GEARBOX_DOWN_THRESHOLD_1 = Decimal("15.0")  # 15.0% DD -> Gear 2 (1.0% risk)
GEARBOX_DOWN_THRESHOLD_2 = Decimal("22.0")  # 22.0% DD -> Gear 1 (0.6% risk)
GEARBOX_RECOVERY_RATIO = Decimal("0.4")     # 40% recovery from trough -> upshift

# Fast-Scratch Exit Configuration
ENABLE_FAST_SCRATCH_EXIT = False
FAST_SCRATCH_BARS = 3
FAST_SCRATCH_MFE_THRESHOLD_R = Decimal("0.3")

# 3-Stage Cascade Take-Profit
ENABLE_CASCADE_TP = False
CASCADE_TP1_R_MULT = Decimal("3.5")
CASCADE_TP2_R_MULT = Decimal("5.5")
CASCADE_TP1_RATIO = Decimal("0.30")
CASCADE_TP2_RATIO = Decimal("0.40")

# RVOL & Delta OI Quantitative Filters
ENABLE_RVOL_FILTER = False
MIN_RVOL = 1.2
ENABLE_DELTA_OI_FILTER = False
ENABLE_ATR_EXHAUSTION_GUARD = False
MAX_DAILY_ATR_EXHAUSTION = 0.80

# Pyramiding (Anti-Martingale)
PYRAMID_TRIGGER_ATR = Decimal("2.0")   # Add +50% at +2.0 ATR profit
PYRAMID_ADD_RATIO = Decimal("0.5")     # 50% add-on quantity
TRAILING_ATR_MULT = Decimal("2.5")     # Ratchet trailing stop distance

# Fees & Execution (Bybit Non-VIP with 10% MNT Discount)
BASE_TAKER_FEE = Decimal("0.0009")     # 0.09% (0.10% * 0.9 MNT discount)
BASE_MAKER_FEE = Decimal("0.000324")   # 0.0324%
BASE_SLIPPAGE = Decimal("0.0002")      # 0.02% conservative slippage
FUNDING_8H_ESTIMATE = Decimal("0.0001")# 0.01% estimated 8h funding rate

# Portfolio Delta Limits
MAX_CONCURRENT_LONGS = 2
MAX_CONCURRENT_SHORTS = 2

# News Core Defensive Shield Configuration (v1.2)
ENABLE_NEWS_DEFENSE = True             # Enable News Core defensive shields
NEWS_CONFIG_PROFILE = "defensive_only" # Strictly defensive (no offensive N1/N3 orders)
ENABLE_MACRO_BLACKOUT = True           # Block entries in macro lockout window
ENABLE_DEPEG_GUARD = True              # Block entries on stablecoin depeg (>0.8%)
ENABLE_EMERGENCY_EJECT = True          # Close Long on verified CRASH_SHOCK
ENABLE_SQUEEZE_ESCAPE = True           # Close Short on MAJOR_CATALYST
ENABLE_RUNNER_PROTECTION = True        # Trail stop instead of ejecting if R >= 1.5R

# Configurable Macro Lockout & Profit Trimming Proportions (v1.2)
MACRO_LOCKOUT_BEFORE_MINUTES = int(os.getenv("MACRO_LOCKOUT_BEFORE_MINUTES", "45")) # Lockout before event (default: 45 min)
MACRO_LOCKOUT_AFTER_MINUTES = int(os.getenv("MACRO_LOCKOUT_AFTER_MINUTES", "30"))   # Lockout after event (default: 30 min)
ENABLE_MACRO_PROFIT_TRIMMING = os.getenv("ENABLE_MACRO_PROFIT_TRIMMING", "True").lower() == "true" # Bank profit at T-45m
MACRO_TRIM_RATIO = Decimal(os.getenv("MACRO_TRIM_RATIO", "0.30"))                   # Trim ratio: 30%
MACRO_TRIM_MIN_R = Decimal(os.getenv("MACRO_TRIM_MIN_R", "1.0"))                   # Min profit R-multiple

# Polling Interval
DEFAULT_POLL_INTERVAL_SEC = 5
