"""
Unit Tests for Dynamic Universe Screener Module (v3).
"""

import pytest
from v3.src.universe_screener import UniverseScreener, ScreenerConfig, CoinCandidateMetric


@pytest.fixture
def screener():
    cfg = ScreenerConfig(
        min_turnover_24h=25_000_000.0,
        min_open_interest=5_000_000.0,
        max_funding_abs=0.0005,
        min_norm_atr=0.020,
        min_adx=16.0,
        min_efficiency_ratio=0.30,
        top_n=3,
        benchmark_symbol="BTCUSDT",
    )
    return UniverseScreener(cfg)


def test_efficiency_ratio_straight_line(screener):
    """Straight trend has efficiency ratio 1.0."""
    prices = [100.0, 105.0, 110.0, 115.0, 120.0]
    er = screener.calculate_efficiency_ratio(prices)
    assert abs(er - 1.0) < 1e-4


def test_efficiency_ratio_choppy_noise(screener):
    """Pure ping-pong chop has efficiency ratio near 0.0."""
    prices = [100.0, 110.0, 100.0, 110.0, 100.0]
    er = screener.calculate_efficiency_ratio(prices)
    assert er == 0.0


def test_liquidity_filter_rejection(screener):
    """Coins below turnover threshold must be rejected."""
    cand = screener.evaluate_symbol(
        symbol="SHITCOINUSDT",
        price=1.0,
        turnover_24h=10_000_000.0,  # $10M < $25M min
        open_interest=10_000_000.0,
        funding_rate=0.0001,
        atr=0.04,
        adx=25.0,
        recent_closes=[0.9, 0.95, 1.0],
    )
    assert cand.passed_filters is False
    assert "Turnover" in cand.rejection_reason


def test_funding_rate_sanitization(screener):
    """Coins with extreme crowded funding rate must be rejected."""
    cand = screener.evaluate_symbol(
        symbol="SQUEEZEUSDT",
        price=50.0,
        turnover_24h=50_000_000.0,
        open_interest=15_000_000.0,
        funding_rate=0.0008,  # 0.08% > 0.05% max
        atr=2.0,
        adx=30.0,
        recent_closes=[45.0, 48.0, 50.0],
    )
    assert cand.passed_filters is False
    assert "Funding" in cand.rejection_reason


def test_volatility_filter_rejection(screener):
    """Stagnant coins with low ATR must be rejected."""
    cand = screener.evaluate_symbol(
        symbol="SLOWCOINUSDT",
        price=100.0,
        turnover_24h=50_000_000.0,
        open_interest=10_000_000.0,
        funding_rate=0.0001,
        atr=1.0,  # 1.0 / 100.0 = 1.0% < 2.0% min
        adx=25.0,
        recent_closes=[98.0, 99.0, 100.0],
    )
    assert cand.passed_filters is False
    assert "NormATR" in cand.rejection_reason


def test_composite_score_and_ranking(screener):
    """Strong trending leader must score higher than a mediocre candidate."""
    c1 = screener.evaluate_symbol(
        symbol="LEADERUSDT",
        price=100.0,
        turnover_24h=80_000_000.0,
        open_interest=20_000_000.0,
        funding_rate=0.0001,
        atr=5.0,  # 5.0% ATR
        adx=40.0,
        recent_closes=[80.0, 90.0, 100.0],
        btc_return_24h=0.02,
        coin_return_24h=0.10,  # +8.0% RS
        volume_24h=3000.0,
        volume_sma_20d=1500.0, # 2.0x volume expansion
    )
    assert c1.passed_filters is True

    c2 = screener.evaluate_symbol(
        symbol="LAGGARDUSDT",
        price=20.0,
        turnover_24h=30_000_000.0,
        open_interest=6_000_000.0,
        funding_rate=0.0001,
        atr=0.5,  # 2.5% ATR
        adx=18.0,
        recent_closes=[19.5, 19.8, 20.0],
        btc_return_24h=0.02,
        coin_return_24h=0.01,  # -1.0% RS
        volume_24h=1000.0,
        volume_sma_20d=1000.0,
    )
    assert c2.passed_filters is True
    assert c1.composite_score > c2.composite_score


def test_top_universe_preserves_active_positions(screener):
    """Active open positions must never be dropped from the universe."""
    candidates = [
        CoinCandidateMetric(
            symbol="COIN_A", price=10.0, turnover_24h=50e6, open_interest=10e6,
            funding_rate=0.0001, norm_atr=0.04, adx=35.0, efficiency_ratio=0.8,
            relative_strength=0.05, volume_ratio=1.5, composite_score=0.85,
            passed_filters=True
        ),
        CoinCandidateMetric(
            symbol="COIN_B", price=20.0, turnover_24h=40e6, open_interest=10e6,
            funding_rate=0.0001, norm_atr=0.035, adx=30.0, efficiency_ratio=0.7,
            relative_strength=0.03, volume_ratio=1.2, composite_score=0.75,
            passed_filters=True
        ),
        CoinCandidateMetric(
            symbol="COIN_C", price=30.0, turnover_24h=35e6, open_interest=8e6,
            funding_rate=0.0001, norm_atr=0.03, adx=25.0, efficiency_ratio=0.6,
            relative_strength=0.01, volume_ratio=1.1, composite_score=0.65,
            passed_filters=True
        ),
        # COIN_DROPPED fell below Top 3:
        CoinCandidateMetric(
            symbol="COIN_DROPPED", price=5.0, turnover_24h=26e6, open_interest=6e6,
            funding_rate=0.0001, norm_atr=0.022, adx=18.0, efficiency_ratio=0.4,
            relative_strength=-0.02, volume_ratio=1.0, composite_score=0.45,
            passed_filters=True
        ),
    ]

    # Without active position: Top 3 selected
    top = screener.get_top_universe(candidates, top_n=3)
    assert "COIN_A" in top
    assert "COIN_B" in top
    assert "COIN_C" in top
    assert "COIN_DROPPED" not in top
    assert "BTCUSDT" in top  # benchmark always included

    # With active position in COIN_DROPPED: it MUST be retained!
    top_with_active = screener.get_top_universe(candidates, top_n=3, keep_active_symbols={"COIN_DROPPED"})
    assert "COIN_DROPPED" in top_with_active
    assert "COIN_A" in top_with_active
    assert "BTCUSDT" in top_with_active
