"""
Comprehensive Invariant Test Suite for NewDragon v4.0 Universal Phase 1.
Verifies Zero-Lookahead HMM, Micro-Account Contract Router, Drawdown Brakes,
and Adaptive 4-Stage Exit FSM.
"""
import sys
from pathlib import Path
import numpy as np
import pytest

# Ensure root of project is on sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from core.models import (
    InstrumentSpec,
    PortfolioState,
    SignalOutput,
    PositionTracker,
    ExitAction,
)
from layers.l2_regime import CausalRegimeDetector
from layers.l5_risk import UniversalPositionSizer
from layers.l6_execution.exit_fsm import AdaptiveExitFSM


# ==============================================================================
# 1. Zero-Lookahead Causal HMM Tests
# ==============================================================================

def test_causal_hmm_zero_lookahead_invariance():
    """
    Verifies that appending future bars X[50:100] (even with severe shock)
    does NOT alter the probability distribution calculated at step t=49.
    """
    rng = np.random.default_rng(42)
    # Train data: 300 bars of 4 features
    train_data = rng.normal(0.0, 0.02, size=(300, 4))
    stream_short = rng.normal(0.0, 0.02, size=(50, 4))
    # Future shock added only to stream_future
    stream_future_shock = np.vstack([stream_short, rng.normal(-0.10, 0.15, size=(50, 4))])
    closes = np.cumprod(1.0 + rng.normal(0.0, 0.01, size=200)) * 100.0

    # Detector A: Evaluates only short stream
    det_a = CausalRegimeDetector(n_states=4, random_state=42)
    det_a.fit_historical(train_data)
    res_a = None
    for row in stream_short:
        res_a = det_a.step_causal(row, closes)

    # Detector B: Evaluates stream with future shock
    det_b = CausalRegimeDetector(n_states=4, random_state=42)
    det_b.fit_historical(train_data)
    res_b_at_50 = None
    for i, row in enumerate(stream_future_shock):
        step_res = det_b.step_causal(row, closes)
        if i == len(stream_short) - 1:
            res_b_at_50 = step_res

    assert res_a is not None
    assert res_b_at_50 is not None
    # Probabilities at step t=49 must be bitwise/numerically identical
    for k in res_a["probabilities"]:
        diff = abs(res_a["probabilities"][k] - res_b_at_50["probabilities"][k])
        assert diff < 1e-12, f"Lookahead bias detected! Diff in {k} = {diff}"


def test_deterministic_state_mapping():
    """
    Verifies that _sort_and_bind_states accurately categorizes
    shock, bull, bear, sideways without label switching.
    """
    rng = np.random.default_rng(123)
    features = rng.normal(0.0, 0.01, size=(250, 4))
    det = CausalRegimeDetector(n_states=4, random_state=123)
    det.fit_historical(features)

    assert set(det.state_map.values()) == {"shock", "bull", "bear", "sideways"}
    assert len(det.state_map) == 4


# ==============================================================================
# 2. Risk Sizing & Micro-Account Contract Router Tests
# ==============================================================================

def test_micro_budget_100usd_rejects_btc_accepts_sol():
    """
    On a $100 budget with 1R ~ $0.50, BTCUSDT requires minNotional = $100.
    The sizer must strictly reject BTC (return 0.0) without expanding risk,
    while accepting SOLUSDT where minNotional = $5.
    """
    sizer = UniversalPositionSizer()
    state_100 = PortfolioState(
        total_capital=100.0,
        available_margin=65.0,
        current_drawdown=0.0,
        regime="bull",
        asset_volatility=0.025,
        inv_vol_sum=40.0,
        avg_correlation=0.5,
        cvar_95_pct=0.05,
        expected_mu=0.02,
        expected_var=0.02,
    )

    btc_spec = InstrumentSpec(
        symbol="BTCUSDT",
        qty_step=0.001,
        tick_size=0.1,
        min_order_qty=0.001,
        min_notional=100.0,
        max_leverage=10.0,
    )
    btc_sig = SignalOutput(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=70000.0,
        stop_price=68600.0,  # 2% stop
        atr_1h=500.0,
        scaled_forecast=15.0,
        composite_score=0.85,
    )
    # BTC order requires at least $100 notional, which exceeds 1R risk budget ($0.425) -> Must reject!
    q_btc = sizer.compute_quantity(btc_sig, btc_spec, state_100)
    assert q_btc == 0.0, f"Expected 0.0 for BTC on $100 budget, got {q_btc}"

    # SOL order passes because notional easily satisfies $5 minNotional
    sol_spec = InstrumentSpec(
        symbol="SOLUSDT",
        qty_step=0.1,
        tick_size=0.01,
        min_order_qty=0.1,
        min_notional=5.0,
        max_leverage=10.0,
    )
    sol_sig = SignalOutput(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=150.0,
        stop_price=147.0,  # 2% stop
        atr_1h=1.5,
        scaled_forecast=15.0,
        composite_score=0.85,
    )
    q_sol = sizer.compute_quantity(sol_sig, sol_spec, state_100)
    assert q_sol >= 0.1, f"Expected valid SOL quantity, got {q_sol}"
    notional_sol = q_sol * 150.0
    assert notional_sol >= 5.0, f"SOL notional (${notional_sol}) must satisfy $5 minimum"


def test_drawdown_brakes_stepped_protection():
    """
    Verifies stepped reduction of risk multiplier across drawdown thresholds.
    """
    sizer = UniversalPositionSizer()
    assert sizer.get_drawdown_multiplier(0.01) == 1.0     # < 5% -> 100%
    assert sizer.get_drawdown_multiplier(0.049) == 1.0
    assert sizer.get_drawdown_multiplier(0.05) == 0.75    # 5% - 10% -> 75%
    assert sizer.get_drawdown_multiplier(0.099) == 0.75
    assert sizer.get_drawdown_multiplier(0.10) == 0.35    # 10% - 20% -> 35%
    assert sizer.get_drawdown_multiplier(0.199) == 0.35
    assert sizer.get_drawdown_multiplier(0.20) == 0.0     # >= 20% -> Hard halt
    assert sizer.get_drawdown_multiplier(0.35) == 0.0


def test_shock_regime_forces_zero_quantity():
    """
    Verifies that a Shock regime forces risk budget to 0.0 and rejects any entry.
    """
    sizer = UniversalPositionSizer()
    state_shock = PortfolioState(
        total_capital=10000.0,
        available_margin=8000.0,
        current_drawdown=0.02,
        regime="shock",  # Market in crisis / liquidation cascade
        asset_volatility=0.04,
        inv_vol_sum=20.0,
    )
    spec = InstrumentSpec("SOLUSDT", qty_step=0.1, tick_size=0.01, min_order_qty=0.1, min_notional=5.0)
    sig = SignalOutput("SOLUSDT", "LONG", entry_price=150.0, stop_price=145.0, atr_1h=2.0, composite_score=1.0)
    assert sizer.compute_quantity(sig, spec, state_shock) == 0.0


# ==============================================================================
# 3. 4-Stage Adaptive Exit FSM Tests
# ==============================================================================

def test_fsm_stage0_fast_scratch_on_inside_bars_and_adverse_ofi():
    """
    Stage 0 Fast Scratch: If 2 inside bars occur with profit < 0.6R
    and adverse order flow imbalance, exit immediately to save capital.
    """
    fsm = AdaptiveExitFSM()
    pos = PositionTracker(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=150.0,
        current_stop=147.0,
        initial_1r_dist=3.0,
        remaining_qty=10.0,
        stage=0,
    )

    # Bar 1: Inside bar (high <= prev_high and low >= prev_low)
    act1 = fsm.evaluate_bar(
        pos=pos,
        high=150.5,
        low=149.8,
        close=150.2,
        prev_high=151.0,
        prev_low=149.5,
        highest_20=151.0,
        lowest_20=149.0,
        atr_1h=1.5,
        z_ofi=-0.5,
    )
    assert act1.action_type == "HOLD"
    assert pos.inside_bar_count == 1

    # Bar 2: Second inside bar with adverse OFI (z_ofi < -1.5)
    act2 = fsm.evaluate_bar(
        pos=pos,
        high=150.3,
        low=150.0,
        close=150.1,
        prev_high=150.5,
        prev_low=149.8,
        highest_20=151.0,
        lowest_20=149.0,
        atr_1h=1.5,
        z_ofi=-1.8,  # Adverse selling pressure
    )
    assert act2.action_type == "FULL_CLOSE_SCRATCH"
    assert act2.close_qty_ratio == 1.0
    assert act2.reason == "fast_scratch_inside_bars"


def test_fsm_stage1_ratchet_lock_at_085r():
    """
    Stage 1: When unrealized profit reaches +0.85R, tighten stop from -1.0R to -0.35R.
    """
    fsm = AdaptiveExitFSM()
    pos = PositionTracker(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=100.0,
        current_stop=95.0,  # 1R = $5.0
        initial_1r_dist=5.0,
        remaining_qty=10.0,
        stage=0,
    )
    # Price reaches 104.3 (+0.86R)
    act = fsm.evaluate_bar(
        pos=pos,
        high=104.5,
        low=103.0,
        close=104.3,
        prev_high=103.0,
        prev_low=102.0,
        highest_20=104.5,
        lowest_20=98.0,
        atr_1h=2.0,
    )
    assert act.action_type == "UPDATE_STOP"
    expected_stop = 100.0 - (0.35 * 5.0)  # 98.25
    assert pos.current_stop == pytest.approx(expected_stop, rel=1e-5)
    assert pos.stage == 1


def test_fsm_stage2_partial_scaleout_at_14r():
    """
    Stage 2: When unrealized profit reaches +1.40R, scale out 60% via Maker Limit
    and advance remaining 40% runner stop to +0.35R (guaranteed profit lock).
    """
    fsm = AdaptiveExitFSM()
    pos = PositionTracker(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=100.0,
        current_stop=98.25,
        initial_1r_dist=5.0,
        remaining_qty=10.0,
        stage=1,
    )
    # Price hits 107.2 (+1.44R)
    act = fsm.evaluate_bar(
        pos=pos,
        high=107.5,
        low=105.0,
        close=107.2,
        prev_high=105.0,
        prev_low=104.0,
        highest_20=107.5,
        lowest_20=98.0,
        atr_1h=2.0,
    )
    assert act.action_type == "PARTIAL_CLOSE_AND_STOP"
    assert act.close_qty_ratio == 0.60
    expected_profit_lock_stop = 100.0 + (0.35 * 5.0)  # 101.75
    assert pos.current_stop == pytest.approx(expected_profit_lock_stop, rel=1e-5)
    assert pos.stage == 2


def test_fsm_stage3_chandelier_trail_and_compression():
    """
    Stage 3: Chandelier trail activates at +2.0R.
    When a shock is detected (shock_prob >= 0.25), ATR multiplier compresses from 2.5 to 1.2.
    """
    fsm = AdaptiveExitFSM()
    pos = PositionTracker(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=100.0,
        current_stop=101.75,
        initial_1r_dist=5.0,
        remaining_qty=4.0,  # Runner remainder
        stage=2,
    )
    # Price hits 110.5 (+2.10R)
    highest_20 = 111.0
    atr = 2.0

    # Normal Chandelier: highest_20 - 2.5 * ATR = 111.0 - 5.0 = 106.0
    act_normal = fsm.evaluate_bar(
        pos=pos,
        high=111.0,
        low=109.0,
        close=110.5,
        prev_high=108.0,
        prev_low=107.0,
        highest_20=highest_20,
        lowest_20=102.0,
        atr_1h=atr,
        shock_prob=0.05,
    )
    assert act_normal.action_type == "UPDATE_STOP"
    assert pos.current_stop == pytest.approx(106.0, rel=1e-5)
    assert pos.stage == 3

    # Shock condition triggers compression: multiplier 1.2 -> stop becomes 111.0 - 2.4 = 108.6
    act_shock = fsm.evaluate_bar(
        pos=pos,
        high=111.0,
        low=110.0,
        close=110.2,
        prev_high=111.0,
        prev_low=109.0,
        highest_20=highest_20,
        lowest_20=102.0,
        atr_1h=atr,
        shock_prob=0.35,  # Shock alert!
    )
    assert act_shock.action_type == "UPDATE_STOP"
    expected_compressed_stop = 111.0 - (1.2 * atr)  # 108.6
    assert pos.current_stop == pytest.approx(expected_compressed_stop, rel=1e-5)
