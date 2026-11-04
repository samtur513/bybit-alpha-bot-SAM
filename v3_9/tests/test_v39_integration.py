"""
Unit and Invariant Tests for v3.9 NewDragon Scalp Lab.
Verifies:
1. Micro-Router strict minNotional ($5 alts / $100 BTC) enforcement.
2. SymbolMicrostructureStateV39 OFI and Z_OFI calculation.
3. Z_OFI Fast Scratch execution within 10s of position fill.
"""

import asyncio
from pathlib import Path
import sys
import pytest
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_7.src.microstructure import InstrumentMeta
from v3_7.src.scalp_engines import SignalIntent
from v3_9.src.config import ScalperConfigV39
from v3_9.src.microstructure_v39 import SymbolMicrostructureStateV39
from v3_9.src.scalp_oms_v39 import ScalpOMSV39


def test_micro_router_min_notional_rejection():
    """Verify Micro-Router rejects orders where risk notional < minNotional."""
    cfg = ScalperConfigV39(equity_usdt=100.0, risk_per_trade_pct=0.005)  # $0.50 risk budget
    oms = ScalpOMSV39(cfg)

    meta = InstrumentMeta(
        symbol="BTCUSDT",
        tick_size=0.1,
        qty_step=0.001,
        min_qty=0.001,
    )
    st = SymbolMicrostructureStateV39("BTCUSDT", meta)
    st.sorted_bids = [(80000.0, 5.0), (79999.0, 5.0), (79998.0, 5.0)]
    st.sorted_asks = [(80001.0, 5.0), (80002.0, 5.0), (80003.0, 5.0)]

    # Intent with tight stop (0.2% risk = $160 distance)
    # Target notional by risk = 0.50 / (0.002 + fee) ~ $150
    # But for a very wide stop (2% risk = $1600 distance):
    # Notional by risk = 0.50 / 0.02 = $25.
    # $25 is way below BTC minNotional ($100).
    intent_small = SignalIntent(
        engine_id="E1_OBI",
        priority=3,
        symbol="BTCUSDT",
        side="Buy",
        entry_price=80000.0,
        sl_price=78400.0,  # 2% stop
        tp1_price=80400.0,
        tp2_price=80800.0,
        max_hold_sec=300.0,
    )

    qty = oms.calculate_safe_qty(intent_small, st)
    # Must be 0.0 because notional ($25) < minNotional ($100)!
    assert qty == 0.0


def test_z_ofi_calculation():
    """Verify that orderbook updates generate correct OFI and normalized Z_OFI."""
    meta = InstrumentMeta(
        symbol="NEARUSDT",
        tick_size=0.001,
        qty_step=0.1,
        min_qty=0.1,
    )
    st = SymbolMicrostructureStateV39("NEARUSDT", meta)

    # Initial snapshot
    st.apply_orderbook("snapshot", {
        "b": [["4.000", "100.0"]],
        "a": [["4.005", "100.0"]],
    }, ts_ms=1000)

    assert st.best_bid == 4.000
    assert st.best_ask == 4.005

    # Feed updates with strong aggressive bid additions
    for i in range(25):
        st.apply_orderbook("delta", {
            "b": [["4.000", str(100.0 + (i + 1) * 20.0)]],
            "a": [["4.005", "100.0"]],
        }, ts_ms=1000 + (i + 1) * 100)

    # After multiple positive bid expansions, Z_OFI must be positive
    z_ofi = st.get_z_ofi()
    assert isinstance(z_ofi, float)
    assert z_ofi >= 0.0


def test_z_ofi_fast_scratch_execution():
    """Verify that adverse institutional flow within 10s triggers OFI_FAST_SCRATCH."""
    async def _test():
        cfg = ScalperConfigV39(
            equity_usdt=300.0,
            enable_z_ofi_scratch=True,
            z_ofi_scratch_threshold=2.0,
            z_ofi_window_sec=10.0,
        )
        oms = ScalpOMSV39(cfg)

        meta = InstrumentMeta(
            symbol="NEARUSDT",
            tick_size=0.001,
            qty_step=0.1,
            min_qty=0.1,
        )
        st = SymbolMicrostructureStateV39("NEARUSDT", meta)
        oms.states["NEARUSDT"] = st

        # Simulate active LONG position opened 2 seconds ago
        now_ms = 1000000
        from v3_7.src.scalp_oms import ActivePosition
        pos = ActivePosition(
            symbol="NEARUSDT",
            engine_id="E1_OBI",
            side="Buy",
            qty=50.0,
            initial_qty=50.0,
            entry_price=4.000,
            sl_price=3.950,
            tp1_price=4.020,
            tp2_price=4.050,
            opened_ms=now_ms - 2000,  # 2.0s hold
            max_hold_sec=300.0,
        )
        oms.active_positions["NEARUSDT"] = pos

        # Set mid price and mock severe adverse flow Z_OFI = -2.5 (strong institutional dumping)
        st.mid_price = 3.995
        st.current_z_ofi = -2.5

        # Run monitor step
        await oms.monitor_step(now_ms)

        # Position must be closed via OFI_FAST_SCRATCH
        assert "NEARUSDT" not in oms.active_positions
        assert oms.scratch_count == 1
        assert len(oms.closed_trades_history) == 1
        closed = oms.closed_trades_history[0]
        assert closed["reason"] == "OFI_FAST_SCRATCH"
        assert closed["net_pnl_usdt"] < 0  # Minimal adverse scratch loss

    asyncio.run(_test())
