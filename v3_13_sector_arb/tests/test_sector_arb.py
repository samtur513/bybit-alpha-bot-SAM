import asyncio
import pytest
import time
from v3_13_sector_arb.src.config import SECTOR_PAIRS
from v3_13_sector_arb.src.sector_arb_8080 import (
    SectorArbEngine8080,
    SectorPairState,
    PendingSpikeSetup,
    ActiveSectorPosition,
)


def test_sector_pair_state_beta_and_zscore():
    cfg = SECTOR_PAIRS[0]  # SOL / SUI
    st = SectorPairState(cfg)

    # Feed correlated price points
    now = time.time()
    for i in range(40):
        t = now - (40 - i) * 60
        p_a = 100.0 + i * 0.5
        p_b = 2.0 + i * 0.01
        st.update_prices(p_a, p_b, t)

    assert st.current_mid_a > 0
    assert st.current_mid_b > 0
    assert st.rolling_beta > 0
    assert isinstance(st.z_score, float)


def test_auditor_pullback_confirmation_gate():
    """Проверяет, что система не входит на летящем ноже (adverse selection), а ждёт отката 25%."""
    engine = SectorArbEngine8080()
    cfg = SECTOR_PAIRS[0]
    pair_id = f"{cfg.symbol_a}/{cfg.symbol_b}"
    st = SectorPairState(cfg)
    engine.pairs_state[pair_id] = st

    # 1. Заполняем историю спреда средними ценами
    now = time.time()
    for i in range(50):
        st.update_prices(100.0, 2.0, now - (50 - i) * 60)

    # 2. Мощный прострел вверх: SOL вырос на 2% относительно SUI
    st.update_prices(102.5, 2.0, now)
    assert st.dislocation_pct > 0.01
    assert st.z_score > 2.0

    # Оцениваем сигнал: должен создаться pending_spike, но позиция НЕ должна открыться
    engine._evaluate_sector_pair_signal(st)
    assert st.pending_spike is not None
    assert st.pending_spike.direction == "SHORT_SPREAD"
    assert len(engine.active_positions) == 0  # Не вошли на летящем ноже!

    # 3. Прострел продолжается ещё выше: отката нет
    st.update_prices(103.0, 2.0, now + 10)
    engine._evaluate_sector_pair_signal(st)
    assert len(engine.active_positions) == 0
    assert st.pending_spike.extreme_disloc > 0.02

    # 4. Происходит подтверждённый откат на 25%+ от пика дислокации
    st.update_prices(101.5, 2.0, now + 20)
    engine._evaluate_sector_pair_signal(st)
    # Reversal confirmed: позиция должна открыться!
    assert pair_id in engine.active_positions
    pos = engine.active_positions[pair_id]
    assert pos.direction == "SHORT_SPREAD"
    assert pos.symbol_a == "SOLUSDT"
    assert pos.symbol_b == "SUIUSDT"


@pytest.mark.asyncio
async def test_two_stage_exit_tp1_and_breakeven():
    """Проверяет двухстадийный выход аудитора: 50% TP1 при Z <= 0.70 и перевод стопа в BE."""
    engine = SectorArbEngine8080()
    cfg = SECTOR_PAIRS[0]
    pair_id = f"{cfg.symbol_a}/{cfg.symbol_b}"
    st = SectorPairState(cfg)
    engine.pairs_state[pair_id] = st

    st.current_mid_a = 100.0
    st.current_mid_b = 2.0
    st.z_score = 0.65  # Reverted below TP1_Z_THRESHOLD (0.70)

    pos = ActiveSectorPosition(
        pair_id=pair_id,
        sector_name=cfg.sector_name,
        direction="SHORT_SPREAD",
        symbol_a=cfg.symbol_a,
        symbol_b=cfg.symbol_b,
        entry_p_a=102.0,
        entry_p_b=2.0,
        notional_a=50.0,
        notional_b=50.0,
        opened_ts=time.time() - 60.0,
        entry_z=2.5,
        beta=1.0,
    )
    engine.active_positions[pair_id] = pos

    # Запускаем проверку выхода
    exit_task = asyncio.create_task(engine.execution_and_exit_loop())
    await asyncio.sleep(0.05)
    exit_task.cancel()
    try:
        await exit_task
    except asyncio.CancelledError:
        pass

    # Половина позиции должна быть частично зафиксирована
    assert pos.tp1_filled is True
    assert pos.be_stop_active is True
    assert pos.tp1_realized_usdt > 0.0


def test_daily_circuit_breaker_halts_trading():
    engine = SectorArbEngine8080()
    cfg = SECTOR_PAIRS[0]
    pair_id = f"{cfg.symbol_a}/{cfg.symbol_b}"
    st = SectorPairState(cfg)
    engine.pairs_state[pair_id] = st

    # Имитируем убыток $6.50
    engine.equity_usdt = 293.50
    engine.realized_pnl_usdt = -6.50
    engine.circuit_breaker_tripped = True

    st.z_score = 3.5
    st.dislocation_pct = 0.03
    engine._evaluate_sector_pair_signal(st)

    # Вход должен быть заблокирован
    assert len(engine.active_positions) == 0
    assert st.pending_spike is None
