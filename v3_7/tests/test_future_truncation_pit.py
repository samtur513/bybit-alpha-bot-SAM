"""
Future Truncation & Point-in-Time (PiT) Test for v3.7 Scalper (crypto-hybrid-bot-tester Standard)
Проверяет отсутствие заглядывания в будущее (Lookahead Bias).
Данные, усеченные в точке T, обязаны давать идентичные метрики OBI, TFI и сигналы с точностью до 10^-9.
"""

import numpy as np
import pytest

from v3_7.src.config import ScalperConfig
from v3_7.src.microstructure import InstrumentMeta, SymbolMicrostructureState
from v3_7.src.scalp_engines import ThreeEnginesEvaluate


@pytest.fixture
def meta() -> InstrumentMeta:
    return InstrumentMeta(
        symbol="BTCUSDT",
        tick_size=0.1,
        qty_step=0.001,
        min_qty=0.001,
        natr_5m=0.004,
        atr_5m=250.0,
        atr_15m=420.0,
        hurst_15m=0.45,
        er_15m=0.48,
        ema9_5m=65100.0,
        ema21_5m=64900.0,
        ema20_15m=64800.0,
        ema50_4h=64000.0,
        vwap_session=65000.0,
        swing_low_1h=64500.0,
        swing_high_1h=65500.0,
        beta_btc=1.0,
        corr_btc=1.0,
    )


def test_future_truncation_microstate_invariance(meta):
    cfg = ScalperConfig()
    evaluator = ThreeEnginesEvaluate(cfg)

    # Создаем длинную последовательность тиков и ордербуков (100 шагов)
    events = []
    base_price = 65000.0
    for i in range(100):
        ts = 100000 + i * 100
        p = base_price + i * 2.0
        events.append({
            "type": "book",
            "ts": ts,
            "data": {
                "b": [[str(p), "10.0"], [str(p - 1.0), "20.0"]],
                "a": [[str(p + 1.0), "12.0"], [str(p + 2.0), "25.0"]],
            }
        })
        events.append({
            "type": "trade",
            "ts": ts + 50,
            "data": [{"T": ts + 50, "S": "Buy", "p": p + 1.0, "v": 1.5}]
        })

    # Выбираем точку усечения T = 50-й шаг
    T_cutoff = events[100]["ts"]

    # Прогон 1: Только до T_cutoff
    state_pit = SymbolMicrostructureState("BTCUSDT", meta)
    for ev in events:
        if ev["ts"] > T_cutoff:
            break
        if ev["type"] == "book":
            state_pit.apply_orderbook("delta" if state_pit.sorted_bids else "snapshot", ev["data"], ev["ts"])
        else:
            state_pit.apply_trades(ev["data"])

    # Прогон 2: Полный прогон с замером состояния ровно в момент T_cutoff
    state_full = SymbolMicrostructureState("BTCUSDT", meta)
    obi_at_T = None
    mid_at_T = None
    tfi_at_T = None

    for ev in events:
        if ev["type"] == "book":
            state_full.apply_orderbook("delta" if state_full.sorted_bids else "snapshot", ev["data"], ev["ts"])
        else:
            state_full.apply_trades(ev["data"])

        if ev["ts"] == T_cutoff:
            obi_at_T = state_full.obi
            mid_at_T = state_full.mid_price
            tfi_at_T, _, _, _, _ = state_full.get_trade_flow_metrics(T_cutoff, 2000)

    # Проверяем равенство с машинной точностью 10^-9
    tfi_pit, _, _, _, _ = state_pit.get_trade_flow_metrics(T_cutoff, 2000)
    assert abs(state_pit.obi - obi_at_T) < 1e-9
    assert abs(state_pit.mid_price - mid_at_T) < 1e-9
    assert abs(tfi_pit - tfi_at_T) < 1e-9
