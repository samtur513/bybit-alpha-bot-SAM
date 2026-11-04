"""
Unit Tests and Accounting Invariant Verification for v3.6 Omega Dual-Regime ZF-8.
"""

from decimal import Decimal
import pytest
from datetime import datetime, timezone

from v3_6.src.config import (
    INITIAL_CAPITAL,
    PORTFOLIO_LEVERAGE,
    MAX_LEVERAGE_CAP,
    DRAWDOWN_LIMIT_PCT,
    ATR_STOP_MULT,
    FAST_SCRATCH_MFE_R,
    FAST_SCRATCH_BARS,
    BASE_TAKER_FEE,
    BASE_MAKER_FEE,
    BASE_SLIPPAGE,
)
from v3_4.src.core_v34 import CoreV34Engine
from v3_2.src.range_engine import RangeMeanReversionEngine
from v3_2.src.regime_classifier import MarketRegimeClassifier, MarketRegime
from v3.src.news_core.news_config import NewsConfig
from v3.src.news_core.regime_guard import RegimeGuard


@pytest.fixture
def sample_instrument_rules():
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


def test_v36_omega_initialization(sample_instrument_rules):
    trend_engine = CoreV34Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=sample_instrument_rules,
        max_leverage=MAX_LEVERAGE_CAP,
        atr_stop_mult=ATR_STOP_MULT,
        taker_fee=BASE_TAKER_FEE,
        slippage=BASE_SLIPPAGE,
        portfolio_leverage=PORTFOLIO_LEVERAGE,
        drawdown_limit_pct=DRAWDOWN_LIMIT_PCT,
    )
    range_engine = RangeMeanReversionEngine(
        initial_capital=Decimal("300.0"),
        instrument_rules=sample_instrument_rules,
        maker_fee=BASE_MAKER_FEE,
        taker_fee=BASE_TAKER_FEE,
        slippage=BASE_SLIPPAGE,
    )

    assert trend_engine.realized_equity == Decimal("300.0")
    assert range_engine.realized_equity == Decimal("300.0")
    assert trend_engine.transmission.current_gear == 1
    assert "LIMP" in trend_engine.transmission.get_config().name


def test_v36_omega_transmission_shift(sample_instrument_rules):
    trend_engine = CoreV34Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=sample_instrument_rules,
    )
    assert trend_engine.transmission.current_gear == 1

    # Simulate 2 consecutive wins (R = 15/3 = 5.0 -> Mega Kickdown +3 gears)
    new_gear = trend_engine.transmission.record_closed_trade(
        net_pnl=Decimal("15.0"),
        initial_risk_usd=Decimal("3.0"),
        current_dd=Decimal("0.0")
    )
    assert new_gear == 4
    assert trend_engine.transmission.current_gear == 4


def test_v36_omega_accounting_invariant(sample_instrument_rules):
    trend_engine = CoreV34Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=sample_instrument_rules,
    )
    range_engine = RangeMeanReversionEngine(
        initial_capital=Decimal("300.0"),
        instrument_rules=sample_instrument_rules,
    )

    curr_prices = {"BTCUSDT": Decimal("90000.0"), "SOLUSDT": Decimal("100.0")}
    trend_eq = trend_engine.get_equity(curr_prices)
    range_eq = range_engine.get_equity(curr_prices)
    combined_eq = trend_eq + (range_eq - Decimal("300.0"))

    assert combined_eq == Decimal("300.0")


def test_v36_omega_macro_embargo_gate():
    cfg = NewsConfig(macro_lockout_before_minutes=45, macro_lockout_after_minutes=30)
    guard = RegimeGuard(cfg)
    
    # Check outside macro event
    normal_dt = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    status_normal = guard.check_status("BTCUSDT", current_time=normal_dt)
    assert not status_normal.block_new_entries

    # Check right at FOMC 2024-03-20 18:00 UTC
    fomc_dt = datetime(2024, 3, 20, 18, 0, tzinfo=timezone.utc)
    status_fomc = guard.check_status("BTCUSDT", current_time=fomc_dt)
    assert status_fomc.block_new_entries
    assert "FOMC" in (status_fomc.reason or "") or "Macro" in (status_fomc.reason or "")


def test_v36_omega_regime_classification_iras():
    classifier = MarketRegimeClassifier(adx_trend_threshold=18.0)
    
    # Chop bar with low KER
    reg = classifier.classify_bar(
        symbol="SOLUSDT",
        adx=15.0,
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
    assert reg.is_chop
    assert reg.regime == MarketRegime.RANGE


def test_v36_paper_trader_export_status_with_active_and_closed_positions(sample_instrument_rules):
    from v3_6.src.paper_trader_v36 import PaperTraderV36
    from v3.src.pyramiding_engine import ActivePosition
    from v3_2.src.range_engine import RangeActivePosition, RangeClosedTrade
    from v3.src.core_v3 import ClosedTrade

    pt = PaperTraderV36(
        symbols=["BTCUSDT", "SOLUSDT"],
        initial_capital=Decimal("300.0"),
        enable_news_defense=False,
    )
    pt.instrument_rules = sample_instrument_rules

    # 1. Add active trend position
    pt.trend_engine.open_positions["BTCUSDT"] = ActivePosition(
        symbol="BTCUSDT",
        direction=1,
        initial_qty=Decimal("0.01"),
        current_qty=Decimal("0.01"),
        entry_price=Decimal("90000.0"),
        current_stop=Decimal("88000.0"),
        initial_atr=Decimal("1000.0"),
        highest_price=Decimal("91000.0"),
        lowest_price=Decimal("89500.0"),
        entry_time=1700000000000,
        is_pyramided=False,
        tp1_taken=False,
        tp2_taken=False,
    )

    # 2. Add active range position
    pt.range_engine.open_positions["SOLUSDT"] = RangeActivePosition(
        symbol="SOLUSDT",
        direction=-1,
        entry_price=Decimal("150.0"),
        current_qty=Decimal("2.0"),
        stop_loss=Decimal("155.0"),
        take_profit=Decimal("145.0"),
        initial_atr=Decimal("5.0"),
        entry_time=1700000050000,
        entry_bar_idx=10,
        entry_fees=Decimal("0.10"),
    )

    # 3. Add closed trades
    pt.trend_engine.closed_trades.append(
        ClosedTrade(
            symbol="BTCUSDT",
            direction=1,
            entry_time=1699900000000,
            exit_time=1699910000000,
            entry_price=Decimal("88000.0"),
            exit_price=Decimal("89000.0"),
            initial_qty=Decimal("0.01"),
            final_qty=Decimal("0.01"),
            net_pnl=Decimal("9.80"),
            exit_reason="TP1",
            was_pyramided=False,
            hold_hours=2.7,
        )
    )

    pt.range_engine.closed_trades.append(
        RangeClosedTrade(
            symbol="SOLUSDT",
            direction=1,
            entry_time=1699920000000,
            exit_time=1699930000000,
            entry_price=Decimal("140.0"),
            exit_price=Decimal("143.0"),
            qty=Decimal("2.0"),
            net_pnl=Decimal("5.80"),
            exit_reason="TP",
            hold_hours=1.5,
            fees_paid=Decimal("0.12"),
            funding_pnl=Decimal("0.0"),
        )
    )

    # Simulate live prices
    pt._on_price("BTCUSDT", Decimal("90500.0"))
    pt._on_price("SOLUSDT", Decimal("148.0"))

    status = pt.poll_cycle()

    assert status["total_open_positions"] == 2
    assert "BTCUSDT" in status["trend_open_positions"]
    assert status["trend_open_positions"]["BTCUSDT"]["entry_time"] == 1700000000000
    assert status["trend_open_positions"]["BTCUSDT"]["direction"] == "LONG"
    assert status["trend_open_positions"]["BTCUSDT"]["unrealized_pnl"] == 5.0

    assert "SOLUSDT" in status["range_open_positions"]
    assert status["range_open_positions"]["SOLUSDT"]["entry_time"] == 1700000050000
    assert status["range_open_positions"]["SOLUSDT"]["direction"] == "SHORT"
    assert status["range_open_positions"]["SOLUSDT"]["unrealized_pnl"] == 4.0

    assert len(status["closed_trades"]) == 2
    assert status["closed_trades"][0]["exit_time"] == 1699930000000
    assert status["closed_trades"][1]["exit_time"] == 1699910000000

