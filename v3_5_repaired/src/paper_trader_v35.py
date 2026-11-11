"""
Paper Trading Daemon v3.5-REPAIRED Alpha Pairs (Phase F1B Audit Hardened).
Operates on 1h candles across 18 liquid Bybit linear perpetuals with:
- Strict confirm=True event-driven Kline processing
- True statistical signed portfolio beta monitoring (Cov/Var relative to BTC)
- Bybit VIP-0 Non-MNT Fee Model (0.100% Taker / 0.036% Maker)
- Bybit V5 instrument validation (qtyStep, minOrderQty, minNotionalValue)
- Append-only Financial Ledger with double-entry cashflow attribution
- Isolated in v3_5_repaired package with zero impact on production VPS.
"""

from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(__file__).resolve().parent.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v3_5_repaired.src.config import (
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
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
)
from v3_5_repaired.src.alpha_pairs_engine import AlphaPairsEngineRepaired, ActivePairPosition
from v3_5_repaired.src.financial_ledger import FinancialLedger
from v3.src.bybit_client import BybitClient
from v3.src.market_stream import MarketStream
from v3.src.news_streamer import LiveNewsStreamer, NewsItem
from v3.src.persistence import atomic_json

logger = logging.getLogger("PaperTraderV35Repaired")


class PaperTraderV35Repaired:
    """
    Paper trader daemon running v3.5-REPAIRED Alpha Pairs.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = INITIAL_CAPITAL,
        state_file: Path = PERSISTENT_STATE_FILE,
        status_file: Path = LIVE_STATUS_FILE,
        md_file: Path = LIVE_MD_FILE,
        enable_news_defense: bool = True,
        client: Optional[BybitClient] = None,
        poll_interval_seconds: int = 5,
    ):
        self.symbols = symbols or list(SYMBOLS)
        self.initial_capital = Decimal(str(initial_capital))
        self.state_file = state_file
        self.live_status_file = status_file
        self.live_md_file = md_file
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
        )
        self.ledger = FinancialLedger()

        self.kline_history: Dict[str, List[Dict[str, float]]] = {s: [] for s in self.symbols}
        self.latest_prices: Dict[str, Decimal] = {}
        self.last_status_cache: Dict[str, Any] = {}
        self.running = False
        self.ws_connected = True
        self.last_ping_ms = 1.8
        self.stream: Optional[MarketStream] = None
        self.news_streamer: Optional[LiveNewsStreamer] = None
        self.macro_blackout_until: float = 0.0

        self._load_persistent_state()
        self._export_status()

    def on_kline_event(self, message: Dict[str, Any]) -> bool:
        """
        Event-driven Kline update handler.
        Invariant: Kline signals must ONLY trigger processing when confirm == True.
        Unconfirmed bars (confirm == False) are discarded.
        Returns:
            bool: True if closed bar was accepted and processed; False if ignored/unconfirmed.
        """
        topic = message.get("topic", "")
        data = message.get("data", [])
        if not topic.startswith("kline") or not data:
            return False

        candle_data = data[0] if isinstance(data, list) else data
        is_confirmed = candle_data.get("confirm", False)
        if not is_confirmed:
            logger.debug(f"Discarding unconfirmed kline update on {topic}")
            return False

        # Extract symbol from topic (e.g. kline.60.BTCUSDT)
        parts = topic.split(".")
        symbol = parts[-1] if len(parts) >= 3 else ""
        if symbol not in self.symbols:
            return False

        with self.lock:
            bar = {
                "start": int(candle_data.get("start", 0)),
                "open": float(candle_data.get("open", 0.0)),
                "high": float(candle_data.get("high", 0.0)),
                "low": float(candle_data.get("low", 0.0)),
                "close": float(candle_data.get("close", 0.0)),
                "volume": float(candle_data.get("volume", 0.0)),
            }
            if symbol not in self.kline_history:
                self.kline_history[symbol] = []
            self.kline_history[symbol].append(bar)
            if len(self.kline_history[symbol]) > 200:
                self.kline_history[symbol] = self.kline_history[symbol][-200:]

            self.latest_prices[symbol] = Decimal(str(bar["close"]))
            logger.info(f"Accepted confirmed 1h kline on {symbol} @ {bar['close']}")
            return True

    def is_macro_blackout(self) -> bool:
        return time.time() < self.macro_blackout_until

    def _load_persistent_state(self) -> None:
        with self.lock:
            if not self.state_file.exists():
                logger.info(f"No persistent state at {self.state_file}. Starting fresh.")
                return
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.engine.load_state(data)
                logger.info(f"Restored state: Equity=${self.engine.realized_equity:.2f}, Positions={len(self.engine.open_positions)}")
            except Exception as e:
                logger.error(f"Failed to load persistent state from {self.state_file}: {e}")

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

            # Statistical signed portfolio beta (Cov/Var relative to BTC)
            stat_beta = self.engine.calculate_portfolio_beta(self.kline_history)

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
                "portfolio_beta": round(float(stat_beta), 4),
                "closed_trades_count": len(self.engine.closed_trades),
                "trading_halted": self.engine.trading_halted,
                "timestamp": int(time.time() * 1000),
            }
            self.last_status_cache = status
            try:
                atomic_json(self.live_status_file, status)
            except Exception:
                pass
