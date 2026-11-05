"""
v3.7 Bybit Scalper Core Daemon
Главный оркестратор высокочастотного скальпинга:
- Сборка потоков orderbook.50 и publicTrade.
- Приоритезация сигналов E2_SWEEP (Приор 1) > E3_LEADLAG (Приор 2) > E1_OBI (Приор 3).
- Цикл сопровождения позиций с шагом 25 мс.
- Управление сборщиком мусора gc_guardian_task.
"""

import asyncio
import gc
import logging
import time
from typing import Dict, Optional

try:
    import orjson
except ImportError:
    import json

    class _OrjsonFallback:
        @staticmethod
        def dumps(obj, *args, **kwargs) -> bytes:
            return json.dumps(obj).encode("utf-8")

        @staticmethod
        def loads(obj, *args, **kwargs):
            if isinstance(obj, (bytes, bytearray)):
                return json.loads(obj.decode("utf-8"))
            return json.loads(obj)

    orjson = _OrjsonFallback()
import websockets

try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

from v3_7.src.config import ScalperConfig
from v3_7.src.microstructure import SymbolMicrostructureState
from v3_7.src.scalp_engines import ThreeEnginesEvaluate
from v3_7.src.scalp_oms import ScalpOMS
from v3_7.src.scalp_screener import UniverseScreener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ScalperCore_v37")


GLOBAL_OMS: Optional[ScalpOMS] = None


async def status_export_task(oms: ScalpOMS) -> None:
    """Периодический сброс живой телеметрии в LIVE_STATUS_FILE и отправка логов активности."""
    from v3.src.persistence import atomic_json
    from v3_7.src.config import LIVE_STATUS_FILE
    last_heartbeat = 0.0
    while True:
        try:
            status = oms.get_status()
            atomic_json(LIVE_STATUS_FILE, status)
            now = time.time()
            if now - last_heartbeat >= 30.0:
                last_heartbeat = now
                syms = list(oms.states.keys()) if hasattr(oms, "states") else []
                pos_cnt = len(oms.active_positions)
                ord_cnt = len(oms.pending_orders)
                logger.info(
                    f"[L2 MONITOR 25ms] Мониторинг {len(syms)} тикеров: {syms} | "
                    f"Очередь: {ord_cnt} лимитов | В рынке: {pos_cnt} поз. | OBI/Sweep/Lead-Lag активны"
                )
        except Exception as e:
            logger.debug(f"Status export exception: {e}")
        await asyncio.sleep(2.0)


async def gc_guardian_task(oms: ScalpOMS) -> None:
    """Отключает авто-сборку мусора и запускает очистку только при отсутствии позиций."""
    gc.disable()
    while True:
        await asyncio.sleep(60.0)
        if not oms.active_positions and not oms.pending_orders:
            gc.collect(1)


async def position_monitor_task(oms: ScalpOMS) -> None:
    """Цикл мониторинга ордеров и позиций с шагом 100 мс (энергоэффективный режим для 1 vCPU)."""
    while True:
        now_ms = int(time.time() * 1000)
        await oms.monitor_step(now_ms)
        await asyncio.sleep(0.100)


async def run_public_stream(
    cfg: ScalperConfig,
    states: Dict[str, SymbolMicrostructureState],
    evaluator: ThreeEnginesEvaluate,
    oms: ScalpOMS,
) -> None:
    topics = []
    for sym in states:
        topics.append(f"orderbook.50.{sym}")
        topics.append(f"publicTrade.{sym}")

    while True:
        try:
            async with websockets.connect(cfg.ws_public_url, ping_interval=None, ping_timeout=None, max_queue=4096) as ws:
                await ws.send(orjson.dumps({"op": "subscribe", "args": topics}).decode("utf-8"))
                logger.info(f"Подписка активна на {len(states)} инструментов: {list(states.keys())}")

                async def ping_bybit():
                    while True:
                        try:
                            await asyncio.sleep(20.0)
                            await ws.send(orjson.dumps({"op": "ping"}).decode("utf-8"))
                        except Exception:
                            break

                ping_task = asyncio.create_task(ping_bybit())
                try:
                    async for raw in ws:
                        msg = orjson.loads(raw)
                        topic = msg.get("topic", "")
                        if not topic:
                            continue

                        now_ms = int(msg.get("ts", time.time() * 1000))
                        parts = topic.split(".")
                        sym = parts[-1]
                        st = states.get(sym)
                        if not st:
                            continue

                        if topic.startswith("orderbook"):
                            st.apply_orderbook(msg.get("type", "delta"), msg.get("data", {}), now_ms)
                        elif topic.startswith("publicTrade"):
                            trade_data = msg.get("data", [])
                            st.apply_trades(trade_data)
                            # Обновление очереди лимитных ордеров
                            for tr in trade_data:
                                is_buy = tr.get("S") == "Buy" or tr.get("side") == "Buy"
                                price = float(tr.get("p", tr.get("price", 0.0)))
                                qty = float(tr.get("v", tr.get("size", 0.0)))
                                oms.on_market_trade(sym, is_buy, price, qty)

                        btc_state = states[cfg.leader_symbol]

                        # 1. Если обновился BTCUSDT и включен Lead-Lag -> проверяем Движок 3
                        if sym == cfg.leader_symbol:
                            if getattr(cfg, "enable_e3_leadlag", False):
                                for alt_sym, alt_st in states.items():
                                    if alt_sym == cfg.leader_symbol:
                                        continue
                                    sig3 = evaluator.evaluate_engine3_lead_lag(alt_st, btc_state, now_ms)
                                    if sig3:
                                        await oms.submit_signal(sig3)
                        else:
                            # 2. По альткоину: v3.7 фокусируется на микроструктуре L2 (E2 Sweep и E1 OBI)
                            if getattr(cfg, "enable_e3_leadlag", False):
                                sig3 = evaluator.evaluate_engine3_lead_lag(st, btc_state, now_ms)
                                if sig3:
                                    await oms.submit_signal(sig3)
                                    continue

                            # Приоритет 1 (v3.7): Движок 2 (Liquidity Sweep & Absorption Reversion)
                            if getattr(cfg, "enable_e2_sweeps", True):
                                sig2 = evaluator.evaluate_engine2_liquidity_sweep(st, btc_state, now_ms)
                                if sig2:
                                    await oms.submit_signal(sig2)
                                    continue

                            # Приоритет 2 (v3.7): Движок 1 (OBI Breakout с фильтром макро-тренда BTC)
                            if getattr(cfg, "enable_e1_obi", True):
                                sig1 = evaluator.evaluate_engine1_obi_breakout(st, now_ms, btc_state)
                                if sig1:
                                    await oms.submit_signal(sig1)
                finally:
                    ping_task.cancel()

        except Exception as e:
            logger.error(f"Сбой WebSocket потока данных: {e}. Переподключение через 1.0 сек...")
            await asyncio.sleep(1.0)


async def periodic_metrics_update_task(screener: UniverseScreener, states: Dict[str, SymbolMicrostructureState]) -> None:
    """Периодическое (каждые 15 минут) обновление свинг-уровней и индикаторов инструментов."""
    while True:
        try:
            await asyncio.sleep(900.0)
            metas = {sym: st.meta for sym, st in states.items()}
            await screener.refresh_instrument_metrics(metas)
            logger.info(f"[METRICS_REFRESH] Обновлены свинг-уровни и индикаторы для {len(metas)} инструментов")
        except Exception as e:
            logger.warning(f"Ошибка при обновлении метрик инструментов: {e}")


async def main() -> None:
    cfg = ScalperConfig()
    logger.info(
        f"Запуск ядра BybitScalpCore v3.7 | "
        f"Комиссии: Maker={cfg.fee_maker*100:.4f}%, Taker={cfg.fee_taker*100:.4f}% "
        f"(Круг MM={cfg.roundtrip_mm*100:.3f}%, MT={cfg.roundtrip_mt*100:.3f}%) | "
        f"Режим: {'PAPER_TRADING' if cfg.paper_trading else 'LIVE_REAL'}"
    )

    screener = UniverseScreener(cfg)
    metas = await screener.build_active_universe()
    if not metas:
        logger.error("Не удалось сформировать активную вселенную тикеров. Завершение.")
        return

    for s, m in metas.items():
        logger.info(
            f"Инструмент [{s}]: NATR_5m={m.natr_5m*100:.2f}% | Beta={m.beta_btc:.2f} | "
            f"Corr={m.corr_btc:.2f} | Hurst={m.hurst_15m:.2f} | SwingLow={m.swing_low_1h} | SwingHigh={m.swing_high_1h}"
        )

    states = {sym: SymbolMicrostructureState(sym, meta) for sym, meta in metas.items()}
    evaluator = ThreeEnginesEvaluate(cfg)
    oms = ScalpOMS(cfg, states)

    global GLOBAL_OMS
    GLOBAL_OMS = oms

    await asyncio.gather(
        position_monitor_task(oms),
        run_public_stream(cfg, states, evaluator, oms),
        status_export_task(oms),
        periodic_metrics_update_task(screener, states),
        gc_guardian_task(oms),
    )


if __name__ == "__main__":
    asyncio.run(main())
