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


def test_structural_basis_rejection_prevents_fee_churn():
    """Проверяет, что постоянный структурный базис (например NEAR +10 bps) не порождает ложных входов."""
    engine = CrossBasisEngine8096()
    spot_st = SpotState(symbol="NEARUSDT")
    spot_st.mid_price = 3.7185
    spot_st.tfi_3s = 0.40
    # Наполняем историю постоянным базисом +10 bps
    for i in range(30):
        spot_st.basis_history.append((1000 + i * 1000, 10.0))
    engine.spot_states["NEARUSDT"] = spot_st

    # Тот же базис +10.0 bps (нет импульсной дислокации)
    perp_tick = {
        "ts": 31000,
        "symbol": "NEARUSDT",
        "bid": 3.7140,
        "ask": 3.7150,
        "mid": 3.7145,  # Basis = (3.7185 - 3.7145) / 3.7145 * 10000 = +10.77 bps
        "tfi_3s": 0.0,
        "tick_size": 0.0005,
        "qty_step": 0.1,
        "min_qty": 0.1,
    }

    engine._evaluate_cross_basis_setup(perp_tick)
    # Сигнал должен быть отклонен, так как дислокация относительно среднего (10.77 - 10.0 = 0.77 bps) < 8.5 bps
    assert "NEARUSDT" not in engine.pending_intents


def test_circuit_breaker_blocks_entries_on_drawdown():
    """Проверяет блокировку входов при срабатывании дневного Circuit Breaker."""
    engine = CrossBasisEngine8096()
    engine.circuit_breaker_tripped = True

    spot_st = SpotState(symbol="BTCUSDT")
    spot_st.mid_price = 76200.0
    spot_st.tfi_3s = 0.50
    engine.spot_states["BTCUSDT"] = spot_st

    perp_tick = {
        "ts": int(time.time() * 1000),
        "symbol": "BTCUSDT",
        "bid": 76000.0,
        "ask": 76002.0,
        "mid": 76001.0,
        "tfi_3s": 0.0,
        "tick_size": 0.1,
        "qty_step": 0.001,
        "min_qty": 0.001,
    }

    engine._evaluate_cross_basis_setup(perp_tick)
    assert "BTCUSDT" not in engine.pending_intents

