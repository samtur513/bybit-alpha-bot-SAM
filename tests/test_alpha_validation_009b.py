"""
Unit Tests for Native Alpha Signal Validation & Control Scenarios (FLEET-REPAIR-009B Step 2).
"""

import asyncio
from decimal import Decimal
import time
import pytest

from tools.verify_true_alpha_validation_009b import TrueAlphaValidator009B
from v3_10_sweep.sweep_8098 import LiquiditySweepEngine8098, PendingLimitIntent, SweepTracker


@pytest.mark.asyncio
async def test_control_scenarios_native_v310():
    """Verify that native v3.10 engine detects positive controls and blocks negative controls."""
    validator = TrueAlphaValidator009B()
    controls = await validator.run_control_scenarios()

    # 1. Long Intent Verification
    il = controls["intent_long"]
    assert isinstance(il, PendingLimitIntent)
    assert il.symbol == "ETHUSDT"
    assert il.side == "Buy"
    assert il.position_idx == 1
    assert il.limit_price == 2500.05
    assert il.sl_price == 2488.00
    assert il.qty > 0

    # 2. Short Intent Verification
    is_ = controls["intent_short"]
    assert isinstance(is_, PendingLimitIntent)
    assert is_.symbol == "SOLUSDT"
    assert is_.side == "Sell"
    assert is_.position_idx == 2
    assert is_.limit_price == 141.99
    assert is_.sl_price == 143.00
    assert is_.qty > 0

    # 3. Negative Controls Verification
    negs = controls["neg_results"]
    assert negs["compression_fail"] is True
    assert negs["depth_exceeded"] is True
    assert negs["timeout_exceeded"] is True
    assert negs["flow_fail"] is True
    assert negs["btc_violent_filter"] is True


@pytest.mark.asyncio
async def test_replay_truthful_calm_stream():
    """Verify that replaying calm historical data truthfully produces zero false signals."""
    validator = TrueAlphaValidator009B()
    await validator.replay_reproducible_market_data()

    assert len(validator.v310_engine.pending_intents) == 0
    assert len(validator.v311_signals) == 0
    assert len(validator.v312_signals) == 0

    # Verify metrics are within calm regime bounds
    assert validator.v310_max_depth_atr < 0.15  # Did not breach swing level into valid sweep zone
    assert validator.v311_max_fr_bps < 3.0       # Did not meet 3.0 bps threshold
    assert validator.v312_max_basis_bps < 25.0   # Did not meet 25.0 bps basis dislocation threshold
