"""
Configuration parameters for Multi-Alpha Growth Engine v2.1.
Includes Bybit MNT fee discount (0.09%), BTC Delta Filter, Re-entry Cooldown, and Audited Risk Caps.
"""

from decimal import Decimal
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V21.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V21.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V21.md"

# Server Port
PORT = 8081

# Data Dirs & Timestamps for Backtests
PARQUET_DATA_DIR = BASE_DIR.parent / "data" / "parquet"
DEV_START_TS = 1704067200000
FINAL_END_TS = 1735689600000

# Universe: 18 high-volume liquid Bybit Linear Perpetuals
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]
SYMBOLS = DEFAULT_SYMBOLS

# Timeframe & Strategy Architecture (I1: 4h Trend Following)
CANDLE_INTERVAL = "240"                # 4-hour candles ("240" in Bybit API)
BAR_INTERVAL_MS = 4 * 3600 * 1000      # 14,400,000 ms
DONCHIAN_WINDOW = 30                   # 30 bars (120 hours on 4h timeframe)

# Core Risk & Sizing (Optimal 2.0% Half-Kelly for Maximum Profit)
INITIAL_CAPITAL = Decimal("300.0")
BASE_RISK_PCT = Decimal("0.02")        # 2.0% optimal Half-Kelly (Champion ROI: +55.5%)
CHOPPY_RISK_PCT = Decimal("0.006")     # 0.6% during Regime Kill-Switch
MAX_LEVERAGE_CAP = Decimal("3.5")      # Hard 3.5x position leverage cap
PORTFOLIO_LEVERAGE = Decimal("3.5")    # Hard 3.5x aggregate portfolio cap
SOFT_DRAWDOWN_LIMIT_PCT = Decimal("15.0")  # Soft de-risking: halves risk above 15% DD
DRAWDOWN_LIMIT_PCT = Decimal("30.0")   # Hard automated trading halt at 30% drawdown
ATR_STOP_MULT = Decimal("2.5")         # 2.5x ATR initial stop-loss
LOSS_STREAK_THRESHOLD = 3              # Reduce risk after 3 consecutive losses
WIN_STREAK_RECOVERY = 2                # Recover risk after 2 consecutive wins

# Trend Regime Filter
MIN_ADX_TREND = 18.0                   # ADX >= 18.0 catches trend ignition and blocks chop

# v2.1 Specific Enhancements
REENTRY_COOLDOWN_BARS = 3              # 3-bar (12-hour) cooldown after closing a position
ENABLE_BTC_DELTA_FILTER = True         # Require BTC bullish trend for altcoin longs

# Pyramiding (Anti-Martingale)
PYRAMID_TRIGGER_ATR = Decimal("2.0")   # Add +50% at +2.0 ATR profit
PYRAMID_ADD_RATIO = Decimal("0.5")     # 50% add-on quantity
TRAILING_ATR_MULT = Decimal("2.5")     # Ratchet trailing stop distance

# Fees & Execution (Bybit Non-VIP with 10% MNT Discount)
BASE_TAKER_FEE = Decimal("0.0009")     # 0.09% (0.10% * 0.9 MNT discount)
BASE_MAKER_FEE = Decimal("0.000324")   # 0.0324% (0.036% * 0.9 MNT discount)
BASE_SLIPPAGE = Decimal("0.0002")      # 0.02% conservative slippage
FUNDING_8H_ESTIMATE = Decimal("0.0001")# 0.01% estimated 8h funding rate

# Portfolio Delta Limits
MAX_CONCURRENT_LONGS = 2
MAX_CONCURRENT_SHORTS = 2

# Dual-Regime & Range Submodule Parameters
ENABLE_RANGE_SUBMODULE = False         # Disabled by default; toggle True for shadow/active range trading
RANGE_RISK_PER_TRADE_PCT = Decimal("0.005") # 0.5% micro-risk per range trade
RANGE_MAX_POSITIONS = 2                # Max 2 concurrent range positions
RANGE_SL_ATR_MULT = Decimal("1.0")     # 1.0x ATR stop loss
RANGE_MIN_TP_PCT = Decimal("0.010")    # 1.0% min TP distance
RANGE_MAX_TP_PCT = Decimal("0.020")    # 2.0% max TP distance
ENABLE_REGIME_CLASSIFIER = True        # ADX & Volatility compression gate

# News Core Defensive Shield Configuration
ENABLE_NEWS_DEFENSE = True             # Enable News Core defensive shield (Regime Guard + Position Matrix)
NEWS_CONFIG_PROFILE = "defensive_only" # Strictly defensive (no offensive N1/N3 trading)
ENABLE_MACRO_BLACKOUT = True           # Block entries ±15m around FOMC, CPI, NFP
ENABLE_DEPEG_GUARD = True              # Block entries on stablecoin depeg (>0.8%)
ENABLE_EMERGENCY_EJECT = True          # Close Long on verified CRASH_SHOCK
ENABLE_SQUEEZE_ESCAPE = True           # Close Short on MAJOR_CATALYST
ENABLE_RUNNER_PROTECTION = True        # Trail stop instead of ejecting if R >= 1.5R

# Polling Interval
DEFAULT_POLL_INTERVAL_SEC = 5
