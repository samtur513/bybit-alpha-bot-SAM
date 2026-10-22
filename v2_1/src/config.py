"""
Configuration parameters for Multi-Alpha Growth Engine v2.1.
Includes Bybit MNT fee discount (0.09%), BTC Delta Filter, and Re-entry Cooldown.
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

# Universe: 18 high-volume liquid Bybit Linear Perpetuals
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]

# Core Risk & Sizing (Half-Kelly)
INITIAL_CAPITAL = Decimal("300.0")
BASE_RISK_PCT = Decimal("0.025")       # 2.5% Half-Kelly
CHOPPY_RISK_PCT = Decimal("0.0075")    # 0.75% during Regime Kill-Switch
MAX_LEVERAGE_CAP = Decimal("3.5")      # Hard 3.5x notional leverage cap
ATR_STOP_MULT = Decimal("2.5")         # 2.5x ATR initial stop-loss
LOSS_STREAK_THRESHOLD = 3              # Reduce risk after 3 consecutive losses
WIN_STREAK_RECOVERY = 2                # Recover risk after 2 consecutive wins

# v2.1 Specific Enhancements
REENTRY_COOLDOWN_BARS = 3              # 3-hour cooldown after closing a position
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

# Polling Interval
DEFAULT_POLL_INTERVAL_SEC = 5
