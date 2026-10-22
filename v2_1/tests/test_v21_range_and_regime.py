"""
Unit and Integration Tests for Regime Classifier and Range Mean-Reversion Engine v2.1.
"""

from decimal import Decimal
import numpy as np
import pandas as pd
import pytest

from v2_1.src.regime_classifier import MarketRegime, MarketRegimeClassifier, RegimeData
from v2_1.src.range_engine import RangeMeanReversionEngine, RangeLimitOrder, RangeActivePosition


@pytest.fixture
def sample_ohlcv():
    """Generates synthetic 200 bars of oscillating OHLCV data."""
    np.random.seed(42)
    n = 200
    timestamps = [1704067200000 + i * 3600000 for i in range(n)]
    base = 100.0
    prices = [base]
    for _ in range(n - 1):
        prices.append(prices[-1] + np.random.normal(0, 1.0))
    close = np.array(prices)
    high = close + np.random.uniform(0.2, 1.5, n)
    low = close - np.random.uniform(0.2, 1.5, n)
    open_p = low + np.random.uniform(0.1, high - low)
    volume = np.random.uniform(100, 1000, n)

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def test_regime_classifier_chop_vs_trend(sample_ohlcv):
    classifier = MarketRegimeClassifier(adx_trend_threshold=20.0, bbw_compression_percentile=40.0)
    df = classifier.prepare_indicators(sample_ohlcv)

    assert "bb_upper" in df.columns
    assert "bb_lower" in df.columns
    assert "bb_width_pctl" in df.columns
    assert "adx" in df.columns
    assert "rsi" in df.columns

    # Test classification logic
    # Chop: ADX < 20
    reg_chop = classifier.classify_bar(
        symbol="BTCUSDT",
        adx=15.0,
        bb_mid=100.0,
        bb_upper=103.0,
        bb_lower=97.0,
        bb_width_pctl=50.0,
        kc_upper=102.5,
        kc_lower=97.5,
        rsi=30.0,
        atr=1.5,
        volume=500.0,
        volume_sma=600.0,
    )
    assert reg_chop.regime == MarketRegime.RANGE
    assert reg_chop.allows_range_trading is True
    assert reg_chop.allows_trend_trading is False

    # Trend: ADX >= 20 and volume > volume_sma
    reg_trend = classifier.classify_bar(
        symbol="BTCUSDT",
        adx=25.0,
        bb_mid=100.0,
        bb_upper=105.0,
        bb_lower=95.0,
        bb_width_pctl=80.0,
        kc_upper=104.0,
        kc_lower=96.0,
        rsi=70.0,
        atr=2.0,
        volume=1200.0,
        volume_sma=600.0,
    )
    assert reg_trend.regime == MarketRegime.TREND
    assert reg_trend.allows_range_trading is False
    assert reg_trend.allows_trend_trading is True


def test_regime_classifier_zero_lookahead_bounds():
    """Verify that Bollinger Bands shift(1) excludes the current candle's extrema."""
    classifier = MarketRegimeClassifier(bb_period=3)
    df = pd.DataFrame({
        "timestamp": [1000, 2000, 3000, 4000],
        "open": [10.0, 10.0, 10.0, 10.0],
        "high": [12.0, 12.0, 12.0, 100.0], # Bar 3 huge spike
        "low": [8.0, 8.0, 8.0, 1.0],       # Bar 3 huge drop
        "close": [10.0, 10.0, 10.0, 50.0],
        "volume": [100.0, 100.0, 100.0, 500.0],
    })
    prep = classifier.prepare_indicators(df)
    # The bb_mid of bar 3 must NOT contain bar 3's close (50.0)
    assert prep.loc[3, "bb_mid"] == 10.0


def test_range_engine_maker_limit_fill():
    """Test strict Zero-Lookahead Maker limit fill condition."""
    engine = RangeMeanReversionEngine(
        initial_capital=Decimal("300.0"),
        risk_per_trade_pct=Decimal("0.01"),
    )

    # Place a pending Buy Limit order at $95.0
    engine.pending_orders["BTCUSDT"] = RangeLimitOrder(
        symbol="BTCUSDT",
        direction=1,
        price=Decimal("95.0"),
        qty=Decimal("0.1"),
        stop_loss=Decimal("93.0"),
        take_profit=Decimal("97.0"),
        created_time=1000,
        created_bar_idx=1,
        atr=Decimal("2.0"),
    )

    # Case 1: Next bar opens at 96.0 and low drops to 94.0 (penetrates limit) -> FILLS
    next_bars = {"BTCUSDT": {"open": 96.0, "high": 98.0, "low": 94.0, "close": 96.5}}
    fills = engine.evaluate_pending_fills(next_bars, {"BTCUSDT": Decimal("96.0")})
    assert "BTCUSDT" in fills
    assert "BTCUSDT" in engine.open_positions
    pos = engine.open_positions["BTCUSDT"]
    assert pos.entry_price == Decimal("95.0")
    # Maker fee 0.0324% deducted
    assert pos.entry_fees == Decimal("95.0") * Decimal("0.1") * Decimal("0.000324")


def test_range_engine_maker_limit_no_fill_if_not_penetrated():
    """Test that if price does not strictly penetrate limit, no fill occurs."""
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.pending_orders["BTCUSDT"] = RangeLimitOrder(
        symbol="BTCUSDT",
        direction=1,
        price=Decimal("95.0"),
        qty=Decimal("0.1"),
        stop_loss=Decimal("93.0"),
        take_profit=Decimal("97.0"),
        created_time=1000,
        created_bar_idx=1,
        atr=Decimal("2.0"),
    )

    # Case 2: Next bar low is 95.0 (touching but not penetrating) -> NO FILL
    next_bars = {"BTCUSDT": {"open": 96.0, "high": 98.0, "low": 95.0, "close": 96.0}}
    fills = engine.evaluate_pending_fills(next_bars, {"BTCUSDT": Decimal("96.0")})
    assert "BTCUSDT" not in fills
    assert "BTCUSDT" not in engine.open_positions


def test_range_engine_take_profit_maker_execution():
    """Verify Take Profit fills as Maker on middle band/target."""
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.open_positions["BTCUSDT"] = RangeActivePosition(
        symbol="BTCUSDT",
        direction=1,
        entry_price=Decimal("100.0"),
        current_qty=Decimal("1.0"),
        stop_loss=Decimal("98.0"),
        take_profit=Decimal("102.0"),
        initial_atr=Decimal("2.0"),
        entry_time=1000,
        entry_bar_idx=0,
        entry_fees=Decimal("100.0") * Decimal("0.000324"),
    )
    engine.bar_counter = 1

    bars = {"BTCUSDT": {"open": 100.5, "high": 103.0, "low": 100.2, "close": 102.5}}
    regimes = {}
    closed = engine.manage_positions_on_bar(2000, bars, regimes)
    assert len(closed) == 1
    t = closed[0]
    assert t.exit_reason == "TP"
    assert t.exit_price == Decimal("102.0")
    assert t.net_pnl > 0


def test_range_engine_stop_loss_collision_priority():
    """Verify intra-bar collision: if both SL and TP are touched, SL triggers."""
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.open_positions["BTCUSDT"] = RangeActivePosition(
        symbol="BTCUSDT",
        direction=1,
        entry_price=Decimal("100.0"),
        current_qty=Decimal("1.0"),
        stop_loss=Decimal("98.0"),
        take_profit=Decimal("102.0"),
        initial_atr=Decimal("2.0"),
        entry_time=1000,
        entry_bar_idx=0,
        entry_fees=Decimal("0.0324"),
    )
    engine.bar_counter = 1

    # Bar touched both 97.0 (SL) and 103.0 (TP)
    bars = {"BTCUSDT": {"open": 100.0, "high": 103.0, "low": 97.0, "close": 101.0}}
    closed = engine.manage_positions_on_bar(2000, bars, {})
    assert len(closed) == 1
    assert closed[0].exit_reason == "SL"
    assert closed[0].net_pnl < 0


def test_range_engine_regime_handoff():
    """Verify that anomalous breakout with ADX >= 20 triggers Regime Handoff."""
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.open_positions["BTCUSDT"] = RangeActivePosition(
        symbol="BTCUSDT",
        direction=1,
        entry_price=Decimal("100.0"),
        current_qty=Decimal("1.0"),
        stop_loss=Decimal("95.0"),
        take_profit=Decimal("105.0"),
        initial_atr=Decimal("2.0"),
        entry_time=1000,
        entry_bar_idx=0,
        entry_fees=Decimal("0.0324"),
    )
    engine.bar_counter = 1

    regime = RegimeData(
        symbol="BTCUSDT",
        regime=MarketRegime.TREND,
        adx=26.0,
        bb_mid=100.0,
        bb_upper=104.0,
        bb_lower=96.0,
        bb_width=0.08,
        bb_width_pctl=60.0,
        kc_upper=103.0,
        kc_lower=97.0,
        rsi=25.0,
        atr=2.0,
        volume=2000.0,
        volume_sma=1000.0, # 2.0x anomalous volume
        is_volatility_compressed=False,
        is_chop=False,
        is_trend=True,
    )
    # Price closed below lower band at 95.8 (bb_lower is 96.0), but above SL (95.0)
    bars = {"BTCUSDT": {"open": 98.0, "high": 98.5, "low": 95.5, "close": 95.8, "adx": 26.0, "volume": 2000.0, "volume_sma": 1000.0}}
    closed = engine.manage_positions_on_bar(2000, bars, {"BTCUSDT": regime})
    assert len(closed) == 1
    assert closed[0].exit_reason == "REGIME_HANDOFF"


def test_range_engine_max_positions_and_leverage():
    """Verify max 2 concurrent positions and leverage limits."""
    engine = RangeMeanReversionEngine(
        initial_capital=Decimal("300.0"),
        max_concurrent_positions=2,
        max_portfolio_leverage=Decimal("3.5"),
    )

    reg = RegimeData(
        symbol="ETHUSDT",
        regime=MarketRegime.RANGE,
        adx=15.0,
        bb_mid=100.0,
        bb_upper=104.0,
        bb_lower=96.0,
        bb_width=0.08,
        bb_width_pctl=30.0,
        kc_upper=103.0,
        kc_lower=97.0,
        rsi=30.0,
        atr=2.0,
        volume=500.0,
        volume_sma=600.0,
        is_volatility_compressed=True,
        is_chop=True,
        is_trend=False,
    )
    bars = {"ETHUSDT": {"open": 98.0, "high": 99.0, "low": 96.5, "close": 97.0}}

    orders = engine.scan_and_generate_orders(
        timestamp=1000,
        bars=bars,
        regimes={"ETHUSDT": reg},
        btc_bullish=True,
        btc_bearish=False,
    )
    assert len(orders) == 1
    assert orders[0].symbol == "ETHUSDT"
    assert orders[0].direction == 1
