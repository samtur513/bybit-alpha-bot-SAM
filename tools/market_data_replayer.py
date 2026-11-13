"""
Deterministic Market Data Stream Replayer for Bybit V5 Public Capture.
FLEET-DATA-FIRST-007 Step 2.

Features:
1. Reads captured JSONL stream (e.g. reports/PUBLIC_CAPTURE_RAW_SANITIZED.jsonl).
2. Operates entirely on Virtual Replay Time Clock (no dependency on system wall clock).
3. Reconstructs L2 Depth (orderbook.50) and Spot BBO (orderbook.1) with sequence gap checking.
4. Passes virtual_clock_ms to L2Orderbook.is_valid_for_execution to prevent false staleness.
5. Emits structured market events (L2, BBO, Ticker/Funding, Trades) to consumers/strategies.
6. Deterministic, reproducible, zero credentials required.
"""

from dataclasses import dataclass, field
from decimal import Decimal
import gzip
import json
import logging
from pathlib import Path
import sys
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.bybit_public_market_collector import L2Orderbook

logging.basicConfig(level=logging.INFO, format="%(asctime)s | REPLAYER | %(message)s")
logger = logging.getLogger("MarketDataReplayer")


@dataclass
class MarketEvent:
    """Normalized market event yielded by the Replayer."""
    event_type: str  # 'ORDERBOOK_L2', 'SPOT_BBO', 'TICKER_FUNDING', 'TRADE'
    symbol: str
    exchange_ts_ms: int
    virtual_clock_ms: int
    data: Dict[str, Any]
    topic: str


class MarketDataReplayer:
    """
    Deterministic stream player that reads serialized Bybit V5 public capture
    and manages orderbook state strictly via virtual replay time.
    """

    def __init__(self, capture_path: Path):
        self.capture_path = Path(capture_path)
        if not self.capture_path.exists():
            raise FileNotFoundError(f"Capture file not found: {self.capture_path}")

        self.virtual_clock_ms: int = 0
        self.start_clock_ms: Optional[int] = None
        self.end_clock_ms: Optional[int] = None
        self.total_raw_events: int = 0
        self.gaps_detected: int = 0

        # State storage
        self.linear_books: Dict[str, L2Orderbook] = {}
        self.spot_bbo: Dict[str, Dict[str, Any]] = {}
        self.latest_tickers: Dict[str, Dict[str, Any]] = {}

    def iter_events(self) -> Generator[MarketEvent, None, None]:
        """
        Iterates over the JSONL file, updates internal state, and yields normalized MarketEvents.
        All checks use virtual_clock_ms.
        """
        is_gz = str(self.capture_path).endswith(".gz")
        open_fn = gzip.open if is_gz else open
        mode = "rt" if is_gz else "r"
        with open_fn(self.capture_path, mode, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    raw_record = json.loads(line)
                except Exception as e:
                    logger.warning(f"Failed to parse line {line_no}: {e}")
                    continue

                self.total_raw_events += 1
                topic = raw_record.get("topic", "")
                raw_payload = raw_record.get("raw", {})
                data = raw_payload.get("data", {})
                msg_type = raw_payload.get("type", "")

                # Advance Virtual Replay Clock strictly monotonically
                exchange_ts = raw_payload.get("ts", raw_record.get("exchange_ts", 0))
                if exchange_ts > self.virtual_clock_ms:
                    self.virtual_clock_ms = exchange_ts

                if self.start_clock_ms is None:
                    self.start_clock_ms = self.virtual_clock_ms
                self.end_clock_ms = self.virtual_clock_ms

                # 1. Linear L2 Depth (orderbook.50)
                if topic.startswith("orderbook.50."):
                    parts = topic.split(".")
                    symbol = parts[2] if len(parts) >= 3 else ""
                    if symbol not in self.linear_books:
                        self.linear_books[symbol] = L2Orderbook(symbol=symbol)

                    ob = self.linear_books[symbol]
                    if msg_type == "snapshot":
                        ob.apply_snapshot(data, self.virtual_clock_ms)
                    elif msg_type == "delta":
                        ok = ob.apply_delta(data, self.virtual_clock_ms)
                        if not ok:
                            self.gaps_detected += 1

                    b_price, a_price = ob.get_bbo()
                    yield MarketEvent(
                        event_type="ORDERBOOK_L2",
                        symbol=symbol,
                        exchange_ts_ms=exchange_ts,
                        virtual_clock_ms=self.virtual_clock_ms,
                        data={
                            "best_bid": b_price,
                            "best_ask": a_price,
                            "is_valid": ob.is_valid_for_execution(current_ts_ms=self.virtual_clock_ms),
                            "u": ob.last_u,
                            "seq": ob.last_seq,
                        },
                        topic=topic,
                    )

                # 2. Spot BBO (orderbook.1)
                elif topic.startswith("orderbook.1."):
                    parts = topic.split(".")
                    symbol = parts[2] if len(parts) >= 3 else ""
                    bids = data.get("b", [])
                    asks = data.get("a", [])
                    best_b = float(bids[0][0]) if bids else 0.0
                    best_a = float(asks[0][0]) if asks else 0.0
                    if best_b > 0 and best_a > 0:
                        bbo_dict = {
                            "bid": best_b,
                            "ask": best_a,
                            "mid": 0.5 * (best_b + best_a),
                            "ts": self.virtual_clock_ms,
                        }
                        self.spot_bbo[symbol] = bbo_dict
                        yield MarketEvent(
                            event_type="SPOT_BBO",
                            symbol=symbol,
                            exchange_ts_ms=exchange_ts,
                            virtual_clock_ms=self.virtual_clock_ms,
                            data=bbo_dict,
                            topic=topic,
                        )

                # 3. Tickers & Funding Rate
                elif topic.startswith("tickers."):
                    parts = topic.split(".")
                    symbol = parts[1] if len(parts) >= 2 else ""
                    if isinstance(data, dict):
                        self.latest_tickers[symbol] = data
                        yield MarketEvent(
                            event_type="TICKER_FUNDING",
                            symbol=symbol,
                            exchange_ts_ms=exchange_ts,
                            virtual_clock_ms=self.virtual_clock_ms,
                            data=data,
                            topic=topic,
                        )

                # 4. Public Trades
                elif topic.startswith("publicTrade."):
                    parts = topic.split(".")
                    symbol = parts[1] if len(parts) >= 2 else ""
                    if isinstance(data, list):
                        yield MarketEvent(
                            event_type="TRADE",
                            symbol=symbol,
                            exchange_ts_ms=exchange_ts,
                            virtual_clock_ms=self.virtual_clock_ms,
                            data={"trades": data},
                            topic=topic,
                        )

    def get_summary(self) -> Dict[str, Any]:
        """Returns statistics on the replayed stream."""
        duration_s = (self.end_clock_ms - self.start_clock_ms) / 1000.0 if (self.start_clock_ms and self.end_clock_ms) else 0.0
        return {
            "total_raw_events": self.total_raw_events,
            "start_clock_ms": self.start_clock_ms,
            "end_clock_ms": self.end_clock_ms,
            "duration_seconds": duration_s,
            "gaps_detected": self.gaps_detected,
            "tracked_symbols_l2": list(self.linear_books.keys()),
            "tracked_symbols_spot": list(self.spot_bbo.keys()),
        }


if __name__ == "__main__":
    test_path = REPO_ROOT / "reports" / "PUBLIC_CAPTURE_RAW_SANITIZED.jsonl"
    if not test_path.exists():
        test_path = REPO_ROOT / "data" / "collected_market_data" / "bybit_live_smoke.jsonl"

    replayer = MarketDataReplayer(test_path)
    count = 0
    event_breakdown = {}

    for ev in replayer.iter_events():
        count += 1
        event_breakdown[ev.event_type] = event_breakdown.get(ev.event_type, 0) + 1

    summary = replayer.get_summary()
    print("=== DETERMINISTIC MARKET DATA REPLAYER SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("Event breakdown:")
    for k, v in event_breakdown.items():
        print(f"  {k}: {v}")
