"""
Unit & Invariant Tests for v3.7 Microstructure Engine
"""

import math
import numpy as np
import pytest

from v3_7.src.microstructure import InstrumentMeta, SymbolMicrostructureState


@pytest.fixture
def mock_meta() -> InstrumentMeta:
    return InstrumentMeta(
        symbol="SOLUSDT",
        tick_size=0.01,
        qty_step=0.1,
        min_qty=0.1,
        natr_5m=0.0055,
        atr_5m=0.85,
        atr_15m=1.45,
        hurst_15m=0.42,
        er_15m=0.45,
        ema9_5m=150.5,
        ema21_5m=149.8,
        ema20_15m=149.0,
        ema50_4h=145.0,
        vwap_session=150.2,
        swing_low_1h=148.50,
        swing_high_1h=153.50,
        beta_btc=1.65,
        corr_btc=0.82,
    )


def test_orderbook_snapshot_and_delta(mock_meta):
    state = SymbolMicrostructureState("SOLUSDT", mock_meta)

    # 1. Snapshot
    snapshot_data = {
        "b": [["150.00", "100.0"], ["149.99", "250.0"], ["149.98", "400.0"]],
        "a": [["150.01", "120.0"], ["150.02", "300.0"], ["150.03", "500.0"]],
    }
    state.apply_orderbook("snapshot", snapshot_data, 1000)

    assert state.best_bid == 150.00
    assert state.best_ask == 150.01
    assert state.mid_price == 150.005
    assert state.spread_ticks == pytest.approx(1.0, abs=1e-5)
    assert len(state.sorted_bids) == 3
    assert len(state.sorted_asks) == 3

    # 2. Delta update (изменение объема и удаление уровня)
    delta_data = {
        "b": [["150.00", "80.0"], ["149.98", "0.0"], ["150.005", "50.0"]],
        "a": [["150.01", "0.0"], ["150.02", "150.0"]],
    }
    state.apply_orderbook("delta", delta_data, 1100)

    assert state.best_bid == 150.005
    assert state.best_ask == 150.02
    assert 149.98 not in state.bids
    assert 150.01 not in state.asks


def test_obi_calculation_symmetry(mock_meta):
    state = SymbolMicrostructureState("SOLUSDT", mock_meta)

    # Симметричный стакан: OBI должен быть равен 0.0
    snapshot_data = {
        "b": [[str(150.0 - i * 0.01), "100.0"] for i in range(10)],
        "a": [[str(150.01 + i * 0.01), "100.0"] for i in range(10)],
    }
    state.apply_orderbook("snapshot", snapshot_data, 1000)
    assert state.obi == pytest.approx(0.0, abs=1e-6)

    # Асимметричный стакан (преобладание покупателей)
    snapshot_data["b"][0][1] = "500.0"
    state.apply_orderbook("snapshot", snapshot_data, 1100)
    assert state.obi > 0.30

    # Асимметричный стакан (преобладание продавцов)
    snapshot_data["b"][0][1] = "50.0"
    snapshot_data["a"][0][1] = "600.0"
    state.apply_orderbook("snapshot", snapshot_data, 1200)
    assert state.obi < -0.30


def test_trade_flow_metrics_and_tfi(mock_meta):
    state = SymbolMicrostructureState("SOLUSDT", mock_meta)
    now_ms = 100000

    # Генерируем поток сделок: 80% покупок
    trades = [
        {"T": now_ms - 1500, "S": "Buy", "p": 150.01, "v": 20.0},
        {"T": now_ms - 1000, "S": "Buy", "p": 150.02, "v": 30.0},
        {"T": now_ms - 500, "S": "Sell", "p": 150.00, "v": 10.0},
        {"T": now_ms - 200, "S": "Buy", "p": 150.02, "v": 30.0},
    ]
    state.apply_trades(trades)

    tfi, buy_win, sell_win, avg_buy, avg_sell = state.get_trade_flow_metrics(now_ms, 2000)
    assert buy_win == 80.0
    assert sell_win == 10.0
    expected_tfi = (80.0 - 10.0) / 90.0
    assert tfi == pytest.approx(expected_tfi, abs=1e-4)


def test_liquidity_sweep_tracking(mock_meta):
    state = SymbolMicrostructureState("SOLUSDT", mock_meta)
    swing_l = mock_meta.swing_low_1h  # 148.50
    swing_h = mock_meta.swing_high_1h # 153.50

    # 1. Закол снизу (Long Sweep)
    trades_low = [
        {"T": 1000, "S": "Sell", "p": 148.40, "v": 50.0},
        {"T": 1500, "S": "Sell", "p": 148.20, "v": 120.0},
    ]
    state.apply_trades(trades_low)

    assert state.sweep_low_active is True
    assert state.sweep_min_price == 148.20
    assert state.sweep_sell_vol == 170.0

    # 2. Сброс свипа
    state.reset_sweep("long")
    assert state.sweep_low_active is False
    assert state.sweep_min_price == 0.0

    # 3. Закол сверху (Short Sweep)
    trades_high = [
        {"T": 2000, "S": "Buy", "p": 153.60, "v": 80.0},
        {"T": 2500, "S": "Buy", "p": 153.85, "v": 150.0},
    ]
    state.apply_trades(trades_high)

    assert state.sweep_high_active is True
    assert state.sweep_max_price == 153.85
    assert state.sweep_buy_vol == 230.0


def test_distance_weighted_obi_dampens_gapped_levels(mock_meta):
    """Проверяет, что дистанционное взвешивание экспоненциально гасит влияние дыр в стакане."""
    state = SymbolMicrostructureState("SOLUSDT", mock_meta)

    # Стакан с большой дырой на покупку: Bid1 на 150.00 (100 qty), Bid2 далеко на 149.00 (100 тиков вниз, 1000 qty)
    # Аски плотные: Ask1 на 150.01 (100 qty), Ask2 на 150.02 (100 qty)
    snapshot = {
        "b": [["150.00", "100.0"], ["149.00", "1000.0"]],
        "a": [["150.01", "100.0"], ["150.02", "100.0"]],
    }
    state.apply_orderbook("snapshot", snapshot, 1000)

    # Далекий объем 1000.0 на расстоянии 100 тиков гасится почти до 0: exp(-0.25 * 100) ~ 0
    # Поэтому OBI не должен быть захвачен этой далекой плитой
    assert state.obi < 0.20
    # Проверка микроцены Стойкова
    assert state.micro_price == pytest.approx(150.005, abs=1e-3)


def test_fixed_price_depletion_and_anti_spoofing(mock_meta):
    """Проверяет фиксацию ценовых уровней против ошибки сдвига и защиту от спуфинга."""
    state = SymbolMicrostructureState("SOLUSDT", mock_meta)

    # t0: начальный стакан
    snap_t0 = {
        "b": [["150.00", "100.0"]],
        "a": [["150.01", "100.0"], ["150.02", "100.0"], ["150.03", "100.0"]],
    }
    state.apply_orderbook("snapshot", snap_t0, 1000)

    # t1: разъели Ask1 и Ask2
    delta_t1 = {
        "b": [],
        "a": [["150.01", "0.0"], ["150.02", "0.0"], ["150.04", "500.0"]],  # добавилась плита выше
    }
    state.apply_orderbook("delta", delta_t1, 1400)

    # Проверяем разъедание: должно учитывать именно уровни <= 150.03
    log_ret, dr_ask, dr_bid, delta_ask_vol, delta_bid_vol = state.get_depletion_with_volumes(1400, 400)
    assert dr_ask > 0.50
    assert delta_ask_vol == 200.0  # Исчезло ровно 200 единиц (150.01 и 150.02)

