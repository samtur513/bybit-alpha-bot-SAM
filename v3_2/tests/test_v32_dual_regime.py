"""
Unit Tests for Bybit Multi-Alpha v3.2 Dual-Regime Hybrid Engine.
Verifies:
1. Indicator computation (Trend Donchian + Regime Bollinger/Keltner/ADX/RSI)
2. Regime Classifier behavior
3. Range Engine Maker limit order generation and fill execution
4. Regime Handoff from Range to Trend
5. State persistence and accounting invariants
"""

from decimal import Decimal
import numpy as np
import pandas as pd
import pytest

from v3_2.src.regime_classifier import MarketRegimeClassifier, MarketRegime, RegimeData
from v3_2.src.range_engine import RangeMeanReversionEngine, RangeActivePosition, RangeLimitOrder
from v3_2.src.paper_trader_v32 import prepare_dual_regime_indicators


def test_dual_regime_indicators_computation():
    n = 150
    df = pd.DataFrame({
        "timestamp": range(1000, 1000 + n),
        "open": np.random.uniform(100, 110, n),
        "high": np.random.uniform(110, 120, n),
        "low": np.random.uniform(90, 100, n),
        "close": np.random.uniform(95, 115, n),
        "volume": np.random.uniform(1000, 5000, n),
    })
    clf = MarketRegimeClassifier()
    res = prepare_dual_regime_indicators(df, clf, lookback=30)

    for col in ["upper", "lower", "ema_fast", "ema_slow", "atr", "rsi", "adx", "bb_mid", "bb_upper", "bb_lower", "kc_upper", "kc_lower"]:
        assert col in res.columns
        assert not res[col].iloc[-10:].isna().all()


def test_regime_classifier_chop_vs_trend():
    clf = MarketRegimeClassifier(adx_trend_threshold=20.0, bbw_compression_percentile=40.0)

    # 1. Chop / Range case (ADX 14 < 20, BB width percentile 0.30)
    chop_reg = clf.classify_bar(
        symbol="BTCUSDT",
        adx=14.0,
        bb_mid=100.0,
        bb_upper=104.0,
        bb_lower=96.0,
        bb_width_pctl=0.30,
        kc_upper=103.0,
        kc_lower=97.0,
        rsi=45.0,
        atr=2.0,
        volume=1000.0,
        volume_sma=1200.0,
    )
    assert chop_reg.regime == MarketRegime.RANGE
    assert chop_reg.allows_range_trading is True
    assert chop_reg.allows_trend_trading is False

    # 2. Trend case (ADX 28 > 20, Volume > Volume SMA)
    trend_reg = clf.classify_bar(
        symbol="BTCUSDT",
        adx=28.0,
        bb_mid=100.0,
        bb_upper=112.0,
        bb_lower=88.0,
        bb_width_pctl=0.85,
        kc_upper=108.0,
        kc_lower=92.0,
        rsi=68.0,
        atr=3.5,
        volume=2500.0,
        volume_sma=1200.0,
    )
    assert trend_reg.regime == MarketRegime.TREND
    assert trend_reg.allows_range_trading is False
    assert trend_reg.allows_trend_trading is True


def test_range_maker_order_generation():
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    bars = {
        "ETHUSDT": {
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 96.0,
            "volume": 1000.0,
            "volume_sma": 1200.0,
            "atr": 2.0,
            "bb_mid": 100.0,
            "bb_lower": 95.5,
            "bb_upper": 104.5,
        }
    }
    regimes = {
        "ETHUSDT": RegimeData(
            symbol="ETHUSDT",
            regime=MarketRegime.RANGE,
            adx=14.0,
            bb_mid=100.0,
            bb_upper=104.5,
            bb_lower=95.5,
            bb_width=0.09,
            bb_width_pctl=0.30,
            kc_upper=103.0,
            kc_lower=97.0,
            rsi=28.0,  # Oversold RSI (< 35) triggers LONG Maker Buy
            atr=2.0,
            volume=1000.0,
            volume_sma=1200.0,
            is_volatility_compressed=True,
            is_chop=True,
            is_trend=False,
        )
    }

    orders = engine.scan_and_generate_orders(
        timestamp=1700000000000,
        bars=bars,
        regimes=regimes,
        btc_bearish=False,
    )

    assert len(orders) == 1
    ord0 = orders[0]
    assert ord0.symbol == "ETHUSDT"
    assert ord0.direction == 1
    assert ord0.price == Decimal("95.5")
    assert ord0.stop_loss == Decimal("93.50")
    assert ord0.take_profit > ord0.price


def test_range_maker_order_fill_and_tp():
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.pending_orders["ETHUSDT"] = RangeLimitOrder(
        symbol="ETHUSDT",
        direction=1,
        price=Decimal("95.5"),
        qty=Decimal("1.0"),
        stop_loss=Decimal("93.5"),
        take_profit=Decimal("97.5"),
        created_time=1700000000000,
        created_bar_idx=0,
        atr=Decimal("2.0"),
    )

    # Next bar penetrates below limit price: open 96.0, low 94.0 -> Fills!
    next_bars = {
        "ETHUSDT": {
            "open": 96.0,
            "high": 98.0,
            "low": 94.0,
            "close": 97.0,
        }
    }
    next_open = {"ETHUSDT": Decimal("96.0")}

    filled = engine.evaluate_pending_fills(next_bars, next_open)
    assert "ETHUSDT" in filled
    assert "ETHUSDT" in engine.open_positions
    pos = engine.open_positions["ETHUSDT"]
    assert pos.entry_price == Decimal("95.5")

    # Increment bar counter so position is eligible for management
    engine.bar_counter = 1

    # Following bar reaches take profit (High >= 97.5)
    bars_tp = {
        "ETHUSDT": {
            "open": 97.0,
            "high": 98.5,
            "low": 96.5,
            "close": 98.0,
            "volume": 1000.0,
            "volume_sma": 1000.0,
        }
    }
    regimes = {
        "ETHUSDT": RegimeData(
            symbol="ETHUSDT",
            regime=MarketRegime.RANGE,
            adx=15.0,
            bb_mid=97.5,
            bb_upper=102.0,
            bb_lower=94.0,
            bb_width=0.08,
            bb_width_pctl=0.35,
            kc_upper=100.0,
            kc_lower=95.0,
            rsi=55.0,
            atr=2.0,
            volume=1000.0,
            volume_sma=1000.0,
            is_volatility_compressed=True,
            is_chop=True,
            is_trend=False,
        )
    }

    closed = engine.manage_positions_on_bar(1700003600000, bars_tp, regimes)
    assert len(closed) == 1
    assert closed[0].exit_reason == "TP"
    assert closed[0].net_pnl > 0
    assert "ETHUSDT" not in engine.open_positions


def test_range_regime_handoff():
    """Verifies that an open Range position closes immediately when ADX surges >= 20 on breakout volume against position."""
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.open_positions["SOLUSDT"] = RangeActivePosition(
        symbol="SOLUSDT",
        direction=1,
        entry_price=Decimal("150.0"),
        current_qty=Decimal("1.0"),
        stop_loss=Decimal("140.0"),  # Wide stop so handoff triggers before SL
        take_profit=Decimal("158.0"),
        initial_atr=Decimal("5.0"),
        entry_time=1700000000000,
        entry_bar_idx=0,
        entry_fees=Decimal("0.05"),
    )
    engine.bar_counter = 1

    # Bar with surging bear momentum breaking below bb_lower: ADX = 26.0, Volume ratio = 2.5x, close < bb_lower
    bars_breakout = {
        "SOLUSDT": {
            "open": 146.0,
            "high": 147.0,
            "low": 143.0,
            "close": 143.5,
            "volume": 2500.0,
            "volume_sma": 1000.0,
        }
    }
    regimes_breakout = {
        "SOLUSDT": RegimeData(
            symbol="SOLUSDT",
            regime=MarketRegime.TREND,
            adx=26.0,
            bb_mid=150.0,
            bb_upper=155.0,
            bb_lower=145.0,
            bb_width=0.06,
            bb_width_pctl=0.60,
            kc_upper=154.0,
            kc_lower=146.0,
            rsi=32.0,
            atr=5.0,
            volume=2500.0,
            volume_sma=1000.0,
            is_volatility_compressed=False,
            is_chop=False,
            is_trend=True,
        )
    }

    closed = engine.manage_positions_on_bar(1700003600000, bars_breakout, regimes_breakout)
    assert len(closed) == 1
    assert closed[0].exit_reason == "REGIME_HANDOFF"
    assert "SOLUSDT" not in engine.open_positions


def test_range_engine_persistence_roundtrip():
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.open_positions["BTCUSDT"] = RangeActivePosition(
        symbol="BTCUSDT",
        direction=1,
        entry_price=Decimal("65000.0"),
        current_qty=Decimal("0.01"),
        stop_loss=Decimal("64000.0"),
        take_profit=Decimal("66000.0"),
        initial_atr=Decimal("1000.0"),
        entry_time=1700000000000,
        entry_bar_idx=10,
        entry_fees=Decimal("0.21"),
    )
    engine.pending_orders["SOLUSDT"] = RangeLimitOrder(
        symbol="SOLUSDT",
        direction=-1,
        price=Decimal("160.0"),
        qty=Decimal("2.0"),
        stop_loss=Decimal("165.0"),
        take_profit=Decimal("155.0"),
        created_time=1700000000000,
        created_bar_idx=10,
        atr=Decimal("5.0"),
    )

    data = engine.to_dict()
    assert data["initial_capital"] == "300.0"
    assert "BTCUSDT" in data["open_positions"]
    assert "SOLUSDT" in data["pending_orders"]

    restored = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    restored.load_dict(data)

    assert "BTCUSDT" in restored.open_positions
    assert restored.open_positions["BTCUSDT"].entry_price == Decimal("65000.0")
    assert "SOLUSDT" in restored.pending_orders
    assert restored.pending_orders["SOLUSDT"].price == Decimal("160.0")


def test_v32_news_defense_integration():
    from v3_2.src.paper_trader_v32 import PaperTraderV32
    from datetime import datetime, timezone

    pt = PaperTraderV32(
        symbols=["BTCUSDT", "ETHUSDT"],
        initial_capital=Decimal("300.0"),
        enable_news_defense=True,
    )
    assert pt.enable_news_defense is True
    assert pt.regime_guard is not None

    # Normal time: no macro blackout
    normal_dt = datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc)
    is_embargo, reason = pt._check_macro_embargo(normal_dt)
    assert not is_embargo

    # Macro FOMC time: 2024-03-20 18:00 UTC -> Embargo active!
    fomc_dt = datetime(2024, 3, 20, 18, 0, tzinfo=timezone.utc)
    is_embargo, reason = pt._check_macro_embargo(fomc_dt)
    assert is_embargo
    assert "FOMC" in reason or "Macro" in reason

    # Test status export
    status = pt.poll_cycle()
    assert status["news_defense_active"] is True
    assert status["news_shield"] is True
    assert "open_positions" in status
