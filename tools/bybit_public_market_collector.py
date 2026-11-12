"""
Public Market Data Collector for Bybit V5 Linear Perpetuals & Spot.
Strict Invariants:
1. Batching limit: max 10 topics per subscribe frame.
2. Custom keepalive: ping_interval=None, application JSON {"op": "ping"} every 20 seconds.
3. Read-only zero-credentials: works purely on public unauthenticated endpoints.
4. Windows Selector Policy on win32.
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import websockets

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | COLLECTOR | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("BybitPublicCollector")

WS_LINEAR_URL = "wss://stream.bybit.com/v5/public/linear"
WS_SPOT_URL = "wss://stream.bybit.com/v5/public/spot"

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT",
    "AVAXUSDT", "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT",
]


class BybitPublicMarketCollector:
    """Autonomous collector for real-time Bybit Spot, Tickers, and Public Trades."""

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        output_dir: Optional[Path] = None,
        collect_spot_bbo: bool = True,
        collect_funding_tickers: bool = True,
        collect_public_trades: bool = False,
    ):
        self.symbols = symbols or list(DEFAULT_SYMBOLS)
        self.output_dir = output_dir or (Path(__file__).resolve().parent.parent / "data" / "collected_market_data")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.collect_spot_bbo = collect_spot_bbo
        self.collect_funding_tickers = collect_funding_tickers
        self.collect_public_trades = collect_public_trades

        self.is_running = False
        self.records_collected: int = 0
        self.latest_spot_bbo: Dict[str, dict] = {}
        self.latest_funding_rates: Dict[str, dict] = {}
        self.latest_trades: Dict[str, list] = {}

    def _chunk_topics(self, topics: List[str], chunk_size: int = 10) -> List[List[str]]:
        """Strict Bybit V5 invariant: max 10 topics per subscribe frame."""
        return [topics[i : i + chunk_size] for i in range(0, len(topics), chunk_size)]

    async def _send_keepalive(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Strict Bybit keepalive invariant: ping every 20s via application JSON."""
        while self.is_running:
            await asyncio.sleep(20.0)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception as e:
                logger.debug(f"Keepalive ping failed: {e}")
                break

    async def run_linear_collector(self, max_seconds: Optional[float] = None) -> None:
        """Subscribes to Linear Perpetuals tickers and public trades."""
        topics = []
        if self.collect_funding_tickers:
            topics.extend([f"tickers.{s}" for s in self.symbols])
        if self.collect_public_trades:
            topics.extend([f"publicTrade.{s}" for s in self.symbols])

        if not topics:
            return

        chunks = self._chunk_topics(topics, 10)
        start_time = time.time()

        while self.is_running:
            try:
                async with websockets.connect(
                    WS_LINEAR_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    max_queue=4096,
                    close_timeout=5,
                ) as ws:
                    logger.info(f"Connected to Bybit Linear WS. Subscribing to {len(topics)} topics in {len(chunks)} chunks...")
                    for ch in chunks:
                        await ws.send(json.dumps({"op": "subscribe", "args": ch}))
                        await asyncio.sleep(0.05)

                    ping_task = asyncio.create_task(self._send_keepalive(ws))
                    try:
                        async for raw in ws:
                            if not self.is_running:
                                break
                            if max_seconds and (time.time() - start_time) >= max_seconds:
                                break

                            msg = json.loads(raw)
                            topic = msg.get("topic", "")
                            if not topic:
                                continue

                            self.records_collected += 1
                            if topic.startswith("tickers."):
                                sym = topic.split(".")[-1]
                                data = msg.get("data", {})
                                self.latest_funding_rates[sym] = {
                                    "symbol": sym,
                                    "funding_rate": float(data.get("fundingRate", 0.0) or 0.0),
                                    "next_funding_time": int(data.get("nextFundingTime", 0) or 0),
                                    "mark_price": float(data.get("markPrice", 0.0) or 0.0),
                                    "ts": int(msg.get("ts", time.time() * 1000)),
                                }
                            elif topic.startswith("publicTrade."):
                                sym = topic.split(".")[-1]
                                trades = msg.get("data", [])
                                self.latest_trades.setdefault(sym, []).extend(trades)
                                if len(self.latest_trades[sym]) > 1000:
                                    self.latest_trades[sym] = self.latest_trades[sym][-1000:]
                    finally:
                        ping_task.cancel()
            except Exception as e:
                logger.warning(f"Linear WS disconnected: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3.0)

            if max_seconds and (time.time() - start_time) >= max_seconds:
                break

    async def run_spot_collector(self, max_seconds: Optional[float] = None) -> None:
        """Subscribes to Bybit Spot orderbook.1 streams for exact Spot BBO."""
        if not self.collect_spot_bbo:
            return

        topics = [f"orderbook.1.{s}" for s in self.symbols]
        chunks = self._chunk_topics(topics, 10)
        start_time = time.time()

        while self.is_running:
            try:
                async with websockets.connect(
                    WS_SPOT_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    max_queue=4096,
                    close_timeout=5,
                ) as ws:
                    logger.info(f"Connected to Bybit Spot WS. Subscribing to {len(topics)} topics in {len(chunks)} chunks...")
                    for ch in chunks:
                        await ws.send(json.dumps({"op": "subscribe", "args": ch}))
                        await asyncio.sleep(0.05)

                    ping_task = asyncio.create_task(self._send_keepalive(ws))
                    try:
                        async for raw in ws:
                            if not self.is_running:
                                break
                            if max_seconds and (time.time() - start_time) >= max_seconds:
                                break

                            msg = json.loads(raw)
                            topic = msg.get("topic", "")
                            if not topic or not topic.startswith("orderbook.1"):
                                continue

                            self.records_collected += 1
                            sym = topic.split(".")[-1]
                            data = msg.get("data", {})
                            bids = data.get("b", [])
                            asks = data.get("a", [])
                            best_bid = float(bids[0][0]) if bids else 0.0
                            best_ask = float(asks[0][0]) if asks else 0.0

                            if best_bid > 0 and best_ask > 0:
                                self.latest_spot_bbo[sym] = {
                                    "symbol": sym,
                                    "bid": best_bid,
                                    "ask": best_ask,
                                    "mid": 0.5 * (best_bid + best_ask),
                                    "ts": int(msg.get("ts", time.time() * 1000)),
                                }
                    finally:
                        ping_task.cancel()
            except Exception as e:
                logger.warning(f"Spot WS disconnected: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3.0)

            if max_seconds and (time.time() - start_time) >= max_seconds:
                break

    async def start(self, duration_seconds: Optional[float] = None) -> None:
        """Runs the combined market data collector."""
        self.is_running = True
        tasks = [
            asyncio.create_task(self.run_linear_collector(duration_seconds)),
            asyncio.create_task(self.run_spot_collector(duration_seconds)),
        ]
        await asyncio.gather(*tasks)

    def stop(self) -> None:
        self.is_running = False
