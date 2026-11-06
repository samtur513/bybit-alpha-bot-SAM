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
    """Проверяет, что в режиме возврата к среднему система ждёт отката 20% от пика."""
    engine = SectorArbEngine8080()
    cfg = SECTOR_PAIRS[0]
    pair_id = f"{cfg.symbol_a}/{cfg.symbol_b}"
    st = SectorPairState(cfg)
    engine.pairs_state[pair_id] = st

    # 1. Заполняем историю спреда средними ценами
    now = time.time()
    for i in range(50):
        st.update_prices(100.0, 2.0, now - (50 - i) * 60)

    # 2. Мощный прострел вверх: SOL вырос на 2.5% относительно SUI в режиме боковика
    st.update_prices(102.5, 2.0, now)
    st.regime = "MEAN_REV"
    assert st.dislocation_pct > 0.01
    assert st.z_score > 1.85

    # Оцениваем сигнал: должен создаться pending_spike, но позиция НЕ должна открыться
    engine._evaluate_sector_pair_signal(st)
    assert st.pending_spike is not None
    assert st.pending_spike.direction == "SHORT_SPREAD"
    assert len(engine.active_positions) == 0  # Не вошли на летящем ноже!

    # 3. Прострел продолжается ещё выше: отката нет
    st.update_prices(103.0, 2.0, now + 10)
    st.regime = "MEAN_REV"
    engine._evaluate_sector_pair_signal(st)
    assert len(engine.active_positions) == 0
    assert st.pending_spike.extreme_disloc > 0.02

    # 4. Происходит подтверждённый откат на 25%+ от пика дислокации
    st.update_prices(101.5, 2.0, now + 20)
    st.regime = "MEAN_REV"
    engine._evaluate_sector_pair_signal(st)
    # Reversal confirmed: позиция должна открыться!
    assert pair_id in engine.active_positions
    pos = engine.active_positions[pair_id]
    assert pos.direction == "SHORT_SPREAD"
    assert pos.symbol_a == "SOLUSDT"
    assert pos.symbol_b == "SUIUSDT"
    assert pos.regime == "MEAN_REV"


def test_dual_regime_momentum_entry():
    """Проверяет, что при KER >= 0.35 система мгновенно входит по импульсу за лидером сектора."""
    engine = SectorArbEngine8080()
    cfg = SECTOR_PAIRS[0]
    pair_id = f"{cfg.symbol_a}/{cfg.symbol_b}"
    st = SectorPairState(cfg)
    engine.pairs_state[pair_id] = st

    now = time.time()
    for i in range(50):
        st.update_prices(100.0, 2.0, now - (50 - i) * 60)

    # Импульсный пробой с высоким KER (тренд)
    st.update_prices(102.5, 2.0, now)
    st.ker_30m = 0.55
    st.regime = "MOMENTUM"
    
    engine._evaluate_sector_pair_signal(st)
    # В режиме MOMENTUM вход происходит сразу!
    assert pair_id in engine.active_positions
    pos = engine.active_positions[pair_id]
    assert pos.direction == "LONG_SPREAD"
    assert pos.regime == "MOMENTUM"


def test_throttled_one_minute_sampling():
    """Проверяет, что частые тики WebSocket (< 60с) не засоряют историю, сохраняя окно 180 минут."""
    cfg = SECTOR_PAIRS[0]
    st = SectorPairState(cfg)

    now = time.time()
    st.update_prices(100.0, 2.0, now)
    assert len(st.price_history_a) == 1

    # Посылаем 50 тиков с разницей 100 миллисекунд
    for i in range(1, 51):
        st.update_prices(100.0 + i * 0.01, 2.0, now + i * 0.1)
    
    # Размер буфера должен остаться 1, а текущая цена обновиться!
    assert len(st.price_history_a) == 1
    assert st.current_mid_a == 100.50

    # Новый бар добавляется только через 60 секунд
    st.update_prices(101.0, 2.0, now + 61.0)
    assert len(st.price_history_a) == 2


@pytest.mark.asyncio
async def test_guaranteed_profit_locking_two_stage():
    """Проверяет железную фиксацию прибыли: TP1 фиксирует кеш, стоп в безубыток, и сделка закрывается в плюс!"""
    engine = SectorArbEngine8080()
    cfg = SECTOR_PAIRS[0]
    pair_id = f"{cfg.symbol_a}/{cfg.symbol_b}"
    st = SectorPairState(cfg)
    engine.pairs_state[pair_id] = st

    st.current_mid_a = 100.0
    st.current_mid_b = 2.0
    st.z_score = 0.50

    # Открываем шорт спреда от 102.0
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
        opened_ts=time.time() - 30.0,
        entry_z=2.5,
        beta=1.0,
        regime="MEAN_REV",
    )
    engine.active_positions[pair_id] = pos

    # Шаг 1: Цена пришла к тейк-профиту TP1 (SOL упал до 100.0) -> фиксируем 50%
    exit_task = asyncio.create_task(engine.execution_and_exit_loop())
    await asyncio.sleep(0.05)
    
    assert pos.tp1_filled is True
    assert pos.be_stop_active is True
    assert pos.tp1_realized_usdt > 0.0  # Кеш зафиксирован!

    # Шаг 2: Рынок разворачивается против нас назад до уровня безубытка
    st.current_mid_a = 101.95  # Возврат к точке входа
    st.z_score = 2.4
    await asyncio.sleep(0.35)
    exit_task.cancel()
    try:
        await exit_task
    except asyncio.CancelledError:
        pass

    # Позиция должна быть закрыта по защитному безубытку BE_PROTECT_TRIGGERED
    assert pair_id not in engine.active_positions
    assert len(engine.closed_trades_history) == 1
    closed = engine.closed_trades_history[0]
    assert closed["exit_reason"] == "BE_PROTECT_TRIGGERED"
    # Общий PnL сделки СТРОГО ПОЛОЖИТЕЛЬНЫЙ благодаря зафиксированным 50% на TP1!
    assert closed["net_pnl"] > 0.0
    assert engine.equity_usdt > engine.initial_capital


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
