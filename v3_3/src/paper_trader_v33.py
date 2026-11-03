"""
Paper Trading Daemon v3.3 Turbo (Port 8086).
Operates on 1h candles with Dynamic Universe Screener, News Core v1.2,
and Streak-Based Adaptive Risk & Dynamic R:R (1:3 -> 1:4 -> 1:5).
"""

from decimal import Decimal
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(__file__).resolve().parent.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v3.src.paper_trader_v3 import PaperTraderV3
from v3_3.src.config import (
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
    PORT,
    VERSION_NAME,
    INITIAL_CAPITAL,
    BASE_RISK_PCT,
    MAX_LEVERAGE_CAP,
    PORTFOLIO_LEVERAGE,
    DRAWDOWN_LIMIT_PCT,
    ATR_STOP_MULT,
    ENABLE_CASCADE_TP,
    MIN_ADX_TREND,
    REENTRY_COOLDOWN_BARS,
    ENABLE_BTC_DELTA_FILTER,
    ENABLE_FAST_SCRATCH_EXIT,
    FAST_SCRATCH_BARS,
    FAST_SCRATCH_MFE_R,
    BASE_TAKER_FEE,
    BASE_SLIPPAGE,
    CANDLE_INTERVAL,
    BAR_INTERVAL_MS,
    DEFAULT_SYMBOLS,
)
from v3_3.src.core_v33 import CoreV33Engine

logger = logging.getLogger("PaperTraderV33")


class PaperTraderV33(PaperTraderV3):
    """
    Paper trader daemon running v3.3 Turbo on Port 8086.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = INITIAL_CAPITAL,
        state_file: Path = PERSISTENT_STATE_FILE,
        status_file: Path = LIVE_STATUS_FILE,
        md_file: Path = LIVE_MD_FILE,
        enable_news_defense: bool = True,
    ):
        # We invoke PaperTraderV3 setup but override engine with CoreV33Engine
        super().__init__(
            symbols=symbols or list(DEFAULT_SYMBOLS),
            initial_capital=initial_capital,
            base_risk_pct=BASE_RISK_PCT,
            max_leverage=MAX_LEVERAGE_CAP,
            state_file=state_file,
            status_file=status_file,
            md_file=md_file,
            enable_news_defense=enable_news_defense,
        )

        # Replace standard engine with CoreV33Engine
        self.engine = CoreV33Engine(
            initial_capital=initial_capital,
            instrument_rules=self.instrument_rules,
            max_leverage=MAX_LEVERAGE_CAP,
            atr_stop_mult=ATR_STOP_MULT,
            taker_fee=BASE_TAKER_FEE,
            slippage=BASE_SLIPPAGE,
            portfolio_leverage=PORTFOLIO_LEVERAGE,
            drawdown_limit_pct=DRAWDOWN_LIMIT_PCT,
            min_adx=float(MIN_ADX_TREND),
            cooldown_bars=REENTRY_COOLDOWN_BARS,
            enable_btc_delta_filter=ENABLE_BTC_DELTA_FILTER,
            enable_fast_scratch=ENABLE_FAST_SCRATCH_EXIT,
            fast_scratch_bars=FAST_SCRATCH_BARS,
            fast_scratch_mfe_r=FAST_SCRATCH_MFE_R,
            enable_cascade_tp=ENABLE_CASCADE_TP,
            bar_interval_ms=BAR_INTERVAL_MS,
        )

        # Re-load persistent state into new engine
        self._load_persistent_state()

    def get_status_dict(self) -> Dict[str, Any]:
        status = super().get_status_dict()
        streak_info = self.engine.streak_manager.to_dict()

        # Augment telemetry with Streak Risk Manager data
        status["version"] = VERSION_NAME
        status["timeframe"] = "1h"
        status["gearbox_gear"] = streak_info["current_gear"]
        status["gearbox_risk_pct"] = streak_info["risk_pct"]
        status["gear_name"] = streak_info["gear_name"]
        status["rr_target"] = streak_info["rr_label"]
        status["streak_wins"] = streak_info["streak_wins"]
        status["streak_losses"] = streak_info["streak_losses"]
        status["gearbox_pyramiding_allowed"] = streak_info["pyramiding_allowed"]
        status["pyramiding_ratio"] = streak_info["pyramid_ratio"]
        status["is_circuit_breaker_active"] = streak_info["is_circuit_breaker_active"]

        return status
