"""
v3.7 Bybit Scalper Core Configuration
Поддерживает ставки Regular User со скидкой 10% MNT: Maker 0.0324%, Taker 0.0900%.
"""

import os
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ScalperConfig:
    # 1. Ключи и флаги среды
    api_key: str = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    paper_trading: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
    use_mnt_discount: bool = os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true"

    # 2. Комиссионная модель (в долях)
    # Базовые: Maker 0.0360% (0.00036), Taker 0.1000% (0.00100)
    # Со скидкой 10% MNT: Maker 0.0324% (0.000324), Taker 0.0900% (0.00090)
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

    # 3. Управление капиталом и риск-лимиты
    equity_usdt: float = float(os.getenv("EQUITY_USDT", "300.0"))
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "0.0035"))  # 0.35% на сделку ($1.05 от $300)
    max_daily_dd_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.015"))  # 1.5% дневной лимит ($4.50 от $300)
    max_leverage_cap: float = 2.5
    max_book_depth_pct: float = 0.20  # Не более 20% от топ-3 уровней стакана

    # 4. Bybit V5 Endpoints
    ws_public_url: str = "wss://stream.bybit.com/v5/public/linear"
    ws_private_url: str = "wss://stream.bybit.com/v5/private"
    ws_trade_url: str = "wss://stream.bybit.com/v5/trade"
    rest_base_url: str = "https://api.bybit.com"

    # 5. Параметры пула монет
    leader_symbol: str = "BTCUSDT"
    max_active_symbols: int = 8

    # 6. Пороги Движка 1 (OBI + Flow Toxicity Breakout)
    e1_obi_threshold: float = 0.52
    e1_tfi_threshold: float = 0.65
    e1_volume_surge_mult: float = 4.5
    e1_depletion_threshold: float = 0.45
    e1_min_natr_5m: float = 0.0045
    e1_max_spread_ticks: float = 2.5
    e1_max_hold_sec: float = 40.0

    # 7. Пороги Движка 2 (Liquidity Sweep & Absorption Reversion)
    e2_min_depth_pct: float = 0.0018
    e2_max_depth_atr_mult: float = 0.75
    e2_k_abs_threshold: float = 3.2
    e2_max_sweep_time_sec: float = 90.0
    e2_max_risk_pct: float = 0.0055
    e2_min_risk_pct: float = 0.0010
    e2_max_hold_sec: float = 180.0

    # 8. Пороги Движка 3 (Lead-Lag Beta-Catchup)
    e3_btc_impulse_ret: float = 0.0022
    e3_btc_tfi_threshold: float = 0.60
    e3_alt_reaction_cap: float = 0.30
    e3_min_lag_spread: float = 0.0042
    e3_min_beta: float = 1.45
    e3_min_corr: float = 0.78
    e3_max_spread_ticks: float = 2.0
    e3_max_hold_sec: float = 18.0
    e3_leader_reversal_threshold: float = 0.45
