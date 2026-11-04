"""
News Defense & Shield Integration Tests for v3.7 Scalper (crypto-hybrid-bot-tester Standard)
"""

import asyncio
import pytest

from v3_7.src.config import ScalperConfig
from v3_7.src.microstructure import InstrumentMeta, SymbolMicrostructureState
from v3_7.src.scalp_engines import SignalIntent
from v3_7.src.scalp_oms import ScalpOMS


@pytest.fixture
def meta() -> InstrumentMeta:
    return InstrumentMeta(
        symbol="ETHUSDT",
        tick_size=0.01,
        qty_step=0.01,
        min_qty=0.01,
        natr_5m=0.005,
        atr_5m=12.0,
        atr_15m=22.0,
        vwap_session=3200.0,
        swing_low_1h=3150.0,
        swing_high_1h=3250.0,
    )


def test_macro_embargo_and_emergency_shock_ejection(meta):
    async def _run():
        cfg = ScalperConfig(equity_usdt=300.0)
        state = SymbolMicrostructureState("ETHUSDT", meta)
        state.apply_orderbook("snapshot", {"b": [["3200.00", "50.0"]], "a": [["3200.05", "50.0"]]}, 1000)

        oms = ScalpOMS(cfg, {"ETHUSDT": state})

        # 1. Подаем 1 сигнал, который исполняется в позицию
        intent1 = SignalIntent(
            engine_id="E1_OBI",
            priority=3,
            symbol="ETHUSDT",
            side="Buy",
            entry_price=3200.00,
            sl_price=3185.00,
            tp1_price=3220.00,
            tp2_price=3240.00,
            max_hold_sec=40.0,
        )
        await oms.submit_signal(intent1)
        # Исполняем позицию
        oms.on_market_trade("ETHUSDT", is_buy=False, price=3200.00, qty=100.0)
        assert "ETHUSDT" in oms.active_positions

        # 2. Подаем 2-й сигнал по другому тикеру SOLUSDT, оставляя его в PENDING
        sol_meta = InstrumentMeta(symbol="SOLUSDT", tick_size=0.01, qty_step=0.1, min_qty=0.1)
        sol_state = SymbolMicrostructureState("SOLUSDT", sol_meta)
        sol_state.apply_orderbook("snapshot", {"b": [["150.00", "200.0"]], "a": [["150.01", "200.0"]]}, 1000)
        oms.states["SOLUSDT"] = sol_state

        intent2 = SignalIntent(
            engine_id="E2_SWEEP",
            priority=1,
            symbol="SOLUSDT",
            side="Buy",
            entry_price=150.00,
            sl_price=149.00,
            tp1_price=151.50,
            tp2_price=153.00,
            max_hold_sec=180.0,
        )
        await oms.submit_signal(intent2)
        assert "SOLUSDT" in oms.pending_orders

        # 3. Приходит макро-эмбарго (FOMC / CPI релиз) или аварийный шок
        await oms.emergency_news_eject(reason="FOMC_MACRO_LOCKOUT")

        # Проверяем инварианты защиты:
        # А. Все ожидающие ордера немедленно сняты
        assert len(oms.pending_orders) == 0
        # Б. Все открытые позиции катапультированы
        assert len(oms.active_positions) == 0
        assert len(oms.closed_trades_history) == 1
        assert oms.closed_trades_history[0]["reason"] == "NEWS_EJECT_FOMC_MACRO_LOCKOUT"
        # В. Режим эмбарго включен -> новые сигналы отклоняются
        assert oms.news_embargo_active is True
        rejected = await oms.submit_signal(intent1)
        assert rejected is False

    asyncio.run(_run())
