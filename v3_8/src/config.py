"""
Configuration for v3.8 NewDragon Trend Lab (Port 8091).
Tests NewDragon v4.0 Universal Phase 1 (UniversalRiskBridge + AdaptiveExitFSM + True 1R)
on 1h Trend Breakout over Bybit 400+ Dynamic Universe.
"""

from decimal import Decimal
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

PORT = int(os.getenv("PORT_V38", "8091"))
VERSION_NAME = "v3.8 NewDragon Trend Lab"
VERSION = "3.8.0"

INITIAL_CAPITAL = Decimal("300.00")
TIMEFRAME = "1h"
CANDLE_INTERVAL = "60"
BAR_INTERVAL_MS = 3600 * 1000

# Risk & Sizing Profile (NewDragon v4 True 1R)
MAX_LEVERAGE = 3.5
RISK_PER_TRADE_PCT = Decimal("0.0080")  # 0.8% True 1R
MAX_PORTFOLIO_HEAT_PCT = Decimal("0.035")  # 3.5%
MAX_CONCURRENT_POSITIONS = 3

# Fee Profile (Bybit Non-VIP with 10% MNT discount)
TAKER_FEE = Decimal("0.000900")   # 9.0 bps
MAKER_FEE = Decimal("0.000324")   # 3.24 bps
SLIPPAGE = Decimal("0.000500")    # 5.0 bps

# NewDragon v4 Phase 1 Exit Parameters
STAGE1_R = 0.85
STAGE1_LOCK_R = -0.35
STAGE2_R = 1.40
STAGE2_CLOSE_PCT = 0.60           # 60% partial take profit via Maker limit
STAGE2_LOCK_R = 0.35             # Move stop to guaranteed profit +0.35R
STAGE3_R = 2.00
CHANDELIER_MULT = 2.5
CHANDELIER_COMPRESSED_MULT = 1.2

# Donchian & Trend
DONCHIAN_WINDOW = 14
EMA_FAST = 50
EMA_SLOW = 200
ADX_THRESHOLD = 16.0

# Dynamic Screener
MIN_TURNOVER_24H_USDT = Decimal("5000000")  # $5M
MAX_ACTIVE_SYMBOLS = 16
SCREENER_ROTATION_INTERVAL_HOURS = 24

# Defensive Shields
ENABLE_NEWS_DEFENSE = True
REGIME_GUARD_PRE_MINUTES = 15
REGIME_GUARD_POST_MINUTES = 10

# Paths
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
PERSISTENT_STATE_FILE = REPORTS_DIR / "STATE_V38.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V38.json"
LIVE_MD_FILE = REPORTS_DIR / "LIVE_STATUS_V38.md"
