"""
Unit Tests for v3.7-v4-exp NewDragon Scalp Lab.
Verifies:
1. UniversalRisk minNotional Guard rejection.
2. 2-Stage Z_OFI Order Flow Imbalance Fast Scratch.
3. Dynamic Breakeven Ratchet (+0.35% -> BE+0.05%).
4. KER chop gate in ScalpEnginesV4.
"""

from decimal import Decimal
from pathlib import Path
import pytest
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from v3_7.src.microstructure import InstrumentMeta
from v3_7.src.scalp_engines import SignalIntent
from v3_7_v4_exp.src.config import ScalperConfigV37V4
from v3_7_v4_exp.src.microstructure_v4 import SymbolMicrostructureStateV4
from v3_7_v4_exp.src.scalp_engines_v4 import ScalpEnginesV4
from v3_7_v4_exp.src.scalp_oms_v4 import ScalpOMSV4, ActivePosition


def test_v37_v4_min_notional_guard():
    cfg = ScalperConfigV37V4(equity_usdt=300.0, risk_per_trade_pct=0.0050)
    meta = InstrumentMeta(
        symbol="BTCUSDT",
        tick_size=0.1,
        qty_step=0.001,
        min_qty=0.001,
    )
    state = SymbolMicrostructureStateV4("BTCUSDT", meta)
    state.mid_price = 70000.0
    state.best_bid = 69999.0
    state.best_ask = 70001.0
    state.sorted_bids = [(69999.0, 10.0)]
    state.sorted_asks = [(70001.0, 10.0)]

    oms = ScalpOMSV4(cfg, {"BTCUSDT": state})

    # Intent with tight stop where risk budget ($1.50) gives notional < $100
    intent = SignalIntent(
        engine_id="E2_SWEEP",
        priority=1,
        symbol="BTCUSDT",
        side="Buy",
        entry_price=70000.0,
        sl_price=68000.0,  # ~2.8% stop distance. Risk $1.50 / 0.028 = ~$53 notional (< $100 minNotional)
        tp1_price=70500.0,
        tp2_price=71500.0,
        max_hold_sec=300.0,
    )

    qty = oms.calculate_safe_qty(intent, state)
    # Must be 0.0 because notional < $100 minNotional!
    assert qty == 0.0


def test_v37_v4_ker_chop_gate():
    cfg = ScalperConfigV37V4(min_ker_threshold=0.30)
    evaluator = ScalpEnginesV4(cfg)

    meta = InstrumentMeta(symbol="SOLUSDT", tick_size=0.01, qty_step=0.01, min_qty=0.01)
    state = SymbolMicrostructureStateV4("SOLUSDT", meta)
    state.mid_price = 150.0
    state.current_ker = 0.15  # Choppy noise! KER < 0.30

    sig = evaluator.evaluate_engine2_sweeps_reversion(state, now_ms=1000)
    assert sig is None  # Blocked by KER chop gate


@pytest.mark.asyncio
async def test_v37_v4_z_ofi_fast_scratch_critical():
    cfg = ScalperConfigV37V4(
        equity_usdt=300.0,
        enable_z_ofi_scratch=True,
        z_ofi_scratch_threshold=1.5,
        z_ofi_critical_threshold=2.5,
        z_ofi_window_sec=15.0,
    )
    meta = InstrumentMeta(symbol="SOLUSDT", tick_size=0.01, qty_step=0.01, min_qty=0.01)
    state = SymbolMicrostructureStateV4("SOLUSDT", meta)
    state.mid_price = 150.0
    state.best_bid = 149.99
    state.best_ask = 150.01
    state.current_z_ofi = -2.8  # Critical adverse flow against LONG!

    oms = ScalpOMSV4(cfg, {"SOLUSDT": state})

    pos = ActivePosition(
        symbol="SOLUSDT",
        engine_id="E2_SWEEP",
        side="Buy",
        qty=1.0,
        initial_qty=1.0,
        entry_price=150.0,
        sl_price=147.0,
        tp1_price=151.0,
        tp2_price=153.0,
        opened_ms=1000,
        max_hold_sec=300.0,
    )
    oms.active_positions["SOLUSDT"] = pos

    # Monitor step at 2s into the trade (within 15s window)
    await oms.monitor_step(now_ms=3000)

    # Position must be closed via OFI_FAST_SCRATCH
    assert "SOLUSDT" not in oms.active_positions
    assert oms.scratch_count == 1
    assert len(oms.closed_trades_history) == 1
    assert oms.closed_trades_history[0]["reason"] == "OFI_FAST_SCRATCH"


@pytest.mark.asyncio
async def test_v37_v4_dynamic_breakeven_ratchet():
    cfg = ScalperConfigV37V4(
        equity_usdt=300.0,
        dynamic_breakeven_pct=0.0035,
    )
    meta = InstrumentMeta(symbol="SOLUSDT", tick_size=0.01, qty_step=0.01, min_qty=0.01)
    state = SymbolMicrostructureStateV4("SOLUSDT", meta)
    state.mid_price = 150.60  # +0.40% profit (> 0.35%)
    state.best_bid = 150.59
    state.best_ask = 150.61
    state.current_z_ofi = 0.5  # Friendly flow

    oms = ScalpOMSV4(cfg, {"SOLUSDT": state})

    pos = ActivePosition(
        symbol="SOLUSDT",
        engine_id="E2_SWEEP",
        side="Buy",
        qty=1.0,
        initial_qty=1.0,
        entry_price=150.0,
        sl_price=147.0,
        tp1_price=151.5,
        tp2_price=153.0,
        opened_ms=1000,
        max_hold_sec=300.0,
    )
    oms.active_positions["SOLUSDT"] = pos

    await oms.monitor_step(now_ms=5000)

    # Stop loss must be ratcheted to BE + 0.05% (150.075)
    assert pos.is_breakeven_set is True
    assert pos.sl_price > 150.0
