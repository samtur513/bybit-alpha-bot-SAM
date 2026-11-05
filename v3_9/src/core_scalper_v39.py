"""
v3.9 Bybit NewDragon Scalp Core Daemon.
Integrates Micro-Router (minNotional enforcement) and Z_OFI Order Flow Imbalance Fast Scratch.
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

from v3_7.src.scalp_engines import ThreeEnginesEvaluate
from v3_7.src.scalp_screener import UniverseScreener
from v3_9.src.config import ScalperConfigV39, LIVE_STATUS_FILE
from v3_9.src.microstructure_v39 import SymbolMicrostructureStateV39
from v3_9.src.scalp_oms_v39 import ScalpOMSV39
from v3.src.persistence import atomic_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ScalperCore_v39")

GLOBAL_OMS: Optional[ScalpOMSV39] = None


async def status_export_task(oms: ScalpOMSV39) -> None:
    """Exports status periodically to LIVE_STATUS_FILE."""
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
                scratches = oms.scratch_count
                logger.info(
                    f"[L2 MONITOR 25ms v3.9] Мониторинг {len(syms)} тикеров: {syms} | "
                    f"Очередь: {ord_cnt} | В рынке: {pos_cnt} поз. | Z_OFI Scratches: {scratches}"
                )
        except Exception as e:
            logger.debug(f"Status export exception: {e}")
        await asyncio.sleep(2.0)


async def gc_guardian_task(oms: ScalpOMSV39) -> None:
    gc.disable()
    while True:
        await asyncio.sleep(60.0)
        if not oms.active_positions and not oms.pending_orders:
            gc.collect(1)


async def position_monitor_task(oms: ScalpOMSV39) -> None:
    while True:
        now_ms = int(time.time() * 1000)
        await oms.monitor_step(now_ms)
        await asyncio.sleep(0.100)


async def run_public_stream(
    cfg: ScalperConfigV39,
    states: Dict[str, SymbolMicrostructureStateV39],
    evaluator: ThreeEnginesEvaluate,
    oms: ScalpOMSV39,
) -> None:
    topics = []
    for sym in states:
        topics.append(f"orderbook.50.{sym}")
        topics.append(f"publicTrade.{sym}")

    while True:
        try:
            async with websockets.connect(cfg.ws_public_url, ping_interval=20, ping_timeout=20, max_queue=4096) as ws:
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
                            for tr in trade_data:
                                is_buy = tr.get("S") == "Buy" or tr.get("side") == "Buy"
                                price = float(tr.get("p", tr.get("price", 0.0)))
                                qty = float(tr.get("v", tr.get("size", 0.0)))
                                oms.on_market_trade(sym, is_buy, price, qty)

                        btc_state = states.get(cfg.leader_symbol)
                        if not btc_state:
                            continue

                        if sym == cfg.leader_symbol:
                            for alt_sym, alt_st in states.items():
                                if alt_sym == cfg.leader_symbol:
                                    continue
                                sig3 = evaluator.evaluate_engine3_lead_lag(alt_st, btc_state, now_ms)
                                if sig3:
                                    await oms.submit_signal(sig3)
                        else:
                            sig3 = evaluator.evaluate_engine3_lead_lag(st, btc_state, now_ms)
                            if sig3:
                                await oms.submit_signal(sig3)
                                continue

                            sig2 = evaluator.evaluate_engine2_liquidity_sweep(st, btc_state, now_ms)
                            if sig2:
                                await oms.submit_signal(sig2)
                                continue

                            sig1 = evaluator.evaluate_engine1_obi_breakout(st, now_ms, btc_state)
                            if sig1:
                                await oms.submit_signal(sig1)
                finally:
                    ping_task.cancel()

        except Exception as e:
            logger.error(f"Сбой WebSocket: {e}. Реконнект через 1.0 сек...")
            await asyncio.sleep(1.0)


async def periodic_metrics_update_task(screener: UniverseScreener, states: Dict[str, SymbolMicrostructureStateV39]) -> None:
    while True:
        try:
            await asyncio.sleep(900.0)
            metas = {sym: st.meta for sym, st in states.items()}
            await screener.refresh_instrument_metrics(metas)
            logger.info(f"[METRICS_REFRESH] Обновлены метрики для {len(metas)} инструментов")
        except Exception as e:
            logger.warning(f"Ошибка обновления метрик: {e}")


async def main() -> None:
    cfg = ScalperConfigV39()
    logger.info(
        f"Запуск ядра BybitScalpCore v3.9 NewDragon Scalp Lab | "
        f"Комиссии: Maker={cfg.fee_maker*100:.4f}%, Taker={cfg.fee_taker*100:.4f}% | "
        f"Z_OFI Scratch={cfg.enable_z_ofi_scratch} (threshold={cfg.z_ofi_scratch_threshold}) | "
        f"Micro-Router minNotional={cfg.enforce_min_notional}"
    )

    screener = UniverseScreener(cfg)
    metas = await screener.build_active_universe()
    if not metas:
        logger.error("Не удалось сформировать активную вселенную тикеров.")
        return

    states = {sym: SymbolMicrostructureStateV39(sym, meta) for sym, meta in metas.items()}
    evaluator = ThreeEnginesEvaluate(cfg)
    oms = ScalpOMSV39(cfg)
    oms.states = states

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
