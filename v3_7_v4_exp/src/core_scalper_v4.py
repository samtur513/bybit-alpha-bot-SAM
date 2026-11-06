"""
v3.7-v4-exp Bybit NewDragon Scalp Core Daemon.
Integrates UniversalRisk minNotional Guard, 2-Stage Z_OFI Order Flow Imbalance Fast Scratch,
KER Chop Gate, and High-Fidelity L2 Queue Simulation.
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


async def periodic_metrics_update_task(screener: UniverseScreener, states: Dict[str, SymbolMicrostructureStateV4]) -> None:
    """Refreshes swing levels and technical metrics periodically without interrupting stream."""
    while True:
        try:
            await asyncio.sleep(900.0)
            metas = {sym: st.meta for sym, st in states.items()}
            await screener.refresh_instrument_metrics(metas)
            logger.info(f"[METRICS_REFRESH] Refreshed metrics for {len(metas)} symbols.")
        except Exception as e:
            logger.warning(f"Error in metrics refresh task: {e}")


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
                ping_interval=None,
                ping_timeout=None,
                close_timeout=5,
                max_size=10 * 1024 * 1024,
            ) as ws:
                for ch in chunks:
                    sub_msg = {"op": "subscribe", "args": ch}
                    await ws.send(orjson.dumps(sub_msg).decode("utf-8"))
                    await asyncio.sleep(0.05)

                logger.info(f"Subscribed to {len(topics)} topics across {len(chunks)} chunks.")

                # Keepalive ping task
                async def ping_bybit():
                    while True:
                        await asyncio.sleep(20.0)
                        try:
                            await ws.send(orjson.dumps({"op": "ping"}).decode("utf-8"))
                        except Exception:
                            break

                ping_task = asyncio.create_task(ping_bybit())
                try:
                    async for raw_msg in ws:
                        msg = orjson.loads(raw_msg)
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

                        btc_state = states.get(btc_sym)
                        if not btc_state:
                            continue

                        # Signal Evaluation:
                        # 1. If BTC updated and E3 Lead-Lag enabled: check all altcoins
                        if sym == btc_sym:
                            if getattr(cfg, "enable_e3_leadlag", True):
                                for alt_sym, alt_st in states.items():
                                    if alt_sym == btc_sym:
                                        continue
                                    sig3 = evaluator.evaluate_engine3_lead_lag(alt_st, btc_state, now_ms)
                                    if sig3:
                                        await oms.submit_signal(sig3)
                        else:
                            # 2. Altcoin updated: evaluate E2 and E3 with KER chop filter
                            intent = evaluator.evaluate_all(st, btc_state, now_ms)
                            if intent:
                                await oms.submit_signal(intent)

                finally:
                    ping_task.cancel()

        except (websockets.ConnectionClosed, Exception) as e:
            logger.warning(f"WebSocket public connection lost: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3.0)


async def main() -> None:
    global GLOBAL_OMS
    cfg = ScalperConfigV37V4(persist_state=True)

    logger.info(
        f"Starting BybitScalpCore v3.7-v4 | "
        f"Maker={cfg.fee_maker*100:.4f}%, Taker={cfg.fee_taker*100:.4f}% | "
        f"Mode={'PAPER_TRADING' if cfg.paper_trading else 'LIVE_REAL'}"
    )

    screener = UniverseScreener(cfg)
    try:
        metas = await screener.build_active_universe()
    except Exception as e:
        logger.warning(f"Screening error: {e}")
        metas = {}

    if not metas:
        fallback_syms = [cfg.leader_symbol, "ETHUSDT", "SOLUSDT", "NEARUSDT", "SUIUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]
        metas = {}
        for s in fallback_syms:
            metas[s] = InstrumentMeta(
                symbol=s,
                tick_size=0.1 if "BTC" in s else 0.01,
                qty_step=0.001,
                min_qty=0.001,
                natr_5m=0.005,
                atr_5m=1.0,
                atr_15m=1.75,
                hurst_15m=0.5,
                er_15m=0.3,
            )

    logger.info(f"Initialized v3.7-v4 Universe with {len(metas)} coins: {list(metas.keys())}")

    states: Dict[str, SymbolMicrostructureStateV4] = {}
    for sym, meta in metas.items():
        states[sym] = SymbolMicrostructureStateV4(sym, meta)

    evaluator = ScalpEnginesV4(cfg)
    oms = ScalpOMSV4(cfg, states)
    GLOBAL_OMS = oms

    tasks = [
        asyncio.create_task(position_monitor_task(oms)),
        asyncio.create_task(status_export_task(oms)),
        asyncio.create_task(gc_guardian_task(oms)),
        asyncio.create_task(periodic_metrics_update_task(screener, states)),
        asyncio.create_task(run_public_stream(cfg, states, evaluator, oms)),
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Main loop cancelled, shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
