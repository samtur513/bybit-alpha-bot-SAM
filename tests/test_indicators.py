"""
Unit tests for Technical Indicators Module (src/indicators.py).
Tests calculate_ema, calculate_ema_200, calculate_wilder_atr,
calculate_donchian_channel, calculate_rsi, calculate_adx, and calculate_volume_sma.
"""
from decimal import Decimal
import numpy as np
import pandas as pd
import pytest

from src.indicators import (
    calculate_adx,
    calculate_donchian_channel,
    calculate_ema,
    calculate_ema_200,
    calculate_rsi,
    calculate_volume_sma,
    calculate_wilder_atr,
)


def test_calculate_ema_generic():
    """Verify calculate_ema works for arbitrary period, starts with SMA, and applies alpha = 2 / (period + 1)."""
    period = 5
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0]
    s = pd.Series(values)
    ema = calculate_ema(s, period=period)

    # First period - 1 (0..3) should be NaN
    for i in range(period - 1):
        assert np.isnan(ema[i])

    # Index period - 1 (4) is SMA of first 5 values: (10+20+30+40+50)/5 = 30.0
    assert ema[4] == pytest.approx(30.0)

    # Index 5: alpha = 2 / (5 + 1) = 2/6 = 1/3
    # ema[5] = (1/3) * 60 + (2/3) * 30 = 20 + 20 = 40.0
    assert ema[5] == pytest.approx(40.0)

    # Index 6: ema[6] = (1/3) * 70 + (2/3) * 40 = 23.3333... + 26.6666... = 50.0
    assert ema[6] == pytest.approx(50.0)


def test_calculate_ema_dataframe_input():
    """Verify calculate_ema accepts DataFrame with 'close' column."""
    df = pd.DataFrame({"close": [10.0] * 20})
    ema = calculate_ema(df, period=10)
    assert np.isnan(ema[8])
    assert ema[9] == pytest.approx(10.0)
    assert ema[10] == pytest.approx(10.0)


def test_calculate_ema_200_compat():
    """Verify calculate_ema_200 delegates to calculate_ema."""
    s = pd.Series([100.0] * 205)
    ema_200 = calculate_ema_200(s)
    ema_gen = calculate_ema(s, period=200)

    pd.testing.assert_series_equal(ema_200, ema_gen)
    assert np.isnan(ema_200[198])
    assert ema_200[199] == pytest.approx(100.0)
    assert ema_200[200] == pytest.approx(100.0)


def test_calculate_volume_sma():
    """Verify volume SMA rolling calculation."""
    period = 20
    vol_values = list(range(1, 31))  # 1 to 30
    df = pd.DataFrame({"volume": vol_values})
    v_sma = calculate_volume_sma(df, period=period)

    for i in range(period - 1):
        assert np.isnan(v_sma[i])

    # Index 19 is SMA of 1..20 = 210 / 20 = 10.5
    assert v_sma[19] == pytest.approx(10.5)
    # Index 20 is SMA of 2..21 = 230 / 20 = 11.5
    assert v_sma[20] == pytest.approx(11.5)

    # Also test Series input
    s_vol = pd.Series(vol_values)
    v_sma_s = calculate_volume_sma(s_vol, period=period)
    pd.testing.assert_series_equal(v_sma, v_sma_s, check_names=False)


def test_calculate_rsi_strictly_increasing():
    """Verify RSI equals 100 when price only increases (zero losses)."""
    period = 14
    closes = [100.0 + i * 2.0 for i in range(30)]
    df = pd.DataFrame({"close": closes})
    rsi = calculate_rsi(df, period=period)

    # First `period` indices (0..13) are NaN
    for i in range(period):
        assert np.isnan(rsi[i])

    # From index 14 onward, RSI should be 100 because avg_loss == 0
    for i in range(period, 30):
        assert rsi[i] == pytest.approx(100.0)


def test_calculate_rsi_strictly_decreasing():
    """Verify RSI equals 0 when price only decreases (zero gains)."""
    period = 14
    closes = [200.0 - i * 2.0 for i in range(30)]
    df = pd.DataFrame({"close": closes})
    rsi = calculate_rsi(df, period=period)

    for i in range(period):
        assert np.isnan(rsi[i])

    for i in range(period, 30):
        assert rsi[i] == pytest.approx(0.0)


def test_calculate_rsi_flat_prices():
    """Verify RSI equals 100 when avg_loss == 0 for flat prices."""
    period = 14
    closes = [100.0] * 30
    df = pd.DataFrame({"close": closes})
    rsi = calculate_rsi(df, period=period)

    for i in range(period):
        assert np.isnan(rsi[i])

    # For flat prices, avg_loss == 0 -> RSI = 100
    for i in range(period, 30):
        assert rsi[i] == pytest.approx(100.0)


def test_calculate_rsi_wilder_smoothing():
    """Verify Wilder smoothing calculation for RSI."""
    period = 14
    # Pattern of +2 then -1 repeatedly
    closes = [100.0]
    for i in range(1, 30):
        delta = 2.0 if (i % 2 == 1) else -1.0
        closes.append(closes[-1] + delta)

    s = pd.Series(closes)
    rsi = calculate_rsi(s, period=period)

    assert np.isnan(rsi[13])
    assert not np.isnan(rsi[14])
    assert 0.0 <= rsi[14] <= 100.0
    assert 0.0 <= rsi[20] <= 100.0


def test_calculate_adx_strong_trend():
    """Verify ADX calculation in a strong uptrend."""
    period = 14
    # Monotonic uptrend with constant range
    bars = []
    price = 100.0
    for i in range(40):
        price += 5.0
        bars.append({"high": price + 2.0, "low": price - 2.0, "close": price})
    df = pd.DataFrame(bars)

    adx = calculate_adx(df, period=period)

    # Warmup: first 2 * period - 1 (27 bars, indices 0..26) are NaN
    for i in range(2 * period - 1):
        assert np.isnan(adx[i])

    # From index 27 onward, ADX is defined and should be near 100 due to pure uptrend
    assert not np.isnan(adx[27])
    assert adx[27] == pytest.approx(100.0, rel=1e-2)
    assert adx[35] == pytest.approx(100.0, rel=1e-2)


def test_calculate_adx_short_data():
    """Verify ADX returns all NaNs if length < 2 * period."""
    period = 14
    bars = [{"high": 105.0, "low": 95.0, "close": 100.0} for _ in range(20)]
    df = pd.DataFrame(bars)
    adx = calculate_adx(df, period=period)

    assert len(adx) == 20
    assert adx.isna().all()


def test_calculate_wilder_atr_series_and_df():
    """Verify calculate_wilder_atr handles DataFrame and Series."""
    data = [{"high": 110.0, "low": 90.0, "close": 100.0} for _ in range(30)]
    df = pd.DataFrame(data)
    atr = calculate_wilder_atr(df, period=14)

    assert np.isnan(atr[13])
    assert atr[14] == pytest.approx(20.0)
    assert atr[15] == pytest.approx(20.0)


def test_calculate_adx_exact_calculation():
    """Verify exact ADX calculation on small manual dataset with period=3."""
    period = 3
    # 7 bars
    data = [
        {"high": 10.0, "low": 8.0, "close": 9.0},   # bar 0
        {"high": 12.0, "low": 9.0, "close": 11.0},  # bar 1: TR=3, +DM=2, -DM=0
        {"high": 11.0, "low": 7.0, "close": 8.0},   # bar 2: TR=4, +DM=0, -DM=2
        {"high": 13.0, "low": 8.0, "close": 12.0},  # bar 3: TR=5, +DM=2, -DM=0
        {"high": 14.0, "low": 11.0, "close": 13.0}, # bar 4: TR=3, +DM=1, -DM=0
        {"high": 15.0, "low": 12.0, "close": 14.0}, # bar 5: TR=3, +DM=1, -DM=0
        {"high": 16.0, "low": 13.0, "close": 15.0}, # bar 6: TR=3, +DM=1, -DM=0
    ]
    df = pd.DataFrame(data)
    adx = calculate_adx(df, period=period)

    # For period = 3: first 2*period - 1 = 5 (indices 0..4) should be NaN
    assert np.isnan(adx[0])
    assert np.isnan(adx[4])
    # Index 5 (6th bar) should be first valid ADX value
    assert not np.isnan(adx[5])
    assert 0.0 <= adx[5] <= 100.0
    assert not np.isnan(adx[6])
    assert 0.0 <= adx[6] <= 100.0
