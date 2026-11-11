"""
Paper Trading Daemon v3.5-REPAIRED Alpha Pairs (Phase F1B / F1C Audit Hardened).
Operates on 1h candles across 18 liquid Bybit linear perpetuals with:
- Strict interval ("60"), timestamp monotonicity, and deduplicated event-driven Kline processing
- 18-instrument synchronization barrier with REST backfill fallback
- Real WebSocket connection lifecycle monitoring (no hardcoded ws_connected=True)
- Time-synchronized factor portfolio beta reporting with UNKNOWN status
- Deep FinancialLedger integration (SQLite WAL)
- Isolated on dedicated port 8188 (zero collision with VPS 8088)
"""

from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(__file__).resolve().parent.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v3_5_repaired.src.config import (
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
    LEDGER_DB_FILE,
    PORT,
    VERSION_NAME,
    INITIAL_CAPITAL,
    SYMBOLS,
    BENCHMARK_SYMBOL,
    CANDLE_INTERVAL,
    BAR_INTERVAL_MS,
    MOMENTUM_WINDOW_HOURS,
    FEE_TAKER,
    FEE_MAKER,
    BASE_SLIPPAGE,
    BAR_SYNC_TIMEOUT_SECONDS,
)
from v3_5_repaired.src.alpha_pairs_engine import (
    AlphaPairsEngineRepaired,
    ActivePairPosition,
    BetaStatus,
    BetaResult,
    OrderbookQuote,
    QuoteSource,
)
from v3_5_repaired.src.financial_ledger import FinancialLedger
from v3.src.bybit_client import BybitClient
from v3.src.market_stream import MarketStream
from v3.src.news_streamer import LiveNewsStreamer, NewsItem
from v3.src.persistence import atomic_json

logger = logging.getLogger("PaperTraderV35Repaired")


class PaperTraderV35Repaired:
    """
    Paper trader daemon running v3.5-REPAIRED Alpha Pairs on dedicated port 8188.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = INITIAL_CAPITAL,
        state_file: Path = PERSISTENT_STATE_FILE,
        status_file: Path = LIVE_STATUS_FILE,
        md_file: Path = LIVE_MD_FILE,
        ledger_db_file: Path = LEDGER_DB_FILE,
        enable_news_defense: bool = True,
        client: Optional[BybitClient] = None,
        poll_interval_seconds: int = 5,
    ):
        self.symbols = symbols or list(SYMBOLS)
        self.initial_capital = Decimal(str(initial_capital))
        self.state_file = state_file
        self.live_status_file = status_file
        self.live_md_file = md_file
        self.ledger_db_file = ledger_db_file
        self.enable_news_defense = enable_news_defense
        self.poll_interval = poll_interval_seconds
        self.client = client or BybitClient(timeout=10, max_retries=3)

        self.lock = threading.RLock()
        self.engine = AlphaPairsEngineRepaired(
            initial_capital=self.initial_capital,
            benchmark=BENCHMARK_SYMBOL,
            fee_taker=FEE_TAKER,
            fee_maker=FEE_MAKER,
            slippage=BASE_SLIPPAGE,
            ledger_db_path=str(self.ledger_db_file),
        )
        self.ledger = self.engine.ledger

        self.kline_history: Dict[str, List[Dict[str, float]]] = {s: [] for s in self.symbols}
        self.latest_prices: Dict[str, Decimal] = {}
        self.last_status_cache: Dict[str, Any] = {}
        self.running = False

        # Real WebSocket connection tracking (Task 9: no fake ws_connected=True)
        self.ws_connected = False
        self.last_ping_ms: Optional[float] = None
        self.last_heartbeat_ts: Optional[float] = None

        # Kline deduplication and synchronization barrier (Task 3)
        self.processed_kline_keys: Set[str] = set()
        self.pending_bars_by_ts: Dict[int, Dict[str, Dict[str, float]]] = {}
        self.pending_bars_first_seen: Dict[int, float] = {}
        self.last_processed_bar_ts: int = 0
        self.live_quotes: Dict[str, OrderbookQuote] = {}

        self.stream: Optional[MarketStream] = None
        self.news_streamer: Optional[LiveNewsStreamer] = None
        self.macro_blackout_until: float = 0.0

        self._load_persistent_state()
        self._export_status()

    def update_ws_heartbeat(self, ping_ms: float) -> None:
        """Updates real WebSocket heartbeat."""
        with self.lock:
            self.last_heartbeat_ts = time.time()
            self.last_ping_ms = round(float(ping_ms), 2)
            self.ws_connected = True

    def on_orderbook_l1_event(self, message: Dict[str, Any]) -> bool:
        """
        Handles Bybit V5 orderbook.1.{symbol} L1 updates.
        Topic format: "orderbook.1.BTCUSDT"
        Extracts bid1, ask1, sizes, quote timestamp, and populates self.live_quotes.
        """
        topic = message.get("topic", "")
        if not topic.startswith("orderbook.1"):
            return False
        data = message.get("data", {})
        symbol = data.get("s") or topic.split(".")[-1]
        if symbol not in self.symbols:
            return False
        bids = data.get("b", [])
        asks = data.get("a", [])
        if not bids or not asks:
            return False
        try:
            bid_p = Decimal(str(bids[0][0]))
            bid_sz = Decimal(str(bids[0][1])) if len(bids[0]) > 1 else None
            ask_p = Decimal(str(asks[0][0]))
            ask_sz = Decimal(str(asks[0][1])) if len(asks[0]) > 1 else None
            ts = int(message.get("ts", time.time() * 1000))
            if ask_p > bid_p > Decimal("0.0"):
                quote = OrderbookQuote(
                    symbol=symbol,
                    bid=bid_p,
                    ask=ask_p,
                    quote_ts=ts,
                    source=QuoteSource.EXCHANGE_L1,
                    bid_size=bid_sz,
                    ask_size=ask_sz,
                )
                with self.lock:
                    self.live_quotes[symbol] = quote
                return True
        except Exception as e:
            logger.error(f"Error parsing orderbook.1 event for {symbol}: {e}")
        return False

    def on_ticker_event(self, message: Dict[str, Any]) -> bool:
        """
        Handles Bybit V5 tickers.{symbol} WebSocket updates.
        Topic format: "tickers.BTCUSDT"
        Extracts bid1Price, ask1Price, sizes, quote timestamp, and populates self.live_quotes.
        """
        topic = message.get("topic", "")
        if not topic.startswith("tickers"):
            return False
        data = message.get("data", {})
        symbol = data.get("symbol") or topic.split(".")[-1]
        if symbol not in self.symbols:
            return False
        bid_str = data.get("bid1Price")
        ask_str = data.get("ask1Price")
        if not bid_str or not ask_str:
            return False
        try:
            bid_p = Decimal(str(bid_str))
            ask_p = Decimal(str(ask_str))
            bid_sz = Decimal(str(data["bid1Size"])) if "bid1Size" in data else None
            ask_sz = Decimal(str(data["ask1Size"])) if "ask1Size" in data else None
            ts = int(message.get("ts", time.time() * 1000))
            if ask_p > bid_p > Decimal("0.0"):
                quote = OrderbookQuote(
                    symbol=symbol,
                    bid=bid_p,
                    ask=ask_p,
                    quote_ts=ts,
                    source=QuoteSource.EXCHANGE_TICKER,
                    bid_size=bid_sz,
                    ask_size=ask_sz,
                )
                with self.lock:
                    self.live_quotes[symbol] = quote
                return True
        except Exception as e:
            logger.error(f"Error parsing ticker event for {symbol}: {e}")
        return False

    def update_live_quote(self, quote: OrderbookQuote) -> None:
        """Explicitly ingests a validated OrderbookQuote."""
        if not isinstance(quote, OrderbookQuote):
            raise TypeError("quote must be an instance of OrderbookQuote")
        with self.lock:
            self.live_quotes[quote.symbol] = quote

    def sync_exchange_instrument_rules(self) -> int:
        """
        Fetches authentic Bybit V5 /v5/market/instruments-info and updates engine rules.
        """
        try:
            resp = self.client.get_instruments_info(category="linear")
            if resp:
                payload = {
                    "retCode": 0,
                    "retMsg": "OK",
                    "result": {"category": "linear", "list": resp},
                    "time": int(time.time() * 1000),
                }
                count = self.engine.verify_from_bybit_instruments_info(payload)
                logger.info(f"Verified {count} instrument rules from live Bybit REST API.")
                return count
        except Exception as e:
            logger.error(f"Failed to fetch live Bybit instrument info: {e}")
        return 0

    def is_ws_alive(self) -> bool:
        """Verifies if WebSocket connection is live based on heartbeat (30s timeout)."""
        with self.lock:
            if self.last_heartbeat_ts is None:
                return False
            is_alive = (time.time() - self.last_heartbeat_ts) < 30.0
            self.ws_connected = is_alive
            return is_alive

    def on_kline_event(self, message: Dict[str, Any]) -> bool:
        """
        Event-driven Kline update handler with:
        - Interval validation (must match CANDLE_INTERVAL = "60")
        - confirm=True enforcement
        - Timestamp validation and deduplication
        - 18-instrument synchronization barrier
        - Automatic REST backfill fallback
        Returns True if bar was accepted.
        """
        topic = message.get("topic", "")
        data = message.get("data", [])
        if not topic.startswith("kline") or not data:
            return False

        candle_data = data[0] if isinstance(data, list) else data

        # 1. Interval Check
        interval = str(candle_data.get("interval", ""))
        parts = topic.split(".")
        topic_interval = parts[1] if len(parts) >= 3 else interval
        if topic_interval != CANDLE_INTERVAL and interval != CANDLE_INTERVAL:
            logger.debug(f"Discarding kline with non-matching interval: {topic_interval}")
            return False

        # 2. confirm=True Check
        is_confirmed = candle_data.get("confirm", False)
        if not is_confirmed:
            logger.debug(f"Discarding unconfirmed kline update on {topic}")
            return False

        # 3. Extract symbol
        symbol = parts[-1] if len(parts) >= 3 else candle_data.get("symbol", "")
        if symbol not in self.symbols:
            return False

        start_ts = int(candle_data.get("start", 0))
        if start_ts <= 0:
            return False

        now_ms = int(time.time() * 1000)
        # Anti-lookahead: candle cannot start in the future
        if start_ts > now_ms:
            logger.warning(f"Lookahead violation: kline start_ts {start_ts} is in the future (> {now_ms}). Discarded.")
            return False

        # Monotonicity: candle cannot be older than already processed bar
        if self.last_processed_bar_ts > 0 and start_ts < self.last_processed_bar_ts:
            logger.debug(f"Discarding obsolete kline {start_ts} < last_processed {self.last_processed_bar_ts}")
            return False

        # 4. Deduplication
        bar_key = f"{symbol}_{CANDLE_INTERVAL}_{start_ts}"
        with self.lock:
            if bar_key in self.processed_kline_keys:
                logger.debug(f"Duplicate kline {bar_key} ignored.")
                return False
            self.processed_kline_keys.add(bar_key)

            bar = {
                "start": start_ts,
                "open": float(candle_data.get("open", 0.0)),
                "high": float(candle_data.get("high", 0.0)),
                "low": float(candle_data.get("low", 0.0)),
                "close": float(candle_data.get("close", 0.0)),
                "volume": float(candle_data.get("volume", 0.0)),
            }
            self.latest_prices[symbol] = Decimal(str(bar["close"]))

            # 4b. Kline Continuity Verification in Active Event Loop
            is_cont = self.verify_kline_continuity(symbol)
            if not is_cont:
                logger.warning(f"Kline continuity gap on {symbol}")

            # 4c. Active Sync Timeout Sweeper
            timed_out = self.check_sync_timeouts(now_sec=time.time())
            if timed_out:
                logger.warning(f"Swept {len(timed_out)} timed-out bars from sync barrier: {timed_out}")

            # 5. 18-Instrument Synchronization Barrier
            if start_ts not in self.pending_bars_by_ts:
                self.pending_bars_by_ts[start_ts] = {}
                self.pending_bars_first_seen[start_ts] = time.time()
            self.pending_bars_by_ts[start_ts][symbol] = bar

            arrived_symbols = set(self.pending_bars_by_ts[start_ts].keys())
            missing_symbols = set(self.symbols) - arrived_symbols

            # Check if all 18 symbols arrived
            if len(missing_symbols) == 0:
                self.pending_bars_first_seen.pop(start_ts, None)
                self._commit_synchronized_bar(start_ts)
                return True

            first_seen = self.pending_bars_first_seen.get(start_ts, time.time())
            elapsed = time.time() - first_seen

            # Attempt REST backfill if quorum reached (>= 16 symbols) or if timeout is approaching
            if len(arrived_symbols) >= (len(self.symbols) - 2) or elapsed >= BAR_SYNC_TIMEOUT_SECONDS:
                self._attempt_rest_backfill(start_ts, list(missing_symbols))
                if set(self.pending_bars_by_ts[start_ts].keys()) == set(self.symbols):
                    self.pending_bars_first_seen.pop(start_ts, None)
                    self._commit_synchronized_bar(start_ts)
                    return True

            # If timeout expired and still missing symbols, fail safe: drop bar, do not trade on partial universe
            if elapsed >= BAR_SYNC_TIMEOUT_SECONDS:
                logger.warning(
                    f"Kline sync timed out ({elapsed:.1f}s) for bar {start_ts}: only {len(arrived_symbols)}/18 arrived. "
                    f"Dropping pending bar buffer to prevent lookahead or biased partial ranking."
                )
                self.pending_bars_by_ts.pop(start_ts, None)
                self.pending_bars_first_seen.pop(start_ts, None)
                self.last_processed_bar_ts = start_ts
                return False

            return True

    def _attempt_rest_backfill(self, target_ts: int, missing_symbols: List[str]) -> None:
        """Fetches missing candle via REST if WebSocket missed it."""
        for sym in missing_symbols:
            try:
                res = self.client.get_kline(symbol=sym, interval=CANDLE_INTERVAL, limit=5, category="linear")
                # Handle both BybitClient return type (List[List[str]]) and raw HTTP dict response
                if isinstance(res, list):
                    rows = res
                elif isinstance(res, dict):
                    rows = res.get("result", {}).get("list", [])
                else:
                    rows = []

                for r in rows:
                    r_start = int(r[0])
                    if r_start == target_ts:
                        self.pending_bars_by_ts[target_ts][sym] = {
                            "start": r_start,
                            "open": float(r[1]),
                            "high": float(r[2]),
                            "low": float(r[3]),
                            "close": float(r[4]),
                            "volume": float(r[5]),
                        }
                        self.latest_prices[sym] = Decimal(str(r[4]))
                        logger.info(f"REST backfilled missing candle for {sym} at {target_ts}")
                        break
            except Exception as e:
                logger.warning(f"Failed REST backfill for {sym}: {e}")

    def _commit_synchronized_bar(self, bar_ts: int) -> None:
        """Commits all synchronized bars to kline history and triggers process_bar."""
        if bar_ts <= self.last_processed_bar_ts:
            return

        bars_dict = self.pending_bars_by_ts.pop(bar_ts, {})
        for sym, b in bars_dict.items():
            if sym not in self.kline_history:
                self.kline_history[sym] = []
            self.kline_history[sym].append(b)
            if len(self.kline_history[sym]) > 200:
                self.kline_history[sym] = self.kline_history[sym][-200:]

        self.last_processed_bar_ts = bar_ts
        logger.info(f"Synchronized 18-symbol bar committed at {bar_ts}. Running process_bar...")

        is_blackout = self.is_macro_blackout()
        decision_clock = bar_ts + (60 * 60 * 1000)
        # Collect verified L1 orderbook quotes (synthetic orderbook calculations strictly banned)
        valid_quotes = {s: q for s, q in self.live_quotes.items() if q.is_valid_spread}
        orderbook_bids = {s: q.bid for s, q in valid_quotes.items()}
        orderbook_asks = {s: q.ask for s, q in valid_quotes.items()}
        self.engine.process_bar(
            prices_series=self.kline_history,
            current_prices=self.latest_prices,
            bar_ts=bar_ts,
            macro_lockout=is_blackout,
            orderbook_bids=orderbook_bids,
            orderbook_asks=orderbook_asks,
            orderbook_quotes=valid_quotes,
            current_clock=decision_clock,
        )
        self.save_persistent_state()
        self._export_status()

    def check_sync_timeouts(self, now_sec: Optional[float] = None) -> List[int]:
        """
        Autonomous synchronization timeout handler:
        Drops partial buffers that have exceeded BAR_SYNC_TIMEOUT_SECONDS without reaching universe count.
        """
        current_time = now_sec if now_sec is not None else time.time()
        dropped_bars = []

        with self.lock:
            for start_ts, first_seen in list(self.pending_bars_first_seen.items()):
                elapsed = current_time - first_seen
                if elapsed > BAR_SYNC_TIMEOUT_SECONDS:
                    symbols_arrived = list(self.pending_bars_by_ts.get(start_ts, {}).keys())
                    logger.warning(
                        f"Synchronization timeout for bar {start_ts} after {elapsed:.1f}s! "
                        f"Dropping incomplete buffer ({len(symbols_arrived)}/18 symbols: {symbols_arrived})."
                    )
                    self.pending_bars_by_ts.pop(start_ts, None)
                    self.pending_bars_first_seen.pop(start_ts, None)
                    dropped_bars.append(start_ts)

        return dropped_bars

    def verify_kline_continuity(self, symbol: str) -> bool:
        """
        Verifies hourly continuity of historical candles for a symbol.
        Returns True if all consecutive candles differ by exactly BAR_INTERVAL_MS (3600000ms).
        """
        history = self.kline_history.get(symbol, [])
        if len(history) < 2:
            return True

        for i in range(1, len(history)):
            prev_ts = int(history[i - 1]["start"])
            curr_ts = int(history[i]["start"])
            if curr_ts - prev_ts != BAR_INTERVAL_MS:
                logger.error(
                    f"Kline continuity breach for {symbol}: gap between {prev_ts} and {curr_ts} "
                    f"(expected delta {BAR_INTERVAL_MS}ms, got {curr_ts - prev_ts}ms)"
                )
                return False
        return True

    def is_macro_blackout(self) -> bool:
        return time.time() < self.macro_blackout_until

    def _load_persistent_state(self) -> None:
        with self.lock:
            data = None
            if self.state_file.exists():
                try:
                    with open(self.state_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as e:
                    logger.error(f"Failed to load persistent state from {self.state_file}: {e}")

            status, errors = self.engine.reconcile_and_recover(data)
            if status == "NO_NEW_TRADES":
                logger.critical(f"Reconciliation error on restart: {errors}. Trading halted in NO_NEW_TRADES mode.")
            else:
                logger.info(f"Restored and reconciled state: Equity=${self.engine.realized_equity:.2f}, Positions={len(self.engine.open_positions)}")

    def save_persistent_state(self) -> None:
        with self.lock:
            try:
                state_dict = self.engine.export_state()
                atomic_json(self.state_file, state_dict)
            except Exception as e:
                logger.error(f"Failed to save persistent state: {e}")

    def _export_status(self) -> None:
        with self.lock:
            eq = self.engine.get_portfolio_equity(self.latest_prices)
            dd = self.engine.max_drawdown_pct
            active_longs = sum(1 for p in self.engine.open_positions.values() if p.direction == 1)
            active_shorts = sum(1 for p in self.engine.open_positions.values() if p.direction == -1)

            # Accurate time-synchronized portfolio beta
            beta_res = self.engine.calculate_portfolio_beta(self.kline_history, self.latest_prices)

            status = {
                "system": VERSION_NAME,
                "port": PORT,
                "equity_usdt": round(float(eq), 2),
                "realized_equity": round(float(self.engine.realized_equity), 2),
                "peak_equity": round(float(self.engine.peak_equity), 2),
                "max_drawdown_pct": round(float(dd), 2),
                "open_positions_count": len(self.engine.open_positions),
                "active_longs": active_longs,
                "active_shorts": active_shorts,
                "portfolio_beta": float(beta_res.beta) if beta_res.beta is not None else "UNKNOWN",
                "beta_status": beta_res.status.value,
                "closed_trades_count": len(self.engine.closed_trades),
                "trading_halted": self.engine.trading_halted,
                "ws_connected": self.is_ws_alive(),
                "last_ping_ms": self.last_ping_ms,
                "timestamp": int(time.time() * 1000),
            }
            self.last_status_cache = status
            try:
                atomic_json(self.live_status_file, status)
            except Exception:
                pass
