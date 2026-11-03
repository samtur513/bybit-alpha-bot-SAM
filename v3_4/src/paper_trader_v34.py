"""
Paper Trading Daemon v3.4 ZF-8 Quant (Port 8087).
Operates on 1h candles with Dynamic Universe Screener, News Core v1.2,
and 8-Speed Intelligent ZF Quant Transmission (Smart Kickdown & ABS Rapid Braking).
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
from v3_4.src.config import (
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
    PORT,
    VERSION_NAME,
    INITIAL_CAPITAL,
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
from v3.src.core_v3 import PortfolioSnapshot
from v3.src.persistence import atomic_json
from v3_4.src.core_v34 import CoreV34Engine
from v3_4.src.zf8_transmission import GEAR_CONFIGS

logger = logging.getLogger("PaperTraderV34")


class PaperTraderV34(PaperTraderV3):
    """
    Paper trader daemon running v3.4 ZF-8 Quant on Port 8087.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = INITIAL_CAPITAL,
        state_file: Path = PERSISTENT_STATE_FILE,
        status_file: Path = LIVE_STATUS_FILE,
        md_file: Path = LIVE_MD_FILE,
        enable_news_defense: bool = True,
        client: Optional[Any] = None,
        poll_interval_seconds: int = 5,
    ):
        base_g1_risk = GEAR_CONFIGS[1].risk_pct
        super().__init__(
            symbols=symbols or list(DEFAULT_SYMBOLS),
            initial_capital=initial_capital,
            base_risk_pct=base_g1_risk,
            max_leverage=MAX_LEVERAGE_CAP,
            poll_interval_seconds=poll_interval_seconds,
            client=client,
            state_file=state_file,
            live_status_file=status_file,
            live_md_file=md_file,
            enable_news_defense=enable_news_defense,
        )

        # Replace standard engine with CoreV34Engine
        self.engine = CoreV34Engine(
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

    def _export_status(
        self, snapshot: PortfolioSnapshot, current_prices: Dict[str, Decimal], server_ts: int
    ) -> None:
        super()._export_status(snapshot, current_prices, server_ts)
        trans_info = self.engine.transmission.to_dict()

        if self.last_status_cache:
            self.last_status_cache.update({
                "version": VERSION_NAME,
                "timeframe": "1h",
                "gearbox_gear": trans_info["current_gear"],
                "gearbox_risk_pct": trans_info["risk_pct"],
                "gear_name": trans_info["gear_name"],
                "gear_tag": trans_info["gear_tag"],
                "rr_target": trans_info["rr_label"],
                "streak_wins": trans_info["streak_wins"],
                "streak_losses": trans_info["streak_losses"],
                "gearbox_pyramiding_allowed": trans_info["pyramiding_allowed"],
                "pyramiding_ratio": trans_info["pyramid_ratio"],
                "is_circuit_breaker_active": trans_info["is_circuit_breaker_active"],
                "last_shift_reason": trans_info["last_shift_reason"],
                "all_gears": trans_info["all_gears"],
            })
            try:
                atomic_json(self.live_status_file, self.last_status_cache)
            except Exception:
                pass
