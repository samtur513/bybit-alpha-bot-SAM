"""
v3.9 Bybit NewDragon Scalp Lab Configuration (Port 8092).
Extends HFT microstructure scalping with NewDragon v4.0 Universal enhancements:
1. Micro-Router: Strict Bybit minNotional enforcement ($5 alts / $100 BTC) preventing risk blowup.
2. Z_OFI Order Flow Imbalance Fast Scratch: Immediate exit if institutional flow flips against position within 10s.
3. Inside-Spread Maker Priority & 2-Stage Scalp Exit (Stage 1 Maker TP 50% at +0.50%, Stage 2 Runner at +1.20%).
"""

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

PORT = int(os.getenv("PORT_V39", "8092"))
VERSION_NAME = "v3.9 NewDragon Scalp Lab"
VERSION = "3.9.0"

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V39.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V39.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V39.md"


@dataclass(frozen=True)
class ScalperConfigV39:
    # 1. Environment & Keys
    api_key: str = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    use_mnt_discount: bool = os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true"

    # 2. Commission Model
    @property
    def fee_maker(self) -> float:
        return 0.000324 if self.use_mnt_discount else 0.000360

    @property
    def fee_taker(self) -> float:
        return 0.000900 if self.use_mnt_discount else 0.001000

    @property
    def roundtrip_mm(self) -> float:
        return 2.0 * self.fee_maker

    @property
    def roundtrip_mt(self) -> float:
        return self.fee_maker + self.fee_taker

    @property
    def roundtrip_tt(self) -> float:
        return 2.0 * self.fee_taker

    # 3. Capital & Risk Limits
    equity_usdt: float = float(os.getenv("EQUITY_USDT", "300.0"))
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "0.0050"))  # 0.50% True 1R ($1.50)
    max_daily_dd_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.020"))  # 2.0% ($6.00)
    max_leverage_cap: float = 2.0
    max_book_depth_pct: float = 0.20
    max_concurrent_positions: int = 2  # Жесткий лимит: не более 2 позиций одновременно (защита от кластерного риска)
    max_positions_per_side: int = 2   # Не более 2 позиций в одну сторону
    max_loss_usdt_cap: float = 1.50  # Жесткий долларовый потолок убытка: не более $1.50 на сделку
    dynamic_breakeven_pct: float = 0.0040  # При +0.40% профита стоп подтягивается в безубыток (+0.05%)
    max_single_trade_notional: float = 100.0  # Жесткий потолок нотионала на 1 сделку ($100), защита от проскальзывания при гэпе
    enable_e3_leadlag: bool = True   # Специализация v3.9: Флагманский Lead-Lag Beta-Catchup
    enable_e2_sweeps: bool = False  # Отключен: Sweeps полностью отданы v3.7, исключая конфликт
    enable_e1_obi: bool = False     # Строго отключен: OBI стакана полностью отдан v3.7, исключая конфликт

    # 4. NewDragon v4 Micro-Router & OFI Scratch
    enforce_min_notional: bool = True
    min_notional_alts: float = 5.0
    min_notional_btc: float = 100.0
    enable_z_ofi_scratch: bool = True
    z_ofi_scratch_threshold: float = 2.0  # Adverse Z_OFI > 2.0 (Short) or < -2.0 (Long)
    z_ofi_window_sec: float = 10.0        # Evaluate within first 10s of fill

    # 5. Bybit Endpoints
    ws_public_url: str = "wss://stream.bybit.com/v5/public/linear"
    ws_private_url: str = "wss://stream.bybit.com/v5/private"
    ws_trade_url: str = "wss://stream.bybit.com/v5/trade"
    rest_base_url: str = "https://api.bybit.com"

    # 6. Universe
    leader_symbol: str = "BTCUSDT"
    max_active_symbols: int = 10

    # 7. Alpha Engine Thresholds
    e1_obi_threshold: float = 0.45
    e1_tfi_threshold: float = 0.40
    e1_volume_surge_mult: float = 1.8
    e1_depletion_threshold: float = 0.25
    e1_min_natr_5m: float = 0.0020
    e1_max_spread_ticks: float = 3.0
    e1_max_hold_sec: float = 900.0

    e2_min_depth_pct: float = 0.0012
    e2_max_depth_atr_mult: float = 0.85
    e2_k_abs_threshold: float = 1.5
    e2_max_sweep_time_sec: float = 90.0
    e2_max_risk_pct: float = 0.0060
    e2_min_risk_pct: float = 0.0008
    e2_max_hold_sec: float = 900.0

    e3_btc_impulse_ret: float = 0.0015
    e3_btc_tfi_threshold: float = 0.50
    e3_alt_reaction_cap: float = 0.40
    e3_min_lag_spread: float = 0.0035
    e3_min_beta: float = 1.20
    e3_min_corr: float = 0.60
    e3_max_spread_ticks: float = 2.5
    e3_max_hold_sec: float = 600.0
    e3_leader_reversal_threshold: float = 0.85
