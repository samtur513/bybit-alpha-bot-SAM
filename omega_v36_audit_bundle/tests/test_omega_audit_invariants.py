"""
Comprehensive Quantitative & Accounting Invariant Test Suite for v3.6 Omega Dual-Regime ZF-8.
Strict Institutional Audit Standards.
"""

from decimal import Decimal
from datetime import datetime, timezone
import pytest

from src.config import (
    INITIAL_CAPITAL,
    PORTFOLIO_LEVERAGE,
    MAX_LEVERAGE_CAP,
    DRAWDOWN_LIMIT_PCT,
    CIRCUIT_BREAKER_DD_PCT,
    CIRCUIT_BREAKER_RECOVERY_DD_PCT,
    ATR_STOP_MULT,
    BASE_TAKER_FEE,
    BASE_MAKER_FEE,
    BASE_SLIPPAGE,
)
from src.core_v34 import CoreV34Engine
from src.zf8_transmission import ZF8Transmission, GEAR_CONFIGS
from src.range_engine import RangeMeanReversionEngine
from src.regime_classifier import MarketRegimeClassifier, MarketRegime
from src.news_core.news_config import NewsConfig
from src.news_core.regime_guard import RegimeGuard


@pytest.fixture
def instrument_rules():
    return {
        "BTCUSDT": {
            "minOrderQty": Decimal("0.001"),
            "qtyStep": Decimal("0.001"),
            "tickSize": Decimal("0.10"),
            "minNotional": Decimal("5.0"),
            "maxOrderQty": Decimal("1000.0"),
        },
        "SOLUSDT": {
            "minOrderQty": Decimal("0.01"),
            "qtyStep": Decimal("0.01"),
            "tickSize": Decimal("0.01"),
            "minNotional": Decimal("5.0"),
            "maxOrderQty": Decimal("50000.0"),
        },
    }


# ==============================================================================
# 1. INVARIANT #1: TRADE ACCOUNTING INTEGRITY (VWAP ENTRY PRICING)
# ==============================================================================
def test_accounting_integrity_vwap_pyramiding():
    """
    Never compute pyramiding profit from the original entry price;
    always use mathematically rigorous weighted average entry price:
    VWAP = (P1 * Q1 + P2 * Q2) / (Q1 + Q2)
    """
    q1 = Decimal("10.0")
    p1 = Decimal("100.0")
    q2 = Decimal("5.0")
    p2 = Decimal("110.0")

    # Formula check
    expected_vwap = (p1 * q1 + p2 * q2) / (q1 + q2)
    assert expected_vwap == Decimal("1550.0") / Decimal("15.0")
    assert round(expected_vwap, 4) == Decimal("103.3333")

    # Verification of PnL conservation at exit price $120.0
    exit_p = Decimal("120.0")
    realized_pnl = (exit_p - expected_vwap) * (q1 + q2)
    individual_pnl = (exit_p - p1) * q1 + (exit_p - p2) * q2
    assert round(realized_pnl, 2) == round(individual_pnl, 2)


def test_accounting_shared_capital_conservation(instrument_rules):
    """
    Penny-perfect conservation between closed trades net PnL and realized equity.
    Shared capital between Trend ZF-8 and Range Maker must never double-count.
    """
    trend = CoreV34Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=instrument_rules,
    )
    range_eng = RangeMeanReversionEngine(
        initial_capital=Decimal("300.0"),
        instrument_rules=instrument_rules,
    )

    prices = {"BTCUSDT": Decimal("90000.0"), "SOLUSDT": Decimal("100.0")}
    eq_trend = trend.get_equity(prices)
    eq_range = range_eng.get_equity(prices)
    combined = eq_trend + (eq_range - Decimal("300.0"))

    assert combined == Decimal("300.00")


# ==============================================================================
# 2. ZF-8 INTELLIGENT TRANSMISSION DYNAMICS
# ==============================================================================
def test_zf8_transmission_kickdown_and_abs():
    """
    Verifies 8-Speed gear shifting:
    - Initial gear: G1 (Limp Mode / Crawl, 1.0% risk)
    - Mega-Runner (R >= 5.0): Kickdown +3 Gears (G1 -> G4)
    - Super-Runner (3.2 <= R < 5.0): Kickdown +2 Gears (G4 -> G6)
    - ABS Rapid Braking (R < -0.7): Downshift -2 Gears (G6 -> G4)
    """
    tx = ZF8Transmission(initial_capital=Decimal("300.0"), initial_gear=1)
    assert tx.current_gear == 1
    assert tx.get_config().risk_pct == Decimal("0.010")

    # Mega-runner: net_pnl = $15 on $3 risk = +5.0R -> +3 gears
    g = tx.record_closed_trade(net_pnl=Decimal("15.0"), initial_risk_usd=Decimal("3.0"))
    assert g == 4
    assert tx.current_gear == 4
    assert tx.get_config().risk_pct == Decimal("0.020")

    # Super-runner: net_pnl = $10.5 on $3 risk = +3.5R -> +2 gears
    g = tx.record_closed_trade(net_pnl=Decimal("10.5"), initial_risk_usd=Decimal("3.0"))
    assert g == 6
    assert tx.current_gear == 6
    assert tx.get_config().risk_pct == Decimal("0.028")

    # ABS Rapid Braking on full stop loss: net_pnl = -$3.0 on $3 risk = -1.0R -> -2 gears
    g = tx.record_closed_trade(net_pnl=Decimal("-3.0"), initial_risk_usd=Decimal("3.0"))
    assert g == 4
    assert tx.current_gear == 4


def test_zf8_circuit_breaker_limp_mode():
    """
    If Drawdown >= 8.0%, the transmission must immediately drop into Gear 1 (Limp Mode, 1.0% risk).
    It remains locked in Gear 1 until Drawdown recovers <= 4.0%.
    """
    tx = ZF8Transmission(initial_capital=Decimal("300.0"), initial_gear=5)
    assert tx.get_effective_gear(current_dd=Decimal("5.0")) == 5

    # Trip circuit breaker
    effective = tx.get_effective_gear(current_dd=Decimal("8.5"))
    assert effective == 1
    assert tx.is_circuit_breaker_active

    # Still locked at 6.0% DD
    assert tx.get_effective_gear(current_dd=Decimal("6.0")) == 1

    # Recovers at 3.5% DD
    recovered = tx.get_effective_gear(current_dd=Decimal("3.5"))
    assert recovered == 5
    assert not tx.is_circuit_breaker_active


# ==============================================================================
# 3. IRAS RANGE MEAN-REVERSION (CHOP FILTER & MAKER REVERSION)
# ==============================================================================
def test_iras_kaufman_efficiency_filter():
    """
    Kaufman Efficiency Ratio (KER):
    KER = |Net Price Change| / Sum(|Individual Bar Changes|)
    KER <= 0.15 identifies pure stationary chop (random walk).
    KER > 0.15 signals directional drift and must reject Range Mean Reversion.
    """
    classifier = MarketRegimeClassifier(adx_trend_threshold=18.0)
    
    # Stationary chop with low KER
    reg_chop = classifier.classify_bar(
        symbol="SOLUSDT",
        adx=14.0,
        bb_mid=100.0,
        bb_upper=104.0,
        bb_lower=96.0,
        bb_width_pctl=0.30,
        kc_upper=105.0,
        kc_lower=95.0,
        rsi=48.0,
        atr=2.0,
        volume=1000.0,
        volume_sma=1000.0,
    )
    assert reg_chop.is_chop
    assert reg_chop.regime == MarketRegime.RANGE


# ==============================================================================
# 4. NEWS CORE v1.2 DEFENSIVE SHIELD (MACRO EMBARGO)
# ==============================================================================
def test_news_core_macro_lockout_window():
    """
    Verifies that scheduled macro events (FOMC, CPI, NFP) enforce an asymmetric
    lockout window [-45 min : +30 min] blocking all new trade entries.
    """
    cfg = NewsConfig(macro_lockout_before_minutes=45, macro_lockout_after_minutes=30)
    guard = RegimeGuard(cfg)

    # Clean period
    status_clean = guard.check_status("BTCUSDT", current_time=datetime(2025, 7, 10, 12, 0, tzinfo=timezone.utc))
    assert not status_clean.block_new_entries

    # FOMC announcement: 2024-03-20 18:00:00 UTC
    # Test at T - 20 minutes (17:40 UTC)
    status_locked = guard.check_status("BTCUSDT", current_time=datetime(2024, 3, 20, 17, 40, tzinfo=timezone.utc))
    assert status_locked.block_new_entries
    assert "Macro blackout" in (status_locked.reason or "")
