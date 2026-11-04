"""
Logic & Validation Tests for v3.7 Scalp Strategy Engines
"""

import pytest
from v3_7.src.config import ScalperConfig
from v3_7.src.microstructure import InstrumentMeta, SymbolMicrostructureState
from v3_7.src.scalp_engines import ThreeEnginesEvaluate


@pytest.fixture
def cfg() -> ScalperConfig:
    return ScalperConfig()


@pytest.fixture
def btc_meta() -> InstrumentMeta:
    return InstrumentMeta(
        symbol="BTCUSDT",
        tick_size=0.1,
        qty_step=0.001,
        min_qty=0.001,
        natr_5m=0.0035,
        atr_5m=250.0,
        atr_15m=400.0,
        hurst_15m=0.48,
        er_15m=0.55,
        ema20_15m=65000.0,
        beta_btc=1.0,
        corr_btc=1.0,
    )


@pytest.fixture
def alt_meta() -> InstrumentMeta:
    return InstrumentMeta(
        symbol="SOLUSDT",
        tick_size=0.01,
        qty_step=0.1,
        min_qty=0.1,
        natr_5m=0.0060,
        atr_5m=0.90,
        atr_15m=1.60,
        hurst_15m=0.41,
        er_15m=0.46,
        ema9_5m=151.0,
        ema21_5m=149.5,
        ema20_15m=149.0,
        ema50_4h=145.0,
        vwap_session=150.0,
        swing_low_1h=148.00,
        swing_high_1h=154.00,
        beta_btc=1.75,
        corr_btc=0.85,
    )


def test_e1_obi_breakout_long_and_short(cfg, alt_meta):
    evaluator = ThreeEnginesEvaluate(cfg)
    state = SymbolMicrostructureState("SOLUSDT", alt_meta)
    now_ms = 200000

    # 1. LONG TEST
    state.apply_orderbook("snapshot", {
        "b": [["151.20", "500.0"], ["151.19", "300.0"]],
        "a": [["151.21", "50.0"], ["151.22", "60.0"]],
    }, now_ms - 500)

    # 60с история
    for t in range(now_ms - 50000, now_ms - 5000, 2000):
        state.apply_trades([{"T": t, "S": "Buy", "p": 151.0, "v": 10.0}])

    # Импульс покупок за 2 сек
    state.apply_trades([
        {"T": now_ms - 800, "S": "Buy", "p": 151.21, "v": 250.0},
        {"T": now_ms - 200, "S": "Buy", "p": 151.21, "v": 250.0},
    ])
    # Разъедание Ask (дельта: уровень 151.21 съеден)
    state.apply_orderbook("delta", {"a": [["151.21", "0.0"], ["151.22", "10.0"]]}, now_ms)

    sig_long = evaluator.evaluate_engine1_obi_breakout(state, now_ms)
    assert sig_long is not None
    assert sig_long.engine_id == "E1_OBI"
    assert sig_long.side == "Buy"
    assert sig_long.entry_price == pytest.approx(151.21, abs=0.011)
    assert sig_long.tp1_price > sig_long.entry_price
    assert sig_long.sl_price < sig_long.entry_price

    # 2. SHORT TEST (медвежий тренд и всплеск продаж)
    alt_meta_short = InstrumentMeta(
        symbol="SOLUSDT",
        tick_size=0.01,
        qty_step=0.1,
        min_qty=0.1,
        natr_5m=0.0060,
        atr_5m=0.90,
        atr_15m=1.60,
        hurst_15m=0.41,
        er_15m=0.46,
        ema9_5m=148.5,
        ema21_5m=150.0,
        ema20_15m=151.0,
        ema50_4h=155.0,
        vwap_session=150.0,
        swing_low_1h=145.0,
        swing_high_1h=154.0,
        beta_btc=1.75,
        corr_btc=0.85,
    )
    state_short = SymbolMicrostructureState("SOLUSDT", alt_meta_short)
    state_short.apply_orderbook("snapshot", {
        "b": [["148.00", "50.0"], ["147.99", "60.0"]],
        "a": [["148.01", "500.0"], ["148.02", "300.0"]],
    }, now_ms - 500)

    for t in range(now_ms - 50000, now_ms - 5000, 2000):
        state_short.apply_trades([{"T": t, "S": "Sell", "p": 149.0, "v": 10.0}])

    state_short.apply_trades([
        {"T": now_ms - 800, "S": "Sell", "p": 148.00, "v": 250.0},
        {"T": now_ms - 200, "S": "Sell", "p": 148.00, "v": 250.0},
    ])
    state_short.apply_orderbook("delta", {"b": [["148.00", "0.0"], ["147.99", "10.0"]]}, now_ms)

    sig_short = evaluator.evaluate_engine1_obi_breakout(state_short, now_ms)
    assert sig_short is not None
    assert sig_short.engine_id == "E1_OBI"
    assert sig_short.side == "Sell"
    assert sig_short.entry_price == pytest.approx(148.00, abs=0.011)
    assert sig_short.tp1_price < sig_short.entry_price
    assert sig_short.sl_price > sig_short.entry_price


def test_e2_liquidity_sweep_long_reclaim(cfg, alt_meta, btc_meta):
    evaluator = ThreeEnginesEvaluate(cfg)
    alt_state = SymbolMicrostructureState("SOLUSDT", alt_meta)
    btc_state = SymbolMicrostructureState("BTCUSDT", btc_meta)
    now_ms = 300000

    # BTC спокоен
    btc_state.apply_orderbook("snapshot", {"b": [["65200.0", "10.0"]], "a": [["65200.1", "10.0"]]}, now_ms - 5000)
    btc_state.apply_orderbook("snapshot", {"b": [["65205.0", "10.0"]], "a": [["65205.1", "10.0"]]}, now_ms)

    # 1. Фоновая история объема (до закола)
    for t in range(now_ms - 50000, now_ms - 20000, 3000):
        alt_state.apply_trades([{"T": t, "S": "Buy", "p": 148.0, "v": 20.0}])

    # 2. Закол swing_low_1h (148.00): падение до 147.60 с сильным поглощением
    alt_state.apply_trades([
        {"T": now_ms - 15000, "S": "Sell", "p": 147.80, "v": 600.0},
        {"T": now_ms - 10000, "S": "Sell", "p": 147.60, "v": 1200.0},
    ])
    assert alt_state.sweep_low_active is True

    # 3. Возврат цены выше 148.00 (Reclaim) до 148.05
    alt_state.apply_orderbook("snapshot", {
        "b": [["148.04", "400.0"], ["148.03", "300.0"]],
        "a": [["148.05", "100.0"], ["148.06", "100.0"]],
    }, now_ms)

    # 4. Покупки на возврате
    alt_state.apply_trades([
        {"T": now_ms - 1500, "S": "Buy", "p": 148.02, "v": 150.0},
        {"T": now_ms - 500, "S": "Buy", "p": 148.04, "v": 200.0},
    ])

    sig = evaluator.evaluate_engine2_liquidity_sweep(alt_state, btc_state, now_ms)
    assert sig is not None
    assert sig.engine_id == "E2_SWEEP"
    assert sig.side == "Buy"
    assert sig.entry_price == 148.00
    assert sig.priority == 1
    assert sig.tp1_price >= 148.00 * 1.0050


def test_e3_lead_lag_beta_catchup_long(cfg, alt_meta, btc_meta):
    evaluator = ThreeEnginesEvaluate(cfg)
    alt_state = SymbolMicrostructureState("SOLUSDT", alt_meta)
    btc_state = SymbolMicrostructureState("BTCUSDT", btc_meta)
    now_ms = 400000

    # BTC совершает резкий импульс +0.35% за 2.5 сек
    btc_p0 = 65000.0
    btc_p1 = 65230.0  # +0.353%
    btc_state.apply_orderbook("snapshot", {"b": [[str(btc_p0), "10.0"]], "a": [[str(btc_p0 + 0.1), "10.0"]]}, now_ms - 2500)
    btc_state.apply_orderbook("snapshot", {"b": [[str(btc_p1), "15.0"]], "a": [[str(btc_p1 + 0.1), "15.0"]]}, now_ms)
    btc_state.apply_trades([
        {"T": now_ms - 1000, "S": "Buy", "p": btc_p1, "v": 45.0},
        {"T": now_ms - 200, "S": "Buy", "p": btc_p1, "v": 50.0},
    ])

    # Альткоин (Бета = 1.75) отыграл только +0.05%
    alt_p0 = 150.00
    alt_p1 = 150.08
    alt_state.apply_orderbook("snapshot", {"b": [[str(alt_p0), "200.0"]], "a": [[str(alt_p0 + 0.01), "200.0"]]}, now_ms - 2500)
    alt_state.apply_orderbook("snapshot", {"b": [[str(alt_p1), "250.0"]], "a": [[str(alt_p1 + 0.01), "200.0"]]}, now_ms)

    sig = evaluator.evaluate_engine3_lead_lag(alt_state, btc_state, now_ms)
    assert sig is not None
    assert sig.engine_id == "E3_LEADLAG"
    assert sig.side == "Buy"
    assert sig.priority == 2
    assert sig.entry_price == alt_p1
    assert sig.tp1_price > sig.entry_price * 1.0038
