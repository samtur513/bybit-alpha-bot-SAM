import asyncio
import pytest
import time
from v3_11_funding.funding_8097 import FundingSqueezeEngine8097, FundingCandidate, OpenFundingPosition


def test_funding_setup_evaluation():
    engine = FundingSqueezeEngine8097()

    now_ms = int(time.time() * 1000)
    # Candidate with negative funding (shorts pay longs 0.08%), settlement in 10 minutes (600s)
    engine.funding_candidates["SOLUSDT"] = FundingCandidate(
        symbol="SOLUSDT",
        funding_rate=-0.0008,
        next_funding_time_ms=now_ms + 600_000,
        open_interest=500_000,
        predicted_rate=-0.0008,
        time_to_settle_sec=600.0,
        action="LONG_SQUEEZE",
    )

    # Tick where flow contradicts (TFI < 0.10) -> Should reject!
    tick_flow_bad = {
        "ts": now_ms,
        "symbol": "SOLUSDT",
        "bid": 99.9,
        "ask": 100.0,
        "mid": 99.95,
        "vwap": 99.0,
        "tfi_3s": -0.20,  # Negative flow -> reject!
        "tick_size": 0.01,
        "qty_step": 0.1,
        "min_qty": 0.1,
        "atr_15m": 0.5,
    }
    engine._evaluate_funding_setup(tick_flow_bad)
    assert "SOLUSDT" not in engine.pending_intents

    # Tick with bullish flow confirmation (TFI >= +0.10 and mid > vwap) -> Should arm!
    tick_flow_good = dict(tick_flow_bad)
    tick_flow_good["tfi_3s"] = 0.35
    engine._evaluate_funding_setup(tick_flow_good)
    assert "SOLUSDT" in engine.pending_intents
    intent = engine.pending_intents["SOLUSDT"]
    assert intent.side == "Buy"
    assert intent.position_idx == 1
    assert intent.limit_price == 99.9


@pytest.mark.asyncio
async def test_clearing_dump_defense_eject():
    engine = FundingSqueezeEngine8097()
    await engine.ledger.init_db()
    now_ms = int(time.time() * 1000)

    # Open position with 800 ms until funding settlement (<= 1500 ms)
    pos = OpenFundingPosition(
        trade_id="test_eject_01",
        symbol="BTCUSDT",
        side="Buy",
        position_idx=1,
        qty=0.1,
        signal_mid=90000.0,
        entry_fill=90000.0,
        sl_price=89000.0,
        tp_price=92000.0,
        opened_ms=now_ms - 60000,
        next_funding_ms=now_ms + 800,  # 0.8s remaining!
        funding_rate=-0.0006,
        sl_order_link_id="test_sl",
        min_price=90000.0,
        max_price=90500.0,
    )
    engine.open_positions["BTCUSDT"] = pos
    engine.latest_ticks["BTCUSDT"] = {"mid": 90400.0, "tick_size": 0.1}

    # Run one pass of execution loop
    task = asyncio.create_task(engine.execution_and_clearing_eject_loop())
    await asyncio.sleep(0.08)
    task.cancel()

    # Position must be closed via CLEARING_EJECT_T_MINUS_1S!
    assert "BTCUSDT" not in engine.open_positions
    assert engine.trades_count == 1
