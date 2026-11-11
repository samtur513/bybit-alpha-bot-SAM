"""
Configuration parameters for Multi-Alpha Market-Neutral Engine v3.5-REPAIRED (Phase F1B).
Isolated from v3_5 to maintain strict audit integrity.

Features:
- Timeframe: 1h ("60" in Bybit linear API)
- Cross-Sectional Momentum (CAR-CSMOM): 72 hours lookback (strictly preserved from v3.5)
- Rebalance Interval: 4 hours (strictly preserved from v3.5)
- Market-Neutral Beta ~ 0.0 (Long Top 2 Leaders / Short Bottom 2 Laggards)
- Hysteresis Rank Retention: Top 5 Long / Bottom 5 Short
- Official Bybit VIP-0 API Fee Model (No MNT discount on API orders):
  * Taker Fee: 0.1000% (0.00100)
  * Maker Fee: 0.0360% (0.00036)
  * Adverse Slippage Penalty: 5 bps (0.00050)
- Penny-Perfect Financial Ledger with Proportional Fee Allocation for Partial Take Profit
- Bybit Exchange Constraints (minNotional = $5.00 USDT, minOrderQty)
- Price Spike Filter (35% threshold) to prevent corrupting trailing stops
"""

from decimal import Decimal
from enum import Enum
from pathlib import Path

class RuleSource(str, Enum):
    STATIC_UNVERIFIED = "STATIC_UNVERIFIED"
    STATIC_CONFIG = "STATIC_CONFIG"
    EXCHANGE_REST = "EXCHANGE_REST"
    HISTORICAL_SPEC = "HISTORICAL_SPEC"
    MOCK = "MOCK"

class QuoteSource(str, Enum):
    EXCHANGE_L1 = "EXCHANGE_L1"
    EXCHANGE_TICKER = "EXCHANGE_TICKER"
    REST_SNAPSHOT = "REST_SNAPSHOT"
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    SYNTHETIC_SCENARIO = "SYNTHETIC_SCENARIO"
    SYNTHETIC = "SYNTHETIC"

RULE_SPEC_VERSION = "bybit_v5_spec_2026_03"
RULE_STATIC_TIMESTAMP = 1709251200000  # Explicit static release timestamp (2026-03-01 UTC)
MAX_RULE_AGE_MS = 24 * 3600 * 1000     # 24 hours max staleness for live exchange rules

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V35_REPAIRED.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V35_REPAIRED.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V35_REPAIRED.md"
LEDGER_DB_FILE = REPORTS_DIR / "financial_ledger_v35_repaired.db"

# Server Port (Dedicated for v3.5-REPAIRED - port 8188 isolates from VPS live v3.5 on 8088)
PORT = 8188
VERSION_NAME = "v3.5-REPAIRED Alpha Pairs (Phase F1C Audit Hardened)"

# Trading Universe (18 High-Liquidity Perps + BTC Benchmark)
BENCHMARK_SYMBOL = "BTCUSDT"
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]

# Candle Timeframe & Bar Interval (1-Hour Resolution)
CANDLE_INTERVAL = "60"                 # 1-hour candles ("60" in Bybit API)
BAR_INTERVAL_MS = 3600 * 1000          # 3,600,000 ms

# Quantitative Strategy Parameters (100% UNCHANGED FROM V35-ORIGINAL)
MOMENTUM_WINDOW_HOURS = 72             # 72-hour responsive relative momentum vs BTC
REBALANCE_INTERVAL_HOURS = 4           # Ranking check every 4 hours
ENTRY_TOP_K = 2                        # Max 2 Longs
ENTRY_BOTTOM_K = 2                     # Max 2 Shorts
MIN_REL_STRENGTH_LONG = Decimal("0.025")   # Long candidate must outperform BTC by >= 2.5%
MIN_REL_STRENGTH_SHORT = Decimal("-0.025") # Short candidate must underperform BTC by <= -2.5%
MIN_KER_THRESHOLD = Decimal("0.0")

# Hysteresis Rank Retention (100% UNCHANGED)
HYSTERESIS_TOP_RANK = 5                # Keep existing Long while in Top 5 and RS > 0
HYSTERESIS_BOTTOM_RANK = 5             # Keep existing Short while in Bottom 5 and RS < 0

# Friction Filters & Spreads (100% UNCHANGED)
MIN_PAIR_NET_ALPHA_SPREAD = Decimal("0.050")
MIN_REBALANCE_SPREAD_ADVANTAGE = Decimal("0.020")
HYSTERESIS_EXIT_RS_BUFFER = Decimal("0.015")
MIN_HOLD_BARS_REBALANCE = 6
PROTECT_PROFITABLE_RUNNERS = True

# Core Capital & Risk Baseline
INITIAL_CAPITAL = Decimal("300.0")
BASE_RISK_PER_LEG_PCT = Decimal("1.0") # 1.0% capital risk per individual leg
MAX_LEVERAGE_PER_LEG = Decimal("1.5")  # Max 1.5x leverage per leg
MAX_PORTFOLIO_LEVERAGE = Decimal("2.0")  # Max 2.0x aggregate leverage across all open positions
MAX_PORTFOLIO_BETA = Decimal("0.35")     # Max allowable net portfolio beta vs BTC
DRAWDOWN_LIMIT_PCT = Decimal("15.0")    # Trading halt threshold (Aligned to 15.0% as per audit spec)
BAR_SYNC_TIMEOUT_SECONDS = 30.0         # Max seconds to wait for 18-symbol kline synchronization
MAX_QUOTE_AGE_MS = 5000                 # Max 5.0 seconds orderbook quote staleness

# Stop Loss & Take Profit Parameters (100% UNCHANGED)
ATR_PERIOD = 24
STOP_LOSS_ATR_MULT = Decimal("2.5")    # 2.5x ATR initial stop
TRAILING_ACTIVATION_R = Decimal("1.5") # Activate trailing stop after +1.5R
TRAILING_DISTANCE_ATR = Decimal("2.0") # Trail behind highest/lowest price by 2.0x ATR
PARTIAL_TP_R = Decimal("4.0")          # Partial TP at +4.0R (close 50%)
PARTIAL_TP_RATIO = Decimal("0.50")     # 50% partial exit ratio

# Official Bybit API Non-VIP Fee Model (No MNT discount on API orders)
FEE_TAKER = Decimal("0.00100")         # 0.100%
FEE_MAKER = Decimal("0.00036")         # 0.036%
BASE_SLIPPAGE = Decimal("0.00050")     # 5 bps conservative adverse execution penalty

# Exchange Execution Constraints & Anomaly Filters (F1B Hardening)
MIN_NOTIONAL_USDT = Decimal("5.0")     # Bybit linear perpetual minNotionalValue threshold
MIN_ORDER_QTY_DEFAULT = Decimal("0.001")
MAX_TICK_SPIKE_PCT = Decimal("0.35")   # 35% max allowable 1-tick deviation before freezing trailing stop

# Enhanced Market Validity Parameters (F1B Auditor Requirements)
MAX_QUOTE_STALENESS_SEC = 15           # Maximum allowable data staleness (seconds)
MAX_BID_ASK_SPREAD_PCT = Decimal("0.0050")  # 0.50% max allowable bid/ask spread
MAX_VOLATILITY_JUMP_ATR_MULT = Decimal("3.5")  # Max 3.5x ATR jump from last known valid price
CONFIRMED_SPIKE_TICKS = 2              # Number of consecutive breach ticks to confirm real move vs bad tick

# Bybit V5 Linear Perpetuals Contract Specifications (Lot & Notional Rules)
# FAIL-CLOSED: Every universe symbol must be explicitly configured. Unknown or unlisted symbols are strictly rejected.
BYBIT_INSTRUMENT_RULES = {
    "BTCUSDT": {
        "tick_size": Decimal("0.10"),
        "qty_step": Decimal("0.001"),
        "min_order_qty": Decimal("0.001"),
        "max_order_qty": Decimal("100.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "ETHUSDT": {
        "tick_size": Decimal("0.01"),
        "qty_step": Decimal("0.01"),
        "min_order_qty": Decimal("0.01"),
        "max_order_qty": Decimal("1000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "SOLUSDT": {
        "tick_size": Decimal("0.01"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("10000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "SUIUSDT": {
        "tick_size": Decimal("0.0001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("500000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "DOGEUSDT": {
        "tick_size": Decimal("0.00001"),
        "qty_step": Decimal("1.0"),
        "min_order_qty": Decimal("1.0"),
        "max_order_qty": Decimal("2000000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "AVAXUSDT": {
        "tick_size": Decimal("0.001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("20000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "NEARUSDT": {
        "tick_size": Decimal("0.001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("100000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "LINKUSDT": {
        "tick_size": Decimal("0.001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("50000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "APTUSDT": {
        "tick_size": Decimal("0.001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("50000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "ADAUSDT": {
        "tick_size": Decimal("0.0001"),
        "qty_step": Decimal("1.0"),
        "min_order_qty": Decimal("1.0"),
        "max_order_qty": Decimal("1000000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "XRPUSDT": {
        "tick_size": Decimal("0.0001"),
        "qty_step": Decimal("1.0"),
        "min_order_qty": Decimal("1.0"),
        "max_order_qty": Decimal("1000000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "BNBUSDT": {
        "tick_size": Decimal("0.01"),
        "qty_step": Decimal("0.01"),
        "min_order_qty": Decimal("0.01"),
        "max_order_qty": Decimal("1000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "TIAUSDT": {
        "tick_size": Decimal("0.001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("50000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "INJUSDT": {
        "tick_size": Decimal("0.001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("50000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "ARBUSDT": {
        "tick_size": Decimal("0.0001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("500000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "OPUSDT": {
        "tick_size": Decimal("0.0001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("500000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "1000PEPEUSDT": {
        "tick_size": Decimal("0.000001"),
        "qty_step": Decimal("100.0"),
        "min_order_qty": Decimal("100.0"),
        "max_order_qty": Decimal("100000000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
    "RENDERUSDT": {
        "tick_size": Decimal("0.001"),
        "qty_step": Decimal("0.1"),
        "min_order_qty": Decimal("0.1"),
        "max_order_qty": Decimal("50000.0"),
        "min_notional_value": Decimal("5.0"),
        "status": "Trading",
    },
}


