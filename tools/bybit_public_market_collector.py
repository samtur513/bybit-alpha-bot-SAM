"""
Production-Grade Public Market Data Collector for Bybit V5 Linear Perpetuals & Spot.
Auditor Verification Script (FLEET-ALPHA-VERIFY-005).

Features:
1. Subscriptions:
   - Linear L2 Depth: orderbook.50.<symbol>
   - Linear Trades: publicTrade.<symbol>
   - Linear Funding & Tickers: tickers.<symbol>
   - Spot BBO: orderbook.1.<symbol>
2. Invariants:
   - Batching limit: max 10 topics per subscribe frame.
   - Custom keepalive: ping_interval=None, ping_timeout=None, application JSON {"op": "ping"} every 20s.
   - Read-only zero-credentials: operates strictly on public Bybit V5 WebSocket streams.
   - Windows Selector Policy on win32.
3. Event log recording with exchange ts, local recv_ts, and latency tracking.
4. Real-time L2 orderbook reconstruction with snapshot + delta application and gap detection.
5. Stale data watchdog (DATA_STALE flag if no updates within threshold).
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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


@dataclass
class L2Orderbook:
    """Maintains an L2 depth orderbook for a symbol from Bybit V5 snapshot & deltas."""
    symbol: str
    bids: Dict[float, float] = field(default_factory=dict)  # price -> size
    asks: Dict[float, float] = field(default_factory=dict)  # price -> size
    last_u: int = 0
    last_seq: int = 0
    last_update_ts: int = 0
    is_synchronized: bool = False
    gaps_detected: int = 0

    def apply_snapshot(self, data: dict, ts: int) -> None:
        self.bids.clear()
        self.asks.clear()
        for p, s in data.get("b", []):
            self.bids[float(p)] = float(s)
        for p, s in data.get("a", []):
            self.asks[float(p)] = float(s)
        self.last_u = int(data.get("u", 0))
        self.last_seq = int(data.get("seq", 0))
        self.last_update_ts = ts
        self.is_synchronized = True

    def apply_delta(self, data: dict, ts: int) -> bool:
        """
        Applies delta update according to Bybit V5 WebSocket protocol:
        - Requires book to be currently synchronized (snapshot already received).
        - Update ID 'u' must equal last_u + 1. If u != last_u + 1, packet loss / gap detected.
        - Cross-sequence 'seq' must be strictly monotonic (seq > last_seq).
        - Returns True if applied cleanly; False if sequence break detected (invalidates book).
        """
        u = int(data.get("u", 0))
        seq = int(data.get("seq", 0))

        if not self.is_synchronized:
            return False

        # Sequence gap check on Bybit V5 update id 'u'
        if self.last_u > 0 and u != (self.last_u + 1):
            self.gaps_detected += 1
            self.is_synchronized = False
            logger.warning(
                f"[L2 GAP DETECTED] {self.symbol}: expected u={self.last_u + 1}, got u={u}. Book invalidated until next snapshot."
            )
            return False

        # Sequence monotonicity check on Bybit V5 'seq'
        if self.last_seq > 0 and seq <= self.last_seq:
            self.gaps_detected += 1
            self.is_synchronized = False
            logger.warning(
                f"[L2 MONOTONICITY VIOLATION] {self.symbol}: last_seq={self.last_seq}, incoming seq={seq}. Book invalidated."
            )
            return False

        # Apply bid updates (size == 0 means level deleted)
        for p_str, s_str in data.get("b", []):
            p = float(p_str)
            s = float(s_str)
            if s == 0.0:
                self.bids.pop(p, None)
            else:
                self.bids[p] = s

        # Apply ask updates
        for p_str, s_str in data.get("a", []):
            p = float(p_str)
            s = float(s_str)
            if s == 0.0:
                self.asks.pop(p, None)
            else:
                self.asks[p] = s

        self.last_u = u
        self.last_seq = seq
        self.last_update_ts = ts
        self.is_synchronized = True
        return True

    def is_valid_for_execution(self, max_stale_ms: int = 5000) -> bool:
        """
        Enforces L2 Orderbook execution gate:
        - Must be synchronized (snapshot applied and consecutive deltas).
        - Must have valid non-crossed bids and asks (best_bid < best_ask).
        - Must not be stale (> max_stale_ms silence).
        """
        if not self.is_synchronized:
            return False
        if not self.bids or not self.asks:
            return False
        best_bid, best_ask = self.get_bbo()
        if best_bid is None or best_ask is None or best_bid >= best_ask:
            return False
        now_ms = int(time.time() * 1000)
        if (now_ms - self.last_update_ts) > max_stale_ms:
            return False
        return True

    def get_bbo(self) -> Tuple[Optional[float], Optional[float]]:
        best_bid = max(self.bids.keys()) if self.bids else None
        best_ask = min(self.asks.keys()) if self.asks else None
        return best_bid, best_ask


class BybitPublicMarketCollector:
    """
    Robust collector capturing real-time market data across Bybit V5 Linear & Spot.
    Writes raw event log with deterministic replay support.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        output_dir: Optional[Path] = None,
        collect_orderbook_l2: bool = True,
        collect_public_trades: bool = True,
        collect_funding_tickers: bool = True,
        collect_spot_bbo: bool = True,
        log_filename: str = "bybit_market_stream.jsonl",
    ):
        self.symbols = symbols or list(DEFAULT_SYMBOLS)
        self.output_dir = output_dir or (Path(__file__).resolve().parent.parent / "data" / "collected_market_data")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_file_path = self.output_dir / log_filename

        self.collect_orderbook_l2 = collect_orderbook_l2
        self.collect_public_trades = collect_public_trades
        self.collect_funding_tickers = collect_funding_tickers
        self.collect_spot_bbo = collect_spot_bbo

        self.is_running = False
        self.records_collected: int = 0
        self.records_by_topic: Dict[str, int] = defaultdict(int)
        self.latencies_ms: List[float] = []

        # Real-time state
        self.orderbooks: Dict[str, L2Orderbook] = {s: L2Orderbook(symbol=s) for s in self.symbols}
        self.spot_bbo: Dict[str, dict] = {}
        self.latest_tickers: Dict[str, dict] = {}
        self.recent_trades: Dict[str, list] = defaultdict(list)
        self.last_msg_time: Dict[str, float] = {}  # symbol -> local monotonic ts

        # Watchdog thresholds
        self.stale_threshold_ob_sec = 5.0
        self.stale_threshold_ticker_sec = 60.0

        self.event_callbacks: List[Callable[[dict], None]] = []
        self._log_file_handle = None

    def _chunk_topics(self, topics: List[str], chunk_size: int = 10) -> List[List[str]]:
        """Strict Bybit V5 invariant: max 10 topics per subscribe frame."""
        return [topics[i : i + chunk_size] for i in range(0, len(topics), chunk_size)]

    async def _send_keepalive(self, ws) -> None:
        """Strict Bybit keepalive invariant: ping every 20s via application JSON."""
        while self.is_running:
            await asyncio.sleep(20.0)
            try:
                await ws.send(json.dumps({"op": "ping"}))
            except Exception as e:
                logger.debug(f"Keepalive ping failed: {e}")
                break

    def _process_linear_message(self, raw_msg: str, recv_ts: int) -> None:
        try:
            msg = json.loads(raw_msg)
        except Exception:
            return

        topic = msg.get("topic", "")
        if not topic:
            return

        exchange_ts = int(msg.get("ts", recv_ts))
        latency = float(recv_ts - exchange_ts)
        self.latencies_ms.append(latency)

        data = msg.get("data")
        msg_type = msg.get("type", "")

        # 1. Orderbook L2 depth (orderbook.50.<symbol>)
        if topic.startswith("orderbook."):
            parts = topic.split(".")
            symbol = parts[2] if len(parts) >= 3 else ""
            if symbol in self.orderbooks and isinstance(data, dict):
                ob = self.orderbooks[symbol]
                if msg_type == "snapshot":
                    ob.apply_snapshot(data, exchange_ts)
                elif msg_type == "delta":
                    ob.apply_delta(data, exchange_ts)
                self.records_by_topic["orderbook"] += 1
                self.last_msg_time[symbol] = time.time()

        # 2. Public Trades (publicTrade.<symbol>)
        elif topic.startswith("publicTrade."):
            parts = topic.split(".")
            symbol = parts[1] if len(parts) >= 2 else ""
            if isinstance(data, list):
                self.recent_trades[symbol].extend(data[-10:])
                self.records_by_topic["publicTrade"] += len(data)
                self.last_msg_time[symbol] = time.time()

        # 3. Tickers / Funding (tickers.<symbol>)
        elif topic.startswith("tickers."):
            parts = topic.split(".")
            symbol = parts[1] if len(parts) >= 2 else ""
            if isinstance(data, dict):
                self.latest_tickers[symbol] = data
                self.records_by_topic["tickers"] += 1
                self.last_msg_time[symbol] = time.time()

        self.records_collected += 1

        # Write to event log
        if self._log_file_handle:
            entry = {
                "exchange_ts": exchange_ts,
                "recv_ts": recv_ts,
                "latency_ms": latency,
                "topic": topic,
                "type": msg_type,
                "raw": msg,
            }
            self._log_file_handle.write(json.dumps(entry) + "\n")

        # Fire callbacks
        for cb in self.event_callbacks:
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _process_spot_message(self, raw_msg: str, recv_ts: int) -> None:
        try:
            msg = json.loads(raw_msg)
        except Exception:
            return

        topic = msg.get("topic", "")
        if not topic:
            return

        exchange_ts = int(msg.get("ts", recv_ts))
        latency = float(recv_ts - exchange_ts)
        self.latencies_ms.append(latency)

        data = msg.get("data")
        if topic.startswith("orderbook.") and isinstance(data, dict):
            parts = topic.split(".")
            symbol = parts[2] if len(parts) >= 3 else ""
            bids = data.get("b", [])
            asks = data.get("a", [])
            best_bid = float(bids[0][0]) if bids else None
            best_ask = float(asks[0][0]) if asks else None
            if best_bid and best_ask:
                self.spot_bbo[symbol] = {
                    "bid": best_bid,
                    "ask": best_ask,
                    "mid": 0.5 * (best_bid + best_ask),
                    "ts": exchange_ts,
                }
                self.records_by_topic["spot_orderbook"] += 1

        self.records_collected += 1

        if self._log_file_handle:
            entry = {
                "exchange_ts": exchange_ts,
                "recv_ts": recv_ts,
                "latency_ms": latency,
                "topic": topic,
                "raw": msg,
            }
            self._log_file_handle.write(json.dumps(entry) + "\n")

    def check_stale_data(self) -> Dict[str, bool]:
        """Watchdog check returning stale flags for all symbols."""
        now = time.time()
        stale_status = {}
        for sym in self.symbols:
            last = self.last_msg_time.get(sym, 0.0)
            elapsed = now - last
            stale_status[sym] = elapsed > self.stale_threshold_ob_sec
        return stale_status

    async def run_linear_collector(self, max_seconds: Optional[float] = None) -> None:
        """Connects to Bybit V5 Linear WebSocket and streams events."""
        topics = []
        if self.collect_orderbook_l2:
            topics.extend([f"orderbook.50.{s}" for s in self.symbols])
        if self.collect_public_trades:
            topics.extend([f"publicTrade.{s}" for s in self.symbols])
        if self.collect_funding_tickers:
            topics.extend([f"tickers.{s}" for s in self.symbols])

        if not topics:
            return

        chunks = self._chunk_topics(topics, 10)
        start_time = time.time()
        backoff = 1.0

        while self.is_running:
            try:
                async with websockets.connect(
                    WS_LINEAR_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    max_queue=4096,
                    close_timeout=5,
                ) as ws:
                    logger.info(f"Connected to Bybit V5 Linear WebSocket: {len(topics)} topics across {len(chunks)} batches.")
                    backoff = 1.0
                    ping_task = asyncio.create_task(self._send_keepalive(ws))

                    for chunk in chunks:
                        sub_msg = {"op": "subscribe", "args": chunk}
                        await ws.send(json.dumps(sub_msg))
                        await asyncio.sleep(0.05)  # Rate limit pacing

                    while self.is_running:
                        if max_seconds and (time.time() - start_time) >= max_seconds:
                            self.is_running = False
                            break

                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            recv_ts = int(time.time() * 1000)
                            self._process_linear_message(raw_msg, recv_ts)
                        except asyncio.TimeoutError:
                            continue

                    ping_task.cancel()
                    break

            except Exception as e:
                logger.warning(f"Linear WS connection error: {e}. Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def run_spot_collector(self, max_seconds: Optional[float] = None) -> None:
        """Connects to Bybit V5 Spot WebSocket for BBO."""
        if not self.collect_spot_bbo:
            return

        topics = [f"orderbook.1.{s}" for s in self.symbols]
        chunks = self._chunk_topics(topics, 10)
        start_time = time.time()
        backoff = 1.0

        while self.is_running:
            try:
                async with websockets.connect(
                    WS_SPOT_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    max_queue=4096,
                    close_timeout=5,
                ) as ws:
                    logger.info(f"Connected to Bybit V5 Spot WebSocket: {len(topics)} topics across {len(chunks)} batches.")
                    backoff = 1.0
                    ping_task = asyncio.create_task(self._send_keepalive(ws))

                    for chunk in chunks:
                        sub_msg = {"op": "subscribe", "args": chunk}
                        await ws.send(json.dumps(sub_msg))
                        await asyncio.sleep(0.05)

                    while self.is_running:
                        if max_seconds and (time.time() - start_time) >= max_seconds:
                            self.is_running = False
                            break

                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            recv_ts = int(time.time() * 1000)
                            self._process_spot_message(raw_msg, recv_ts)
                        except asyncio.TimeoutError:
                            continue

                    ping_task.cancel()
                    break

            except Exception as e:
                logger.warning(f"Spot WS connection error: {e}. Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def run(self, max_seconds: Optional[float] = None) -> None:
        """Runs concurrent Linear and Spot collectors."""
        self.is_running = True
        self._log_file_handle = open(self.log_file_path, "a", encoding="utf-8")
        logger.info(f"Starting collector for {max_seconds or 'infinite'} seconds. Writing to {self.log_file_path.name}")

        try:
            tasks = [self.run_linear_collector(max_seconds)]
            if self.collect_spot_bbo:
                tasks.append(self.spot_spot_collector(max_seconds) if hasattr(self, "spot_spot_collector") else self.run_spot_collector(max_seconds))
            await asyncio.gather(*tasks)
        finally:
            self.is_running = False
            if self._log_file_handle:
                self._log_file_handle.flush()
                self._log_file_handle.close()
                self._log_file_handle = None
            logger.info(f"Collector stopped. Total records: {self.records_collected}")
