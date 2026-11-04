"""
Accounting & Queue Invariant Tests for v3.7 Scalp OMS (crypto-hybrid-bot-tester Standard)
"""

import asyncio
import pytest

from v3_7.src.config import ScalperConfig
from v3_7.src.microstructure import InstrumentMeta, SymbolMicrostructureState
from v3_7.src.scalp_engines import SignalIntent
from v3_7.src.scalp_oms import ScalpOMS


@pytest.fixture
def cfg() -> ScalperConfig:
    return ScalperConfig(equity_usdt=300.0, use_mnt_discount=True)


@pytest.fixture
def meta() -> InstrumentMeta:
    return InstrumentMeta(
        symbol="SOLUSDT",
        tick_size=0.01,
        qty_step=0.1,
        min_qty=0.1,
        natr_5m=0.006,
        atr_5m=0.9,
        atr_15m=1.6,
        vwap_session=150.0,
        swing_low_1h=148.0,
        swing_high_1h=154.0,
        beta_btc=1.5,
        corr_btc=0.8,
    )


def test_queue_simulation_prevents_instant_fill(cfg, meta):
    """
    Инвариант №1: Заявка Post-Only НЕ исполняется мгновенно (Winner's Curse / Adverse Selection).
    Требуется реальное разъедание очереди перед нами в стакане.
    """
    async def _run():
        state = SymbolMicrostructureState("SOLUSDT", meta)
        state.apply_orderbook("snapshot", {
            "b": [["150.00", "500.0"]],
            "a": [["150.01", "200.0"]],
        }, 1000)

        oms = ScalpOMS(cfg, {"SOLUSDT": state})
        intent = SignalIntent(
            engine_id="E1_OBI",
            priority=3,
            symbol="SOLUSDT",
            side="Buy",
            entry_price=150.00,
            sl_price=149.50,
            tp1_price=150.70,
            tp2_price=151.50,
            max_hold_sec=40.0,
        )

        # 1. Подаем сигнал -> ордер встает в PENDING с очередью 500.0 монет
        submitted = await oms.submit_signal(intent)
        assert submitted is True
        assert "SOLUSDT" in oms.pending_orders
        assert "SOLUSDT" not in oms.active_positions
        assert oms.pending_orders["SOLUSDT"].queue_ahead_vol == 500.0

        # 2. Проходит продажа на 200 монет -> очередь уменьшается, но ордер ЕЩЕ НЕ исполнен
        pos = oms.on_market_trade("SOLUSDT", is_buy=False, price=150.00, qty=200.0)
        assert pos is None
        assert oms.pending_orders["SOLUSDT"].queue_ahead_vol == 300.0
        assert "SOLUSDT" not in oms.active_positions

        # 3. Проходит продажа на 350 монет -> очередь съедена полностью, ордер ИСПОЛНЕН
        pos = oms.on_market_trade("SOLUSDT", is_buy=False, price=150.00, qty=350.0)
        assert pos is not None
        assert "SOLUSDT" not in oms.pending_orders
        assert "SOLUSDT" in oms.active_positions
        assert pos.entry_price == 150.00

    asyncio.run(_run())


def test_penny_perfect_tp1_partial_realization(cfg, meta):
    """
    Инвариант №2: При взятии TP1 ровно 50% объема фиксируется в PnL,
    объем позиции уменьшается вдвое, а стоп переносится в безубыток (+0.10%).
    """
    async def _run():
        state = SymbolMicrostructureState("SOLUSDT", meta)
        state.apply_orderbook("snapshot", {"b": [["150.00", "100.0"]], "a": [["150.01", "100.0"]]}, 1000)

        oms = ScalpOMS(cfg, {"SOLUSDT": state})
        intent = SignalIntent(
            engine_id="E2_SWEEP",
            priority=1,
            symbol="SOLUSDT",
            side="Buy",
            entry_price=150.00,
            sl_price=149.50,
            tp1_price=151.00,  # +0.667%
            tp2_price=152.00,
            max_hold_sec=180.0,
        )
        await oms.submit_signal(intent)

        # Исполняем лимитку входа
        pending = oms.pending_orders["SOLUSDT"]
        initial_qty = pending.qty
        oms.on_market_trade("SOLUSDT", is_buy=False, price=150.00, qty=pending.queue_ahead_vol + 10.0)
        pos = oms.active_positions["SOLUSDT"]
        assert pos.qty == initial_qty

        # Цена поднимается до TP1 (151.00)
        now_ms = 2000
        state.apply_orderbook("snapshot", {"b": [["151.00", "100.0"]], "a": [["151.01", "100.0"]]}, now_ms)
        await oms.monitor_step(now_ms)

        # Проверяем инвариант частичной фиксации
        assert pos.tp1_filled is True
        assert pos.qty == pytest.approx(initial_qty * 0.5, abs=0.1)
        assert pos.tp1_realized_pnl_usdt > 0.0
        assert oms.daily_pnl_usdt == pytest.approx(pos.tp1_realized_pnl_usdt, abs=1e-5)
        # Стоп перенесен в безубыток выше цены входа
        assert pos.sl_price > pos.entry_price

        # Цена доходит до TP2 (152.00) -> закрытие остатка
        now_ms = 3000
        state.apply_orderbook("snapshot", {"b": [["152.00", "100.0"]], "a": [["152.01", "100.0"]]}, now_ms)
        await oms.monitor_step(now_ms)

        assert "SOLUSDT" not in oms.active_positions
        assert len(oms.closed_trades_history) == 1
        closed_trade = oms.closed_trades_history[0]
        assert closed_trade["reason"] == "TP2_TARGET"
        assert closed_trade["net_pnl_usdt"] > pos.tp1_realized_pnl_usdt
        assert oms.daily_pnl_usdt == pytest.approx(closed_trade["net_pnl_usdt"], abs=1e-5)

    asyncio.run(_run())


def test_consecutive_losses_and_circuit_breaker(cfg, meta):
    """
    Инвариант №3: 3 убытка подряд включают карантин монеты,
    а дневная просадка -1.5% останавливает всю торговлю.
    """
    async def _run():
        state = SymbolMicrostructureState("SOLUSDT", meta)
        oms = ScalpOMS(cfg, {"SOLUSDT": state})

        # Симулируем 3 убыточные сделки
        for i in range(3):
            intent = SignalIntent(
                engine_id="E1_OBI",
                priority=3,
                symbol="SOLUSDT",
                side="Buy",
                entry_price=150.00,
                sl_price=149.20,
                tp1_price=151.00,
                tp2_price=152.00,
                max_hold_sec=40.0,
            )
            state.apply_orderbook("snapshot", {"b": [["150.00", "50.0"]], "a": [["150.01", "50.0"]]}, 1000 + i * 5000)
            await oms.submit_signal(intent)
            oms.on_market_trade("SOLUSDT", is_buy=False, price=150.00, qty=100.0)

            # Выбиваем стоп
            state.apply_orderbook("snapshot", {"b": [["149.10", "50.0"]], "a": [["149.11", "50.0"]]}, 1500 + i * 5000)
            await oms.monitor_step(1500 + i * 5000)

        # После 3 убытков тикер SOLUSDT в карантине на 60 минут
        assert oms.consecutive_losses["SOLUSDT"] == 3
        assert oms.cooldown_until["SOLUSDT"] > 0.0

        # Следующий сигнал по SOLUSDT должен быть отклонен
        rejected = await oms.submit_signal(intent)
        assert rejected is False

    asyncio.run(_run())
