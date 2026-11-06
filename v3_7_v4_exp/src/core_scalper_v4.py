"""
v3.7-v4-exp Bybit NewDragon Scalp Core Daemon.
Integrates UniversalRisk minNotional Guard and 2-Stage Z_OFI Order Flow Imbalance Fast Scratch.
"""

import asyncio
from collections import deque
import gc
import json
import logging
from pathlib import Path
import sys
import time
from typing import Dict, Optional

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

try:
    import orjson
except ImportError:
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

from v3_7.src.microstructure import InstrumentMeta
from v3_7.src.scalp_screener import UniverseScreener
from v3_7_v4_exp.src.config import ScalperConfigV37V4, LIVE_STATUS_FILE, PERSISTENT_STATE_FILE
from v3_7_v4_exp.src.microstructure_v4 import SymbolMicrostructureStateV4
from v3_7_v4_exp.src.scalp_engines_v4 import ScalpEnginesV4
from v3_7_v4_exp.src.scalp_oms_v4 import ScalpOMSV4
from v3.src.persistence import atomic_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ScalperCore_v37_v4")

GLOBAL_OMS: Optional[ScalpOMSV4] = None


async def status_export_task(oms: ScalpOMSV4) -> None:
    """Exports status periodically to LIVE_STATUS_FILE and PERSISTENT_STATE_FILE."""
    last_heartbeat = 0.0
    while True:
        try:
            status = oms.get_status()
            atomic_json(LIVE_STATUS_FILE, status)
            now = time.time()
            if now - last_heartbeat >= 30.0:
                last_heartbeat = now
                atomic_json(PERSISTENT_STATE_FILE, status)
                syms = list(oms.states.keys()) if hasattr(oms, "states") else []
                pos_cnt = len(oms.active_positions)
                ord_cnt = len(oms.pending_orders)
                scratches = oms.scratch_count
                logger.info(
                    f"[L2 MONITOR v3.7-v4] Мониторинг {len(syms)} тикеров | "
                    f"Очередь: {ord_cnt} | В рынке: {pos_cnt} поз. | Z_OFI Scratches: {scratches}"
                )
        except Exception as e:
            logger.debug(f"Status export exception: {e}")
        await asyncio.sleep(2.0)


async def gc_guardian_task(oms: ScalpOMSV4) -> None:
    gc.disable()
    while True:
        await asyncio.sleep(60.0)
        if not oms.active_positions and not oms.pending_orders:
            gc.collect(1)


async def position_monitor_task(oms: ScalpOMSV4) -> None:
    while True:
        now_ms = int(time.time() * 1000)
        await oms.monitor_step(now_ms)
        await asyncio.sleep(0.025)  # 25ms HFT evaluation frequency


async def run_public_stream(
    cfg: ScalperConfigV37V4,
    states: Dict[str, SymbolMicrostructureStateV4],
    evaluator: ScalpEnginesV4,
    oms: ScalpOMSV4,
) -> None:
    topics = []
    for sym in states:
        topics.append(f"orderbook.50.{sym}")
        topics.append(f"publicTrade.{sym}")

    chunk_size = 10
    chunks = [topics[i:i + chunk_size] for i in range(0, len(topics), chunk_size)]
    btc_sym = cfg.leader_symbol

    while True:
        try:
            logger.info(f"Connecting to Bybit Public WebSocket: {cfg.ws_public_url}")
            async with websockets.connect(
                cfg.ws_public_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=10 * 1024 * 1024,
            ) as ws:
                for ch in chunks:
                    sub_msg = {"op": "subscribe", "args": ch}
                    await ws.send(orjson.dumps(sub_msg).decode("utf-8"))
                    await asyncio.sleep(0.05)

                logger.info(f"Subscribed to {len(topics)} topics across {len(chunks)} chunks.")

                async for raw_msg in ws:
                    msg = orjson.loads(raw_msg)
                    if "topic" not in msg:
                        continue
                    topic = msg["topic"]
                    data = msg.get("data", {})
                    ts_ms = msg.get("ts", int(time.time() * 1000))

                    if topic.startswith("orderbook.50."):
                        sym = topic.split(".")[-1]
                        st = states.get(sym)
                        if st:
                            st.apply_orderbook(msg.get("type", "delta"), data, ts_ms)
                            # Evaluate signals on orderbook updates
                            btc_st = states.get(btc_sym)
                            intent = evaluator.evaluate_all(st, btc_st, ts_ms)
                            if intent:
                                await oms.on_signal(intent)

                    elif topic.startswith("publicTrade."):
                        sym = topic.split(".")[-1]
                        st = states.get(sym)
                        if st and isinstance(data, list):
                            for tr in data:
                                st.apply_trade(tr)

        except (websockets.ConnectionClosed, Exception) as e:
            logger.warning(f"WebSocket public connection lost: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3.0)


async def main() -> None:
    global GLOBAL_OMS
    cfg = ScalperConfigV37V4()

    screener = UniverseScreener(
        min_turnover_24h_usdt=10_000_000.0,
        max_symbols=cfg.max_active_symbols,
        blacklist=["AKEUSDT", "TRUMPUSDT", "ELSAUSDT", "WLDUSDT"],
    )
    symbols = screener.get_top_universe()
    if cfg.leader_symbol not in symbols:
        symbols.insert(0, cfg.leader_symbol)

    logger.info(f"Initialized v3.7-v4 Universe with {len(symbols)} coins: {symbols}")

    states: Dict[str, SymbolMicrostructureStateV4] = {}
    for sym in symbols:
        meta = InstrumentMeta(
            symbol=sym,
            tick_size=0.01 if "BTC" not in sym else 0.1,
            qty_step=0.001 if "BTC" not in sym else 0.001,
            min_qty=0.001,
        )
        states[sym] = SymbolMicrostructureStateV4(sym, meta)

    evaluator = ScalpEnginesV4(cfg)
    oms = ScalpOMSV4(cfg, states)
    GLOBAL_OMS = oms

    tasks = [
        asyncio.create_task(position_monitor_task(oms)),
        asyncio.create_task(status_export_task(oms)),
        asyncio.create_task(gc_guardian_task(oms)),
        asyncio.create_task(run_public_stream(cfg, states, evaluator, oms)),
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Main loop cancelled, shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
