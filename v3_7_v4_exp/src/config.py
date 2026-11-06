"""
Configuration for v3.7-v4-exp: NewDragon Scalp Lab (Port 8093).
Combines M1/15m Scalper OMS with NewDragon v4 Phase 1:
- UniversalRisk minNotional Guard ($5 alts / $100 BTC) preventing risk inflation
- 2-Stage Z_OFI Order Flow Imbalance Fast Scratch (15s Window)
- Dynamic Breakeven Ratchet (+0.35% -> BE+0.05%)
- Post-Only Maker limit execution with inside-spread priority
- Multi-Window Order Flow & Microstructure analysis
"""

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path

PORT = int(os.getenv("PORT_V37_V4_EXP", "8093"))
VERSION_NAME = "v3.7-v4 NewDragon Scalp Lab"
VERSION = "4.0.0-exp2"

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V37_V4.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V37_V4.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V37_V4.md"


@dataclass(frozen=True)
class ScalperConfigV37V4:
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
    max_concurrent_positions: int = 2
    max_positions_per_side: int = 2
    max_loss_usdt_cap: float = 1.50  # Hard dollar loss limit: max $1.50 per trade
    dynamic_breakeven_pct: float = 0.0035  # At +0.35% profit, ratchet stop to BE+0.05%
    max_single_trade_notional: float = 100.0  # Max single trade notional cap ($100)

    # 4. Alpha Engines Configuration
    enable_e1_obi: bool = True        # Order Flow Imbalance (Z_OFI & OBI Breakout)
    enable_e2_sweeps: bool = True     # Liquidity sweep reclaim
    enable_e3_leadlag: bool = True    # Lead-lag beta-catchup to BTC

    # 5. NewDragon v4 UniversalRisk minNotional & Z_OFI Scratch
    enforce_min_notional: bool = True
    min_notional_alts: float = 5.0
    min_notional_btc: float = 100.0
    enable_z_ofi_scratch: bool = True
    z_ofi_scratch_threshold: float = 1.5  # Trigger scratch when adverse flow exceeds 1.5 sigma
    z_ofi_critical_threshold: float = 2.5
    z_ofi_window_sec: float = 15.0       # Evaluate during first 15s of trade
    min_ker_threshold: float = 0.30      # KER chop filter
    z_ofi_entry_threshold: float = 2.0   # Trigger entry on >= +2.0 sigma (Buy) or <= -2.0 sigma (Sell)

    # 6. Bybit Endpoints
    ws_public_url: str = "wss://stream.bybit.com/v5/public/linear"
    ws_private_url: str = "wss://stream.bybit.com/v5/private"
    ws_trade_url: str = "wss://stream.bybit.com/v5/trade"
    rest_base_url: str = "https://api.bybit.com"

    # 7. Universe
    leader_symbol: str = "BTCUSDT"
    max_active_symbols: int = 12

    # 8. Engine Parameters
    # E1 Breakout (optional)
    e1_depletion_threshold: float = 0.35
    e1_max_hold_sec: float = 900.0
    e1_max_spread_ticks: float = 2.5
    e1_min_er_15m: float = 0.30
    e1_min_natr_5m: float = 0.0035
    e1_obi_threshold: float = 0.60
    e1_tfi_threshold: float = 0.55
    e1_volume_surge_mult: float = 2.0

    # E2 Liquidity Sweeps
    e2_min_depth_pct: float = 0.0012
    e2_max_depth_atr_mult: float = 0.85
    e2_k_abs_threshold: float = 1.6
    e2_max_er_compression: float = 0.55
    e2_max_risk_pct: float = 0.0060
    e2_min_risk_pct: float = 0.0008
    e2_max_sweep_time_sec: float = 90.0
    e2_reclaim_ticks: float = 2.0
    e2_tp1_ratio: float = 0.50
    e2_tp1_atr_mult: float = 0.60
    e2_tp2_atr_mult: float = 1.20
    e2_max_hold_sec: float = 900.0

    # E3 Lead-Lag Beta Catchup
    e3_alt_reaction_cap: float = 0.40
    e3_btc_impulse_ret: float = 0.0009
    e3_btc_tfi_threshold: float = 0.40
    e3_leader_delta_threshold: float = 0.0035
    e3_alt_lag_ratio: float = 0.40
    e3_min_beta: float = 0.80
    e3_min_corr: float = 0.60
    e3_max_spread_ticks: float = 3.0
    e3_min_lag_spread: float = 0.0018
    e3_tp1_atr_mult: float = 0.50
    e3_tp2_atr_mult: float = 1.20
    e3_max_hold_sec: float = 600.0
    e3_leader_reversal_threshold: float = 0.50
