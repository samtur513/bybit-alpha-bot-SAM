"""
Unit tests for RVOL, Delta OI, and ATR Exhaustion Guard in Universe Screener (v3.1).
"""

import pytest
from v3.src.universe_screener import UniverseScreener, ScreenerConfig, CoinCandidateMetric


def test_rvol_and_delta_oi_in_composite_score():
    cfg = ScreenerConfig(
        min_turnover_24h=25_000_000.0,
        min_open_interest=5_000_000.0,
        enable_rvol_filter=True,
        min_rvol=1.2,
        enable_delta_oi_filter=True,
    )
    screener = UniverseScreener(cfg)

    # Candidate with good RVOL (2.0) and positive Delta OI (+8%)
    cand_good = screener.evaluate_symbol(
        symbol="PUMPUSDT",
        price=10.0,
        turnover_24h=50_000_000.0,
        open_interest=10_000_000.0,
        funding_rate=0.0001,
        atr=0.4,
        adx=30.0,
        recent_closes=[9.0, 9.5, 10.0],
        volume_24h=2000.0,
        volume_sma_20d=1000.0,  # RVOL = 2.0
        delta_oi_pct=0.08,      # +8% OI growth
    )
    assert cand_good.passed_filters is True
    assert cand_good.rvol == 2.0
    assert cand_good.delta_oi_pct == 0.08

    # Candidate with low RVOL (0.8 < 1.2 min)
    cand_low_vol = screener.evaluate_symbol(
        symbol="LOWVOLUSDT",
        price=10.0,
        turnover_24h=50_000_000.0,
        open_interest=10_000_000.0,
        funding_rate=0.0001,
        atr=0.4,
        adx=30.0,
        recent_closes=[9.0, 9.5, 10.0],
        volume_24h=800.0,
        volume_sma_20d=1000.0,  # RVOL = 0.8
        delta_oi_pct=0.05,
    )
    assert cand_low_vol.passed_filters is False
    assert "RVOL" in cand_low_vol.rejection_reason


def test_atr_exhaustion_guard():
    """If coin has already traversed >= 80% daily ATR in current candle, reject entry."""
    cfg = ScreenerConfig(
        enable_atr_exhaustion_guard=True,
        max_daily_atr_exhaustion=0.80,
    )
    screener = UniverseScreener(cfg)

    # 24h range is 9.0 to 10.0 (move = 1.0), daily ATR is 1.0 -> 100% exhaustion (>80%)
    cand_exhausted = screener.evaluate_symbol(
        symbol="EXHAUSTEDUSDT",
        price=10.0,
        turnover_24h=50_000_000.0,
        open_interest=10_000_000.0,
        funding_rate=0.0001,
        atr=1.0,
        adx=30.0,
        recent_closes=[9.0, 9.5, 10.0],
        current_candle_range=0.90,  # 0.90 / 1.0 = 90% > 80%
    )
    assert cand_exhausted.passed_filters is False
    assert "ATR Exhaustion" in cand_exhausted.rejection_reason
