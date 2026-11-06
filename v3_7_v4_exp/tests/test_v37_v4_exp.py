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


@pytest.mark.asyncio
async def test_v37_v4_oms_status_and_limit_fill():
    cfg = ScalperConfigV37V4(equity_usdt=300.0)
    meta = InstrumentMeta(symbol="NEARUSDT", tick_size=0.01, qty_step=0.1, min_qty=0.1)
    state = SymbolMicrostructureStateV4("NEARUSDT", meta)
    state.mid_price = 4.0
    state.best_bid = 3.99
    state.best_ask = 4.01
    state.sorted_bids = [(3.99, 1000.0)]
    state.sorted_asks = [(4.01, 1000.0)]

    oms = ScalpOMSV4(cfg, {"NEARUSDT": state})

    intent = SignalIntent(
        engine_id="E2_SWEEP",
        priority=1,
        symbol="NEARUSDT",
        side="Buy",
        entry_price=3.99,
        sl_price=3.90,
        tp1_price=4.10,
        tp2_price=4.20,
        max_hold_sec=300.0,
    )
    ok = await oms.submit_signal(intent)
    assert ok is True
    assert "NEARUSDT" in oms.pending_orders

    # Simulate market trade eating the queue ahead of our limit buy order
    oms.on_market_trade(symbol="NEARUSDT", is_buy=False, price=3.99, qty=1050.0)

    # Order should be filled and moved to active positions
    assert "NEARUSDT" not in oms.pending_orders
    assert "NEARUSDT" in oms.active_positions

    st = oms.get_status()
    assert st["port"] == 8093
    assert st["active_positions_count"] == 1
    assert "NEARUSDT" in st["active_positions"]
    assert "NEARUSDT" in st["open_positions"]
    assert st["status"] == "RUNNING"


def test_v37_v4_z_ofi_entry_trigger():
    cfg = ScalperConfigV37V4(enable_e1_obi=True, min_ker_threshold=0.30, z_ofi_entry_threshold=2.0)
    meta = InstrumentMeta(symbol="NEARUSDT", tick_size=0.01, qty_step=0.1, min_qty=0.1, natr_5m=0.005)
    meta_btc = InstrumentMeta(symbol="BTCUSDT", tick_size=0.1, qty_step=0.001, min_qty=0.001)

    st_alt = SymbolMicrostructureStateV4("NEARUSDT", meta)
    st_btc = SymbolMicrostructureStateV4("BTCUSDT", meta_btc)

    st_alt.mid_price = 3.75
    st_alt.best_bid = 3.74
    st_alt.best_ask = 3.75
    st_alt.spread_ticks = 1.0

    st_btc.mid_price = 76000.0

    evaluator = ScalpEnginesV4(cfg)

    # 1. Z_OFI = +3.5sigma, KER = 0.50 -> Should fire LONG E1_ZOFI
    st_alt.get_z_ofi = lambda: 3.5
    st_alt.get_ker = lambda: 0.50

    sig = evaluator.evaluate_all(st_alt, st_btc, now_ms=1000)
    assert sig is not None
    assert sig.engine_id == "E1_ZOFI"
    assert sig.side == "Buy"
    assert "Z_OFI=+3.50σ" in sig.reason

    # 2. Z_OFI = +3.5sigma, but KER = 0.15 (chop) -> Should be blocked by KER gate
    st_alt.get_ker = lambda: 0.15
    sig_chop = evaluator.evaluate_all(st_alt, st_btc, now_ms=1000)
    assert sig_chop is None

    # 3. Z_OFI = -2.8sigma, KER = 0.60 -> Should fire SHORT E1_ZOFI
    st_alt.get_z_ofi = lambda: -2.8
    st_alt.get_ker = lambda: 0.60
    sig_short = evaluator.evaluate_all(st_alt, st_btc, now_ms=1000)
    assert sig_short is not None
    assert sig_short.engine_id == "E1_ZOFI"
    assert sig_short.side == "Sell"
    assert "Z_OFI=-2.80σ" in sig_short.reason

    # 4. Z_OFI = -2.8sigma, KER = 0.60, but Alt is pumping above VWAP/EMA9 -> SHORT Vetoed!
    st_alt.meta.vwap_session = 3.50  # Mid price is 3.75, well above VWAP
    st_alt.meta.ema9_5m = 3.60       # Mid price is above EMA9
    sig_veto = evaluator.evaluate_all(st_alt, st_btc, now_ms=1000)
    assert sig_veto is None  # Trend veto protects against counter-trend alt shorts!



@pytest.mark.asyncio
async def test_v37_v4_tp1_partial_accounting():
    cfg = ScalperConfigV37V4(equity_usdt=300.0)
    meta = InstrumentMeta(symbol="UAIUSDT", tick_size=0.0001, qty_step=1.0, min_qty=1.0)
    state = SymbolMicrostructureStateV4("UAIUSDT", meta)
    state.mid_price = 0.2085  # Above TP1 (0.2079)
    state.best_bid = 0.2084
    state.best_ask = 0.2086

    oms = ScalpOMSV4(cfg, {"UAIUSDT": state})

    pos = ActivePosition(
        symbol="UAIUSDT",
        engine_id="E1_ZOFI",
        side="Buy",
        qty=100.0,
        initial_qty=100.0,
        entry_price=0.2000,
        sl_price=0.1980,
        tp1_price=0.2079,
        tp2_price=0.2175,
        opened_ms=1000,
        max_hold_sec=300.0,
    )
    oms.active_positions["UAIUSDT"] = pos

    # 1. Step to trigger TP1
    await oms.monitor_step(now_ms=2000)

    assert pos.tp1_filled is True
    assert pos.qty == 50.0  # 50% left as runner
    assert pos.sl_price == pos.entry_price  # SL moved to breakeven
    assert len(oms.closed_trades_history) == 1
    tp1_record = oms.closed_trades_history[0]
    assert tp1_record["exit_reason"] == "TP1_50PCT_MAKER"
    assert tp1_record["net_pnl_usdt"] > 0
    assert oms.daily_pnl_usdt == pytest.approx(tp1_record["net_pnl_usdt"], abs=1e-4)

    # Status check
    st = oms.get_status()
    assert "UAIUSDT" in st["active_positions"]
    pos_stat = st["active_positions"]["UAIUSDT"]
    assert pos_stat["stop_loss"] == 0.2000
    assert pos_stat["sl_price"] == 0.2000
    assert pos_stat["take_profit_1"] == 0.2079
    assert pos_stat["tp1_price"] == 0.2079
    assert pos_stat["take_profit_2"] == 0.2175
    assert pos_stat["tp2_price"] == 0.2175
    assert "hold_sec" in pos_stat

    # 2. Price reaches TP2 -> Runner exit
    state.mid_price = 0.2180
    await oms.monitor_step(now_ms=5000)

    assert "UAIUSDT" not in oms.active_positions
    assert len(oms.closed_trades_history) == 2
    runner_record = oms.closed_trades_history[1]
    assert runner_record["exit_reason"] == "TP2_RUNNER_RUNNER"
    assert runner_record["net_pnl_usdt"] > 0

    # Trade Accounting Integrity: sum of trade records equals daily PnL
    total_trades_pnl = sum(t["net_pnl_usdt"] for t in oms.closed_trades_history)
    assert total_trades_pnl == pytest.approx(oms.daily_pnl_usdt, abs=1e-4)
