"""
Configuration parameters for Multi-Alpha Growth Engine v3.
Features:
- Dynamic Universe Selection (400+ Bybit Linear Perpetuals filtered to Top-25 by Composite Momentum Score)
- 4-Hour Trend Following (Donchian 30 = 120h, Zero-Lookahead PIT)
- News Core v1.1 Defensive Shield (Regime Guard + Position Matrix)
- Penny-perfect trade accounting and weighted average entry price
- Port 8082 for parallel A/B/C testing with v2.0 (8080) and v2.1 (8081)
"""

from decimal import Decimal
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V3.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V3.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V3.md"

# Server Port (Dedicated for v3)
PORT = 8082

# Data Dirs & Timestamps for Backtests
PARQUET_DATA_DIR = BASE_DIR.parent / "data" / "parquet_2024_2026"
DEV_START_TS = 1704067200000
FINAL_END_TS = 1735689600000

# Base Universe Fallback (Top Liquid Assets)
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]
SYMBOLS = DEFAULT_SYMBOLS

# Dynamic Universe Screener Configuration (v3 Feature)
ENABLE_DYNAMIC_UNIVERSE = True
UNIVERSE_TOP_N = 25                    # Keep active top 25 high-momentum coins
SCREENER_INTERVAL_HOURS = 4            # Re-rank universe every 4 hours (aligned with 4h candle close)
MIN_24H_TURNOVER = 25_000_000.0        # $25M USDT min 24h turnover
MIN_OPEN_INTEREST = 5_000_000.0        # $5M USDT min open interest
MAX_FUNDING_RATE_ABS = 0.0005          # Max |funding| 0.05% / 8h
MIN_NORM_ATR = 0.020                   # 2.0% minimum normalized ATR

# Timeframe & Strategy Architecture (I1: 4h Trend Following)
CANDLE_INTERVAL = "240"                # 4-hour candles ("240" in Bybit API)
BAR_INTERVAL_MS = 4 * 3600 * 1000      # 14,400,000 ms
DONCHIAN_WINDOW = 30                   # 30 bars (120 hours on 4h timeframe)

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

# Auto-Gearbox Configuration (v3.1)
ENABLE_AUTO_GEARBOX = True
GEARBOX_DOWN_THRESHOLD_1 = Decimal("10.0")  # 10.0% DD -> Gear 2 (1.0% risk, no pyramiding)
GEARBOX_DOWN_THRESHOLD_2 = Decimal("16.0")  # 16.0% DD -> Gear 1 (0.5% risk, survival mode)
GEARBOX_RECOVERY_RATIO = Decimal("0.5")     # 50% recovery from trough -> upshift

# Fast-Scratch Exit Configuration (v3.1)
ENABLE_FAST_SCRATCH_EXIT = True
FAST_SCRATCH_BARS = 3                      # Check first 3 bars (12h) after breakout
FAST_SCRATCH_MFE_THRESHOLD_R = Decimal("0.3")  # Scratch if profit < 0.3 ATR and price returns inside channel

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

# News Core Defensive Shield Configuration
ENABLE_NEWS_DEFENSE = True             # Enable News Core defensive shields
NEWS_CONFIG_PROFILE = "defensive_only" # Strictly defensive (no offensive N1/N3 orders)
ENABLE_MACRO_BLACKOUT = True           # Block entries ±15m around FOMC, CPI, NFP
ENABLE_DEPEG_GUARD = True              # Block entries on stablecoin depeg (>0.8%)
ENABLE_EMERGENCY_EJECT = True          # Close Long on verified CRASH_SHOCK
ENABLE_SQUEEZE_ESCAPE = True           # Close Short on MAJOR_CATALYST
ENABLE_RUNNER_PROTECTION = True        # Trail stop instead of ejecting if R >= 1.5R

# Polling Interval
DEFAULT_POLL_INTERVAL_SEC = 5
