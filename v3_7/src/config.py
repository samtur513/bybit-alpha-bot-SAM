"""
v3.7 Bybit Scalper Core Configuration
Поддерживает ставки Regular User со скидкой 10% MNT: Maker 0.0324%, Taker 0.0900%.
"""

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

# Paths & Port Configuration
PORT = int(os.getenv("PORT", "8090"))
VERSION_NAME = "v3.7 Bybit Scalper (15m/M1)"
BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V37.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V37.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V37.md"


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
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "0.0050"))  # 0.50% на сделку ($1.50 от $300)
    max_daily_dd_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.020"))  # 2.0% дневной лимит ($6.00 от $300)
    max_leverage_cap: float = 2.0
    max_single_trade_notional: float = 100.0  # Жесткий потолок нотионала на 1 сделку ($100), защита от проскальзывания при гэпе
    max_book_depth_pct: float = 0.20  # Не более 20% от топ-3 уровней стакана
    max_concurrent_positions: int = 2  # Жесткий лимит: не более 2 позиций одновременно (защита от кластерного риска)
    max_positions_per_side: int = 2   # Не более 2 позиций в одну сторону
    max_loss_usdt_cap: float = 1.50  # Жесткий долларовый потолок убытка: не более $1.50 на сделку
    dynamic_breakeven_pct: float = 0.0045  # При +0.45% профита стоп подтягивается в безубыток (+0.05%)
    enable_e3_leadlag: bool = False  # Отключен: Lead-Lag полностью отдан v3.9, исключая дублирование
    enable_e2_sweeps: bool = True   # Специализация v3.7: Снятие ликвидности и абсорбция
    enable_e1_obi: bool = True      # Специализация v3.7: Дисбаланс L2 стакана (строго при KER >= 0.30)

    # 4. Bybit V5 Endpoints
    ws_public_url: str = "wss://stream.bybit.com/v5/public/linear"
    ws_private_url: str = "wss://stream.bybit.com/v5/private"
    ws_trade_url: str = "wss://stream.bybit.com/v5/trade"
    rest_base_url: str = "https://api.bybit.com"

    # 5. Параметры пула монет
    leader_symbol: str = "BTCUSDT"
    max_active_symbols: int = 10

    # 6. Пороги Движка 1 (OBI + Flow Toxicity Breakout - Институциональный фильтр)
    e1_obi_threshold: float = 0.60          # Повышен с 0.45: глубокий дисбаланс стакана L2
    e1_tfi_threshold: float = 0.55          # Повышен с 0.45: направленный поток агрессивных покупок/продаж
    e1_volume_surge_mult: float = 2.0
    e1_depletion_threshold: float = 0.35    # Повышен с 0.25: реальное разъедание лимитных плотностей >= 35%
    e1_min_natr_5m: float = 0.0035         # Повышен с 0.0020: отсечение низковолатильного мертвого флэта
    e1_min_er_15m: float = 0.30            # Коэффициент Кауфмана >= 0.30: 100% блокировка входов в боковом пиле
    e1_max_spread_ticks: float = 2.5
    e1_max_hold_sec: float = 900.0  # 15 минут

    # 7. Пороги Движка 2 (Liquidity Sweep & Absorption Reversion)
    e2_min_depth_pct: float = 0.0012
    e2_max_depth_atr_mult: float = 0.85
    e2_k_abs_threshold: float = 1.6
    e2_max_sweep_time_sec: float = 90.0
    e2_max_risk_pct: float = 0.0060
    e2_min_risk_pct: float = 0.0008
    e2_max_hold_sec: float = 900.0  # 15 минут

    # 8. Пороги Движка 3 (Lead-Lag Beta-Catchup - Флагманский арбитраж)
    e3_btc_impulse_ret: float = 0.0009
    e3_btc_tfi_threshold: float = 0.40
    e3_alt_reaction_cap: float = 0.40
    e3_min_lag_spread: float = 0.0018
    e3_min_beta: float = 1.15
    e3_min_corr: float = 0.60
    e3_max_spread_ticks: float = 3.0
    e3_max_hold_sec: float = 1200.0  # 20 минут вместо 18 секунд
    e3_leader_reversal_threshold: float = 0.55
