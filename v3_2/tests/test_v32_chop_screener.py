"""
Unit tests for v3.2 IRAS Chop Screener Module.
Verifies:
1. Liquidity filter gating
2. Conflict avoidance with Trend Engine
3. Kaufman Efficiency Ratio noise gating (KER <= 0.15)
4. Bollinger Bandwidth friction clearance (Bandwidth >= 3.5%)
5. Institutional Range Alpha Score (IRAS) ranking and Carry Boost
"""

import pytest
from v3_2.src.chop_screener import ChopUniverseScreener, ChopScreenerConfig, ChopCandidateMetric


@pytest.fixture
def screener():
    cfg = ChopScreenerConfig(
        min_turnover_24h=10_000_000.0,
        max_ker=0.15,
        min_bandwidth_pct=0.035,
        max_adx=18.0,
        top_n=3,
    )
    return ChopUniverseScreener(cfg)


def test_screener_excludes_trend_symbols(screener):
    """Coins currently claimed by Trend Engine must be rejected immediately."""
    klines = {
        "open": [100.0] * 30,
        "high": [106.0] * 30,
        "low": [94.0] * 30,
        "close": [100.0] * 30,
        "volume": [1000.0] * 30,
    }
    cand = screener.evaluate_symbol(
        symbol="BTCUSDT",
        price=100.0,
        turnover_24h=50_000_000.0,
        open_interest=10_000_000.0,
        funding_rate=0.0001,
        klines=klines,
        exclude_symbols={"BTCUSDT", "ETHUSDT"},
    )
    assert cand.passed_filters is False
    assert "Trend Engine" in cand.rejection_reason


def test_screener_rejects_trending_coins(screener):
    """Coins with smooth directional trends (KER > 0.15) must be rejected."""
    closes = [100.0 + i * 2.0 for i in range(30)]
    klines = {
        "open": closes,
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "volume": [1000.0] * 30,
    }
    cand = screener.evaluate_symbol(
        symbol="TRENDCOINUSDT",
        price=closes[-1],
        turnover_24h=30_000_000.0,
        open_interest=10_000_000.0,
        funding_rate=0.0001,
        klines=klines,
    )
    assert cand.passed_filters is False
    assert "KER" in cand.rejection_reason


def test_screener_rejects_low_bandwidth(screener):
    """Coins with bandwidth < 3.5% cannot beat friction and must be rejected."""
    # Flat line with 0.5% width
    closes = [100.0 if i % 2 == 0 else 100.5 for i in range(30)]
    klines = {
        "open": closes,
        "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes],
        "close": closes,
        "volume": [1000.0] * 30,
    }
    cand = screener.evaluate_symbol(
        symbol="TIGHTCOINUSDT",
        price=closes[-1],
        turnover_24h=20_000_000.0,
        open_interest=5_000_000.0,
        funding_rate=0.0001,
        klines=klines,
    )
    assert cand.passed_filters is False
    assert "Bandwidth" in cand.rejection_reason


def test_screener_accepts_pure_chop_with_iras(screener):
    """Coins with wide stationary oscillation and low KER pass and calculate IRAS score."""
    # Ping-pong between 96 and 104 -> ~8% bandwidth, near-zero KER
    closes = [96.0 if i % 2 == 0 else 104.0 for i in range(30)]
    klines = {
        "open": closes,
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [1000.0] * 30,
    }
    cand = screener.evaluate_symbol(
        symbol="STATIONARYUSDT",
        price=closes[-1],
        turnover_24h=25_000_000.0,
        open_interest=5_000_000.0,
        funding_rate=-0.0004,
        klines=klines,
    )
    assert cand.passed_filters is True
    assert cand.ker <= 0.15
    assert cand.bandwidth_pct >= 0.035
    assert cand.crossings >= 5
    assert cand.chop_score > 0.05
    assert cand.carry_mult > 1.0


def test_calibrated_default_config():
    """Verifies calibrated production defaults for IRAS screener."""
    default_cfg = ChopScreenerConfig()
    assert default_cfg.min_turnover_24h == 5_000_000.0
    assert default_cfg.max_ker == 0.25
    assert default_cfg.min_bandwidth_pct == 0.025
    assert default_cfg.max_adx == 20.0
    assert default_cfg.rescan_interval_hours == 1
