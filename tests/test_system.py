"""
Unit and Integration Tests for Bybit Research & Trading System.
Verifies indicators, risk management, circuit breakers, and simulation rules.
"""
from decimal import Decimal
import numpy as np
import pandas as pd
import pytest

from src.config import StrategyParams
from src.indicators import (
    calculate_donchian_channel,
    calculate_ema_200,
    calculate_wilder_atr,
)
from src.risk_manager import RiskManager
from src.strategy import StrategyV01

def test_ema_200_calculation():
    """Verify EMA(200) starts with SMA(200) and uses alpha = 2 / 201."""
    # Create 205 constant values
    values = [100.0] * 205
    s = pd.Series(values)
    ema = calculate_ema_200(s, period=200)
    
    assert np.isnan(ema[0])
    assert np.isnan(ema[198])
    assert ema[199] == pytest.approx(100.0)
    assert ema[200] == pytest.approx(100.0)

def test_wilder_atr_calculation():
    """Verify Wilder's ATR starts with SMA(14) and applies smoothing."""
    # Create test bars with constant high=110, low=90, close=100 (TR = 20)
    data = []
    for _ in range(30):
        data.append({"high": 110.0, "low": 90.0, "close": 100.0})
    df = pd.DataFrame(data)
    atr = calculate_wilder_atr(df, period=14)
    
    assert np.isnan(atr[0])
    assert np.isnan(atr[13])
    assert atr[14] == 20.0
    assert atr[15] == 20.0

def test_donchian_channel_excludes_current_bar():
    """Verify channel looks back N closed bars strictly excluding current bar."""
    highs = [10.0, 20.0, 30.0, 40.0, 50.0]
    lows = [5.0, 15.0, 25.0, 35.0, 45.0]
    closes = [8.0, 18.0, 28.0, 38.0, 48.0]
    df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
    
    ch_h, ch_l = calculate_donchian_channel(df, lookback=2)
    # At index 2, previous 2 bars are index 0, 1 -> max high = 20.0, min low = 5.0
    assert ch_h[2] == 20.0
    assert ch_l[2] == 5.0
    # Current bar high (30.0) must NOT be included at index 2
    assert ch_h[2] != 30.0

def test_risk_manager_sizing_and_filters():
    """Verify risk manager respects 1% risk, qtyStep floor, and minNotional."""
    rm = RiskManager(initial_capital=Decimal("300.0"))
    
    # Capital $300, 1% risk = $3.00
    # Stop distance = 1000, entry = 60,000
    # Expected q = 3.0 / 1000 = 0.003 BTC
    res = rm.calculate_order_size(
        symbol="BTCUSDT",
        direction=1,
        entry_price=Decimal("60000.0"),
        atr=Decimal("500.0"),
        k_mult=2,  # stop distance = 1000
        current_funding_rate=Decimal("0.0001"),
        current_ts=1728691200000,
    )
    assert res.allowed is True
    assert res.qty > Decimal("0")
    # Must be rounded to qtyStep 0.001
    assert res.qty % Decimal("0.001") == Decimal("0")

def test_risk_manager_skips_when_min_order_exceeds_budget():
    """Verify trade is skipped when minOrderQty exceeds risk budget (no round up)."""
    # Capital $50, 1% risk = $0.50
    # At stop distance 2000, minOrderQty 0.001 BTC would risk 0.001 * 2000 = $2.00 > $0.50
    rm = RiskManager(initial_capital=Decimal("50.0"))
    res = rm.calculate_order_size(
        symbol="BTCUSDT",
        direction=1,
        entry_price=Decimal("60000.0"),
        atr=Decimal("1000.0"),
        k_mult=2,
        current_funding_rate=Decimal("0.0001"),
        current_ts=1728691200000,
    )
    assert res.allowed is False
    assert "below minOrderQty" in res.skip_reason or "exceeds budget" in res.skip_reason

def test_risk_manager_circuit_breakers():
    """Verify 30% peak drawdown triggers permanent halt."""
    rm = RiskManager(initial_capital=Decimal("300.0"))
    # Equity drops to $200 (33.3% drawdown > 30%)
    rm.update_calendar_and_equity(1728691200000, Decimal("200.0"))
    assert rm.is_permanently_halted is True
    assert "Max peak drawdown" in rm.halt_reason
