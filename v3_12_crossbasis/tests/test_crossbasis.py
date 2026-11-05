import asyncio
import pytest
import time
from v3_12_crossbasis.crossbasis_8096 import CrossBasisEngine8096, SpotState


def test_cross_basis_setup_evaluation():
    engine = CrossBasisEngine8096()

    # 1. Spot leads with aggressive buying: Spot = 100.10, Perp = 100.00 -> Basis = +10.0 bps
    spot_st = SpotState(symbol="SOLUSDT")
    spot_st.mid_price = 100.10
    spot_st.tfi_3s = 0.50
    engine.spot_states["SOLUSDT"] = spot_st

    perp_tick = {
        "ts": int(time.time() * 1000),
        "symbol": "SOLUSDT",
        "bid": 99.98,
        "ask": 100.02,
        "mid": 100.00,
        "tfi_3s": 0.05,  # Perp is lagging!
        "tick_size": 0.01,
        "qty_step": 0.1,
        "min_qty": 0.1,
    }

    engine._evaluate_cross_basis_setup(perp_tick)
    assert "SOLUSDT" in engine.pending_intents
    intent = engine.pending_intents["SOLUSDT"]
    assert intent.side == "Buy"
    assert intent.position_idx == 1
    assert intent.basis_bps > 7.0
