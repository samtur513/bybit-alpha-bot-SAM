import asyncio
import os
import pytest
from v3_10_sweep.tca_ledger import ShadowTCALedger, TCATradeRecord
from v3_10_sweep.sweep_8098 import LiquiditySweepEngine8098, SweepTracker


@pytest.mark.asyncio
async def test_tca_mathematical_conservation():
    db_file = "test_tca_ledger.db"
    if os.path.exists(db_file):
        os.remove(db_file)

    ledger = ShadowTCALedger(db_path=db_file, fee_maker=0.000324, fee_taker=0.00090)
    await ledger.init_db()

    # 1. Test LONG trade
    rec_long = TCATradeRecord(
        trade_id="test_long_01",
        module_id="M3_SWEEP_8098",
        sub_strategy="SWEEP_RECLAIM_LONG",
        symbol="SOLUSDT",
        side="Buy",
        opened_at_ms=1000,
        closed_at_ms=5000,
        sweep_atr_depth=0.35,
        compression_ratio=2.2,
        touches_count=2,
        absorption_k=3.1,
        cvd_flip_tfi=0.45,
        signal_mid_price=150.0,
        entry_fill_price=149.95,  # Maker limit filled below mid (+0.05 spread capture)
        exit_mid_price=153.0,
        exit_fill_price=153.05,   # Maker limit filled above mid (+0.05 spread capture)
        qty=2.0,
        entry_is_maker=True,
        exit_is_maker=True,
        funding_cashflow_usdt=0.0,
        mae_pct=0.1,
        mfe_pct=2.1,
        exit_reason="TP_VWAP_TARGET",
    )
    res_long = await ledger.record_closed_trade(rec_long)

    # Pure Alpha: +1.0 * (153.0 - 150.0) * 2.0 = +6.0 USDT
    assert pytest.approx(res_long["pure_alpha"], 1e-4) == 6.0
    # Exec Capture: (150.0 - 149.95)*2 + (153.05 - 153.0)*2 = 0.10 + 0.10 = +0.20 USDT
    assert pytest.approx(res_long["exec_alpha"], 1e-4) == 0.20
    # True raw price change: (153.05 - 149.95) * 2.0 = +6.20 USDT
    assert pytest.approx(res_long["pure_alpha"] + res_long["exec_alpha"], 1e-4) == 6.20

    # 2. Test SHORT trade
    rec_short = TCATradeRecord(
        trade_id="test_short_01",
        module_id="M3_SWEEP_8098",
        sub_strategy="SWEEP_RECLAIM_SHORT",
        symbol="BTCUSDT",
        side="Sell",
        opened_at_ms=6000,
        closed_at_ms=10000,
        sweep_atr_depth=0.40,
        compression_ratio=2.5,
        touches_count=1,
        absorption_k=2.8,
        cvd_flip_tfi=-0.38,
        signal_mid_price=95000.0,
        entry_fill_price=95010.0,  # Sold higher than mid (+10 spread capture)
        exit_mid_price=94000.0,
        exit_fill_price=93990.0,   # Bought back lower than mid (+10 spread capture)
        qty=0.05,
        entry_is_maker=True,
        exit_is_maker=True,
        funding_cashflow_usdt=0.0,
        mae_pct=0.05,
        mfe_pct=1.1,
        exit_reason="TP_VWAP_TARGET",
    )
    res_short = await ledger.record_closed_trade(rec_short)

    # Pure Alpha: -1.0 * (94000 - 95000) * 0.05 = +50.0 USDT
    assert pytest.approx(res_short["pure_alpha"], 1e-4) == 50.0
    # Exec Capture: (95010 - 95000)*0.05 + (94000 - 93990)*0.05 = 0.50 + 0.50 = +1.0 USDT
    assert pytest.approx(res_short["exec_alpha"], 1e-4) == 1.0

    # 3. Test Missed Intent
    missed = await ledger.record_missed_intent(
        module_id="M3_SWEEP_8098",
        symbol="ETHUSDT",
        side="Buy",
        created_ms=12000,
        limit_price=3100.0,
        signal_mid=3100.0,
        mid_after_60s=3115.0,
        qty=0.5,
    )
    # Virtual missed: (3115 - 3100) * 0.5 = 7.5 USDT
    assert pytest.approx(missed, 1e-4) == 7.5

    # Summary stats
    stats = await ledger.get_summary_stats("M3_SWEEP_8098")
    assert stats["total_trades"] == 2
    assert stats["win_rate_pct"] == 100.0
    assert stats["missed_intents_count"] == 1
    assert stats["missed_alpha_usdt"] == 7.5

    if os.path.exists(db_file):
        os.remove(db_file)


def test_anti_compression_gate_rejection():
    engine = LiquiditySweepEngine8098()

    # Tick where price breaks below support, but touches > 3 (should be rejected)
    tick_too_many_touches = {
        "ts": 1000000,
        "symbol": "SOLUSDT",
        "bid": 99.4,
        "ask": 99.5,
        "mid": 99.45,
        "atr_15m": 2.0,
        "swing_low": 100.0,
        "swing_high": 110.0,
        "low_touches": 4,  # > 3 -> True breakout risk!
        "high_touches": 2,
        "low_comp_ratio": 2.5,
        "high_comp_ratio": 2.5,
        "tfi_3s": 0.4,
        "sell_3s": 100.0,
        "buy_3s": 10.0,
        "avg_1m_vol": 50.0,
        "tick_size": 0.01,
        "qty_step": 0.1,
        "min_qty": 0.1,
        "top3_bid_notional": 50000.0,
        "top3_ask_notional": 50000.0,
        "vwap": 105.0,
        "obi": 0.2,
    }
    engine._evaluate_anti_compression_and_sweep(tick_too_many_touches)
    assert not engine.trackers.get("SOLUSDT", SweepTracker()).active

    # Tick where price is compressed right on support (< 1.8 ATR)
    tick_compressed = dict(tick_too_many_touches)
    tick_compressed["low_touches"] = 2
    tick_compressed["low_comp_ratio"] = 1.2  # < 1.8 -> compression base before breakdown!
    engine._evaluate_anti_compression_and_sweep(tick_compressed)
    assert not engine.trackers.get("SOLUSDT", SweepTracker()).active

    # Tick with clean Long sweep (> 1.8 ATR, <= 3 touches, depth < 0.75 ATR)
    tick_clean_sweep = dict(tick_too_many_touches)
    tick_clean_sweep["low_touches"] = 2
    tick_clean_sweep["low_comp_ratio"] = 2.2
    tick_clean_sweep["mid"] = 99.4  # depth = 0.60 / 2.0 = 0.30 ATR
    engine._evaluate_anti_compression_and_sweep(tick_clean_sweep)
    tr = engine.trackers.get("SOLUSDT")
    assert tr is not None and tr.active and tr.side == "Buy"


def test_short_sweep_detection_and_reclaim():
    engine = LiquiditySweepEngine8098()

    tick_sweep_high = {
        "ts": 1000000,
        "symbol": "ETHUSDT",
        "bid": 3105.0,
        "ask": 3105.5,
        "mid": 3105.25,
        "atr_15m": 20.0,
        "swing_low": 2900.0,
        "swing_high": 3100.0,
        "low_touches": 2,
        "high_touches": 2,
        "low_comp_ratio": 3.0,
        "high_comp_ratio": 2.5,  # >= 1.8 ATR
        "tfi_3s": 0.5,
        "sell_3s": 10.0,
        "buy_3s": 150.0,
        "avg_1m_vol": 20.0,
        "tick_size": 0.05,
        "qty_step": 0.01,
        "min_qty": 0.01,
        "top3_bid_notional": 100000.0,
        "top3_ask_notional": 100000.0,
        "vwap": 3050.0,
        "obi": 0.3,
    }
    # Price breaks above swing high 3100 to 3105.25 (depth = 5.25 / 20 = 0.26 ATR)
    engine._evaluate_anti_compression_and_sweep(tick_sweep_high)
    tr = engine.trackers.get("ETHUSDT")
    assert tr is not None and tr.active and tr.side == "Sell"
    assert tr.extreme_price == 3105.25
