import asyncio
import pytest
import time
from v3_14_basket_dispersion.src.config import (
    PORT_8094,
    DISPERSION_ALTS,
    MIN_Z_ENTRY,
    MIN_DISLOCATION_PCT,
    MAX_BENCHMARK_KER_4H,
)
from v3_14_basket_dispersion.src.dispersion_8094 import (
    DispersionEngine8094,
    BenchmarkState,
    AltDispersionState,
    PendingDispersionSetup,
    ActiveBasketPosition,
)


def test_benchmark_synthetic_price_and_ker():
    bench = BenchmarkState()
    now = time.time()
    
    # 1. Update BTC & ETH
    bench.update_price("BTCUSDT", 70000.0, now)
    bench.update_price("ETHUSDT", 2500.0, now)
    
    # Geometric mean: sqrt(70000 * 2500) = sqrt(175,000,000) = ~13228.756
    expected_p = (70000.0 * 2500.0) ** 0.5
    assert abs(bench.bench_price - expected_p) < 0.01

    # 2. Simulate ranging market (low KER)
    for i in range(40):
        # Oscillate price
        osc = 100.0 if i % 2 == 0 else -100.0
        bench.index_history.append((now - (40 - i) * 60, expected_p + osc))
    bench._compute_ker_4h()
    assert bench.ker_4h <= MAX_BENCHMARK_KER_4H
    assert not bench.is_macro_trend_blocking

    # 3. Simulate runaway trend market (high KER)
    bench.index_history.clear()
    for i in range(40):
        bench.index_history.append((now - (40 - i) * 60, expected_p + i * 200.0))
    bench._compute_ker_4h()
    assert bench.ker_4h > MAX_BENCHMARK_KER_4H
    assert bench.is_macro_trend_blocking


def test_alt_dispersion_state_beta_and_residual():
    st = AltDispersionState("SOLUSDT")
    now = time.time()
    bench_base = 13000.0
    alt_base = 100.0

    # Feed 40 correlated points
    for i in range(40):
        t = now - (40 - i) * 60
        st.update_market(alt_base + i * 0.5, bench_base + i * 50.0, t)

    assert st.current_mid > 0
    assert 0.5 <= st.rolling_beta <= 2.5
    assert isinstance(st.z_score, float)


def test_auditor_pullback_confirmation_gate():
    """Проверяет защиту от adverse selection: ожидание истощения и 20% отката."""
    engine = DispersionEngine8094()
    st = engine.alts_state["SOLUSDT"]
    now = time.time()
    bench_p = 13000.0

    # 1. Заполняем историю базовыми ценами
    for i in range(50):
        st.update_market(100.0, bench_p, now - (50 - i) * 60)

    # Убеждаемся, что бенчмарк в нормальном режиме
    engine.benchmark.ker_4h = 0.20
    engine.benchmark.p_btc = 70000.0
    engine.benchmark.p_eth = 2500.0
    engine.benchmark.bench_price = bench_p

    # 2. Мощный прострел вверх: SOL вырос на 3% относительно бенчмарка
    st.update_market(103.0, bench_p, now)
    assert st.dislocation_pct > MIN_DISLOCATION_PCT
    assert st.z_score >= MIN_Z_ENTRY

    # Оцениваем сигнал: создаётся pending_spike, но позиция НЕ открывается сразу!
    engine._evaluate_dispersion_signal(st)
    assert st.pending_spike is not None
    assert st.pending_spike.direction == "SHORT_ALT_LONG_HEDGE"
    assert len(engine.active_positions) == 0  # Не вошли на летящем ноже!

    # 3. Прострел продолжается ещё выше: отката нет
    st.update_market(103.5, bench_p, now + 10)
    engine._evaluate_dispersion_signal(st)
    assert len(engine.active_positions) == 0
    assert st.pending_spike.extreme_disloc > 0.03

    # 4. Происходит подтверждённый откат на 20%+ от пика дислокации
    st.update_market(102.2, bench_p, now + 20)
    engine._evaluate_dispersion_signal(st)
    # Reversal confirmed: 3-леговая корзина должна открыться!
    assert "SOLUSDT" in engine.active_positions
    pos = engine.active_positions["SOLUSDT"]
    assert pos.direction == "SHORT_ALT_LONG_HEDGE"
    assert pos.alt_symbol == "SOLUSDT"
    assert pos.notional_alt == 45.0
    assert pos.notional_btc > 0
    assert pos.notional_eth > 0


def test_ker_macro_trend_filter_blocks_entry():
    """Проверяет, что при KER 4h > 0.45 арбитражные входы строго блокируются."""
    engine = DispersionEngine8094()
    st = engine.alts_state["SOLUSDT"]
    now = time.time()
    bench_p = 13000.0

    for i in range(50):
        st.update_market(100.0, bench_p, now - (50 - i) * 60)

    # Задаем макро-пробой тренда на бенчмарке
    engine.benchmark.ker_4h = 0.65  # > 0.45
    assert engine.benchmark.is_macro_trend_blocking

    # Прострел альта
    st.update_market(103.0, bench_p, now)
    engine._evaluate_dispersion_signal(st)

    # Вход должен быть заблокирован фильтром макро-тренда!
    assert st.pending_spike is None
    assert len(engine.active_positions) == 0


@pytest.mark.asyncio
async def test_two_stage_exit_tp1_and_breakeven():
    """Проверяет частичный 50% TP1 при Z <= 0.50 и перевод стопа в BE."""
    engine = DispersionEngine8094()
    st = engine.alts_state["SOLUSDT"]
    now = time.time()
    bench_p = 13000.0

    st.current_mid = 100.0
    st.z_score = 0.45  # Схождение ниже TP1_Z_THRESHOLD (0.50)
    engine.benchmark.p_btc = 70000.0
    engine.benchmark.p_eth = 2500.0
    engine.benchmark.bench_price = bench_p

    pos = ActiveBasketPosition(
        alt_symbol="SOLUSDT",
        direction="SHORT_ALT_LONG_HEDGE",
        entry_p_alt=103.0,
        entry_p_btc=70000.0,
        entry_p_eth=2500.0,
        notional_alt=45.0,
        notional_btc=22.5,
        notional_eth=22.5,
        opened_ts=now - 120.0,
        entry_z=2.50,
        beta=1.0,
        entry_fr_diff=0.0002,
    )
    engine.active_positions["SOLUSDT"] = pos

    exit_task = asyncio.create_task(engine.execution_and_exit_loop())
    await asyncio.sleep(0.05)
    exit_task.cancel()
    try:
        await exit_task
    except asyncio.CancelledError:
        pass

    # Половина корзины зафиксирована, стоп перенесён в BE
    assert pos.tp1_filled is True
    assert pos.be_stop_active is True
    assert pos.tp1_realized_usdt > 0.0


def test_circuit_breaker_halts_trading():
    engine = DispersionEngine8094()
    st = engine.alts_state["SOLUSDT"]

    engine.equity_usdt = 293.0
    engine.realized_pnl_usdt = -7.0
    engine.circuit_breaker_tripped = True

    st.z_score = 3.0
    st.dislocation_pct = 0.02
    engine._evaluate_dispersion_signal(st)

    assert len(engine.active_positions) == 0
    assert st.pending_spike is None
