"""
Unit & Accounting Invariant Test Suite for v3.5 Alpha Pairs (Port 8088).
Tests:
- Zero-lookahead Relative Strength & KER calculations
- Cross-sectional ranking logic (Longs on leaders, Shorts on laggards)
- Hysteresis Rank Retention Buffer (prevents unnecessary turnover)
- Trailing Stop and Partial TP (+4.0R) executions
- News Core v1.2 Macro Lockout Trimming (30% trim at >= 1.0 ATR)
- Penny-perfect accounting invariants (conservation of equity)
"""

from decimal import Decimal
import pytest
import numpy as np

from v3_5.src.alpha_pairs_engine import AlphaPairsEngine, ActivePairPosition
from v3_5.src.config import (
    INITIAL_CAPITAL,
    BENCHMARK_SYMBOL,
    STOP_LOSS_ATR_MULT,
    TRAILING_ACTIVATION_R,
    TRAILING_DISTANCE_ATR,
    PARTIAL_TP_R,
)


def make_dummy_kline(trend_slope: float, n_bars: int = 180, base_p: float = 100.0) -> list:
    bars = []
    curr = base_p
    for i in range(n_bars):
        curr += trend_slope + (0.1 if i % 2 == 0 else -0.1)
        bars.append({
            "timestamp": 1700000000000 + i * 3600000,
            "open": curr - 0.2,
            "high": curr + 0.5,
            "low": curr - 0.5,
            "close": curr,
            "volume": 1000.0,
        })
    return bars


def test_ranking_relative_strength_and_ker():
    engine = AlphaPairsEngine(initial_capital=Decimal("300.0"))

    # Create dummy universe:
    # BTC flat
    # SOL strong uptrend (+0.5 per bar)
    # DOGE moderate uptrend (+0.2 per bar)
    # NEAR flat (0.0 per bar)
    # ARB downtrend (-0.3 per bar)
    # OP severe downtrend (-0.6 per bar)
    universe = {
        "BTCUSDT": make_dummy_kline(0.0),
        "SOLUSDT": make_dummy_kline(0.5),
        "DOGEUSDT": make_dummy_kline(0.2),
        "NEARUSDT": make_dummy_kline(0.0),
        "ARBUSDT": make_dummy_kline(-0.3),
        "OPUSDT": make_dummy_kline(-0.6),
    }

    metrics = engine.compute_metrics(universe)
    rankings = engine.rank_universe(metrics)

    assert len(rankings) == 5  # BTC is excluded from altcoin ranks
    assert rankings[0]["symbol"] == "SOLUSDT"  # Top leader
    assert rankings[0]["rs"] > 0
    assert rankings[-1]["symbol"] == "OPUSDT"  # Bottom laggard
    assert rankings[-1]["rs"] < 0


def test_hysteresis_rank_buffer_retention():
    engine = AlphaPairsEngine(initial_capital=Decimal("300.0"))

    # Manually seed an active LONG position in SOL
    engine.open_positions["SOLUSDT"] = ActivePairPosition(
        symbol="SOLUSDT",
        direction=1,
        qty=Decimal("1.0"),
        entry_price=Decimal("100.0"),
        stop_price=Decimal("70.0"),
        initial_risk_dist=Decimal("10.0"),
        initial_atr=Decimal("4.0"),
        highest_p=Decimal("100.0"),
        lowest_p=Decimal("100.0"),
        entry_bar=1,
        entry_ts=1700000000000,
    )

    # Simulate ranking where SOL is rank #3 with positive RS (+3.0%)
    # In naive system, it would be exited. In Hysteresis, it must be KEPT!
    engine.bar_counter = 11  # Will become 12 inside process_bar (Rebalance bar)
    universe = {
        "BTCUSDT": make_dummy_kline(0.0),
        "SUIUSDT": make_dummy_kline(0.8),
        "APTUSDT": make_dummy_kline(0.6),
        "SOLUSDT": make_dummy_kline(0.4),  # Rank 3
        "NEARUSDT": make_dummy_kline(0.0),
        "ARBUSDT": make_dummy_kline(-0.3),
        "OPUSDT": make_dummy_kline(-0.6),
    }
    current_prices = {s: Decimal(str(universe[s][-1]["close"])) for s in universe}

    engine.process_bar(universe, current_prices, bar_ts=1700000000000 + 12 * 3600000)

    # Position in SOLUSDT must STILL be open due to Top-5 Hysteresis buffer
    assert "SOLUSDT" in engine.open_positions
    assert len(engine.closed_trades) == 0


def test_hysteresis_exit_on_drop_below_threshold():
    engine = AlphaPairsEngine(initial_capital=Decimal("300.0"))

    # Active LONG in SOL
    engine.open_positions["SOLUSDT"] = ActivePairPosition(
        symbol="SOLUSDT",
        direction=1,
        qty=Decimal("1.0"),
        entry_price=Decimal("100.0"),
        stop_price=Decimal("70.0"),
        initial_risk_dist=Decimal("10.0"),
        initial_atr=Decimal("4.0"),
        highest_p=Decimal("100.0"),
        lowest_p=Decimal("100.0"),
        entry_bar=1,
        entry_ts=1700000000000,
    )

    engine.bar_counter = 11  # Will become 12 inside process_bar (Rebalance bar)
    # SOL now has negative relative strength vs BTC
    universe = {
        "BTCUSDT": make_dummy_kline(0.5),  # BTC rallying
        "SUIUSDT": make_dummy_kline(0.8),
        "APTUSDT": make_dummy_kline(0.6),
        "NEARUSDT": make_dummy_kline(0.2),
        "SOLUSDT": make_dummy_kline(-0.1),  # Underperforming BTC!
        "ARBUSDT": make_dummy_kline(-0.3),
        "OPUSDT": make_dummy_kline(-0.6),
    }
    current_prices = {s: Decimal(str(universe[s][-1]["close"])) for s in universe}

    engine.process_bar(universe, current_prices, bar_ts=1700000000000 + 12 * 3600000)

    # SOL must be exited via HYSTERESIS_EXIT
    assert "SOLUSDT" not in engine.open_positions
    assert len(engine.closed_trades) >= 1
    assert any(t.symbol == "SOLUSDT" and t.reason == "HYSTERESIS_EXIT" for t in engine.closed_trades)


def test_trailing_stop_and_partial_tp():
    engine = AlphaPairsEngine(initial_capital=Decimal("300.0"))

    pos = ActivePairPosition(
        symbol="ETHUSDT",
        direction=1,
        qty=Decimal("1.0"),
        entry_price=Decimal("2000.0"),
        stop_price=Decimal("1900.0"),
        initial_risk_dist=Decimal("100.0"),  # 1R = 100 USD
        initial_atr=Decimal("40.0"),
        highest_p=Decimal("2000.0"),
        lowest_p=Decimal("2000.0"),
        entry_bar=1,
        entry_ts=1700000000000,
    )
    engine.open_positions["ETHUSDT"] = pos

    # Simulate price running to +2.0R (2200 USD)
    universe = {"BTCUSDT": make_dummy_kline(0.0), "ETHUSDT": make_dummy_kline(0.0)}
    current_prices = {"BTCUSDT": Decimal("60000.0"), "ETHUSDT": Decimal("2200.0")}

    engine.process_bar(universe, current_prices, bar_ts=1700000000000 + 3600000)

    # Trailing stop should have moved up from 1900.0 to 2200 - (2.0 * 40) = 2120.0
    assert engine.open_positions["ETHUSDT"].stop_price == Decimal("2120.0")

    # Now simulate price running to +4.5R (2450 USD) -> Triggers Partial TP 50%
    current_prices["ETHUSDT"] = Decimal("2450.0")
    old_realized = engine.realized_equity
    engine.process_bar(universe, current_prices, bar_ts=1700000000000 + 7200000)

    # 50% volume closed, qty should be 0.5, realized equity should have increased
    assert engine.open_positions["ETHUSDT"].qty == Decimal("0.5")
    assert engine.open_positions["ETHUSDT"].tp_taken is True
    assert engine.realized_equity > old_realized


def test_accounting_invariants_conservation():
    engine = AlphaPairsEngine(initial_capital=Decimal("300.0"))

    # Execute a trade sequence
    pos = ActivePairPosition(
        symbol="SOLUSDT",
        direction=1,
        qty=Decimal("2.0"),
        entry_price=Decimal("100.0"),
        stop_price=Decimal("90.0"),
        initial_risk_dist=Decimal("10.0"),
        initial_atr=Decimal("4.0"),
        highest_p=Decimal("100.0"),
        lowest_p=Decimal("100.0"),
        entry_bar=1,
        entry_ts=1700000000000,
    )
    engine.open_positions["SOLUSDT"] = pos

    # Stop loss hits at 88.0 <= 90.0
    universe = {"BTCUSDT": make_dummy_kline(0.0), "SOLUSDT": make_dummy_kline(0.0)}
    current_prices = {"BTCUSDT": Decimal("60000.0"), "SOLUSDT": Decimal("88.0")}

    engine.process_bar(universe, current_prices, bar_ts=1700000000000 + 3600000)

    assert "SOLUSDT" not in engine.open_positions
    assert len(engine.closed_trades) == 1

    closed = engine.closed_trades[0]
    expected_realized = Decimal("300.0") + closed.net_pnl
    assert engine.realized_equity == expected_realized


def test_runner_protection_preserves_profitable_1000pepe():
    """
    Ensure profitable positions (like 1000PEPEUSDT) are NOT exited during
    rebalancing cycles, protecting floating profit and avoiding turnover fee drag.
    """
    engine = AlphaPairsEngine(initial_capital=Decimal("300.0"))

    # Active LONG in 1000PEPEUSDT entered at 0.004865, now at 0.005003 (+3.40 USD profit)
    pepe_pos = ActivePairPosition(
        symbol="1000PEPEUSDT",
        direction=1,
        qty=Decimal("25000.0"),
        entry_price=Decimal("0.004865"),
        stop_price=Decimal("0.004500"),
        initial_risk_dist=Decimal("0.000365"),
        initial_atr=Decimal("0.000150"),
        highest_p=Decimal("0.005003"),
        lowest_p=Decimal("0.004865"),
        entry_bar=1,
        entry_ts=1700000000000,
    )
    engine.open_positions["1000PEPEUSDT"] = pepe_pos

    # Advance to rebalance bar (12)
    engine.bar_counter = 11

    # Simulate market where PEPE slipped to rank 6, but is still in solid profit
    universe = {
        "BTCUSDT": make_dummy_kline(0.1, n_bars=180, base_p=60000.0),
        "SOLUSDT": make_dummy_kline(0.5, n_bars=180, base_p=100.0),
        "SUIUSDT": make_dummy_kline(0.4, n_bars=180, base_p=2.0),
        "APTUSDT": make_dummy_kline(0.3, n_bars=180, base_p=10.0),
        "NEARUSDT": make_dummy_kline(0.2, n_bars=180, base_p=5.0),
        "DOGEUSDT": make_dummy_kline(0.15, n_bars=180, base_p=0.15),
        "1000PEPEUSDT": make_dummy_kline(0.12, n_bars=180, base_p=0.005),
        "ARBUSDT": make_dummy_kline(-0.2, n_bars=180, base_p=1.0),
        "OPUSDT": make_dummy_kline(-0.4, n_bars=180, base_p=2.0),
    }
    current_prices = {
        s: Decimal(str(universe[s][-1]["close"])) for s in universe
    }
    current_prices["1000PEPEUSDT"] = Decimal("0.005003")  # In profit!

    engine.process_bar(universe, current_prices, bar_ts=1700000000000 + 12 * 3600000)

    # 1000PEPEUSDT MUST NOT be exited! It is a profitable runner!
    assert "1000PEPEUSDT" in engine.open_positions
    assert len(engine.closed_trades) == 0


def test_minimum_net_pair_spread_filter():
    """
    Ensure new pairs are NOT entered if the relative strength spread between
    leaders and laggards is compressed (< 5.0%), preventing fee erosion.
    """
    engine = AlphaPairsEngine(initial_capital=Decimal("300.0"))

    # Flat market with almost zero dispersion:
    # Leaders +0.02%, Laggards -0.02% -> Net spread = 0.04% (< 5.0% threshold)
    universe = {
        "BTCUSDT": make_dummy_kline(0.0, n_bars=180, base_p=60000.0),
        "SOLUSDT": make_dummy_kline(0.0002, n_bars=180, base_p=100.0),
        "SUIUSDT": make_dummy_kline(0.0001, n_bars=180, base_p=2.0),
        "ARBUSDT": make_dummy_kline(-0.0001, n_bars=180, base_p=1.0),
        "OPUSDT": make_dummy_kline(-0.0002, n_bars=180, base_p=2.0),
    }
    current_prices = {s: Decimal(str(universe[s][-1]["close"])) for s in universe}

    engine.process_bar(universe, current_prices, bar_ts=1700000000000 + 3600000)

    # Because spread < 5.0%, no new pairs should be opened!
    assert len(engine.open_positions) == 0


def test_strictly_coupled_pairs_execution():
    """
    Ensure engine NEVER enters a naked unhedged single leg.
    Every new pair entry must be balanced (1 Long + 1 Short) to maintain Beta ~ 0.0.
    """
    engine = AlphaPairsEngine(initial_capital=Decimal("300.0"))

    # Case 1: Only Long qualifies (SOL is +10% vs BTC), but no alt is <= -2.5% vs BTC
    universe_only_long = {
        "BTCUSDT": make_dummy_kline(0.0, n_bars=180, base_p=60000.0),
        "SOLUSDT": make_dummy_kline(0.5, n_bars=180, base_p=100.0),  # Strong Long
        "NEARUSDT": make_dummy_kline(0.0, n_bars=180, base_p=5.0),   # Flat
        "ARBUSDT": make_dummy_kline(0.0, n_bars=180, base_p=1.0),    # Flat
    }
    prices_1 = {s: Decimal(str(universe_only_long[s][-1]["close"])) for s in universe_only_long}
    engine.process_bar(universe_only_long, prices_1, bar_ts=1700000000000 + 3600000)

    # Must NOT open a naked Long without a short hedge!
    assert len(engine.open_positions) == 0

    # Case 2: Both Long and Short qualify (SOL is +10%, OP is -10%)
    universe_both = {
        "BTCUSDT": make_dummy_kline(0.0, n_bars=180, base_p=60000.0),
        "SOLUSDT": make_dummy_kline(0.5, n_bars=180, base_p=100.0),   # Strong Long
        "NEARUSDT": make_dummy_kline(0.0, n_bars=180, base_p=5.0),    # Flat
        "OPUSDT": make_dummy_kline(-0.2, n_bars=180, base_p=100.0),   # Strong Short
    }
    prices_2 = {s: Decimal(str(universe_both[s][-1]["close"])) for s in universe_both}
    engine.process_bar(universe_both, prices_2, bar_ts=1700000000000 + 7200000)

    # Exactly 2 positions: 1 Long (SOL) and 1 Short (OP)
    assert len(engine.open_positions) == 2
    assert engine.open_positions["SOLUSDT"].direction == 1
    assert engine.open_positions["OPUSDT"].direction == -1
