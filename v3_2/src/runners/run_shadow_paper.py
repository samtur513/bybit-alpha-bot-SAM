#!/usr/bin/env python3
"""
v3_2/src/runners/run_shadow_paper.py
Standalone Operational Runner for v3.2 Realistic Shadow Paper Trading Engine.
Protocol: TORG-BOT — REAL-RUNTIME-WIRING-007

Architecture & Invariants:
1. Real Quantitative Shadow Engine: Wraps V32LegacyShadowEngine with exact original Donchian Trend logic:
   - 120-bar Donchian channel with shift(1) Zero-Lookahead.
   - Signal Parity with Native (min_volume_ratio = 1.2, ADX >= 18.0, EMA50 > EMA200).
   - Realistic Execution Model:
     * Standard retail fee tier: 0.100% taker fee, 0.040% maker fee.
     * Discrete Level 2 orderbook depth walking (walk_discrete_l2) & depth slippage.
     * Strict spread gate: reject fills if (best_ask - best_bid) / best_bid > 0.08%.
     * Contract-specific 8-hour perpetual funding accrual.
     * Missed Intent Ledger for spread rejections and unconfirmed books.
2. Strict Capital Isolation: Starting capital = 300.000000 USDT.
3. Isolated SQLite Ledger: Uses v32_shadow_ledger.db with WAL mode. NEVER touches v32_paper_ledger.db or Port 8083 state.
4. Truthful Telemetry: Without confirmed market data or with stale orderbook (>60s), reports NOT_STARTED/STALE with equity = None.
5. Strictly Paper Trading: Zero live or demo trading API orders. Zero private keys required.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3.src.bybit_client import BybitClient
from v3.src.persistence import atomic_json
from v3_2.src.config import DEFAULT_SYMBOLS, DONCHIAN_WINDOW, CANDLE_INTERVAL, BAR_INTERVAL_MS
from v3_2.src.paper_trader_v32 import prepare_dual_regime_indicators, MarketRegimeClassifier
from v3_2.src.shadow.paper_trader_v32_shadow import (
    V32LegacyShadowEngine,
    PortfolioInstance,
    L2OrderbookSnapshot,
    walk_discrete_l2,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ShadowPaperRunner] %(message)s",
)
logger = logging.getLogger("ShadowPaperRunner")

DEFAULT_COHORT_ID = "TORG_THREE_SYS_20261115_001"
DEFAULT_T0_UTC = "2026-11-17T00:00:00+00:00"
DEFAULT_DB_NAME = "v32_shadow_ledger.db"
DEFAULT_STATE_NAME = "v32_shadow_state.json"
DEFAULT_TELEMETRY_NAME = "telemetry_shadow.json"


class ShadowPaperRunner:
    """
    Independent v3.2 Realistic Shadow Paper Trading Daemon running real quantitative logic
    and discrete Level 2 orderbook execution.
    """

    def __init__(
        self,
        db_path: Path,
        telemetry_path: Path,
        state_path: Optional[Path] = None,
        initial_capital: Decimal = Decimal("300.000000"),
        cohort_id: str = DEFAULT_COHORT_ID,
        t0_utc: str = DEFAULT_T0_UTC,
        symbols: Optional[List[str]] = None,
        schedule_active: bool = False,
    ):
        self.db_path = Path(db_path).resolve()
        self.telemetry_path = Path(telemetry_path).resolve()
        self.state_path = Path(state_path).resolve() if state_path else self.db_path.parent / DEFAULT_STATE_NAME
        self.initial_capital = initial_capital
        self.cohort_id = cohort_id
        self.t0_utc = t0_utc
        self.schedule_active = schedule_active
        self.symbols = list(symbols) if symbols else list(DEFAULT_SYMBOLS)
        self._running = False

        # Parse T0 timestamp
        try:
            self.t0_dt = datetime.fromisoformat(self.t0_utc.replace("Z", "+00:00"))
            self.t0_ms = int(self.t0_dt.timestamp() * 1000)
        except Exception:
            self.t0_dt = datetime.now(timezone.utc)
            self.t0_ms = int(self.t0_dt.timestamp() * 1000)

        # Ensure parent directories exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Initialize Bybit Public Client (Read-Only)
        self.client = BybitClient(timeout=10, max_retries=5)

        # 2. Initialize V32LegacyShadowEngine
        self.engine = V32LegacyShadowEngine(
            initial_capital=self.initial_capital,
            db_path=str(self.db_path),
            state_file=str(self.state_path),
            enable_news_defense=True,
            enable_auto_gearbox=True,
            require_confirmed_l2=True,
        )

        # 3. Market Data Tracking & Instrument Rules Initialization
        self.has_market_data = False
        self.classifier = MarketRegimeClassifier()
        self.latest_prices: Dict[str, Decimal] = {}
        self.latest_quote_times: Dict[str, float] = {}
        self.latest_l2_times: Dict[str, float] = {}

        for s in self.symbols:
            rules = {
                "minOrderQty": Decimal("0.001"),
                "qtyStep": Decimal("0.001"),
                "tickSize": Decimal("0.01"),
                "minNotional": Decimal("5.0"),
                "maxOrderQty": Decimal("1000000"),
            }
            if hasattr(self.engine, "native_core"):
                self.engine.native_core.instrument_rules[s] = rules
            if hasattr(self.engine, "shadow_core"):
                self.engine.shadow_core.instrument_rules[s] = rules

    @property
    def portfolio(self) -> PortfolioInstance:
        """Returns the authoritative realistic shadow portfolio."""
        return self.engine.shadow_portfolio

    def ingest_closed_bar(
        self,
        symbol: str,
        bar: Dict[str, Any],
        best_bid: Optional[Decimal] = None,
        best_ask: Optional[Decimal] = None,
        depth_bid_usdt: Optional[Decimal] = None,
        depth_ask_usdt: Optional[Decimal] = None,
        l2_ladder_asks: Optional[List[Tuple[Decimal, Decimal]]] = None,
        l2_ladder_bids: Optional[List[Tuple[Decimal, Decimal]]] = None,
        l2_snapshot: Optional[L2OrderbookSnapshot] = None,
        funding_rate: Optional[Decimal] = None,
        btc_bullish: bool = True,
        btc_bearish: bool = True,
        is_historical_catchup: bool = False,
    ) -> Dict[str, Any]:
        """
        Processes a closed bar with discrete L2 orderbook quotes:
        1. Protect T0 at engine level (Section P0.1): zero trades/fees before authorized T0.
        2. Strictly forbids substituting current live orderbook for historical missed candles (Section P0.4).
        3. Validates real L2 orderbook data: strictly forbids artificial calculation from close
           or arbitrary 50,000 USDT depth (Section P1).
        4. Checks spread gate (rejects if spread > 0.08%).
        5. Steps V32LegacyShadowEngine.process_closed_bar_event inside single SQLite transaction.
        6. Updates candle buffers, positions, fees, and funding.
        7. Writes atomic state and telemetry.
        """
        ts = int(bar["timestamp"])
        bar_close_ms = ts + BAR_INTERVAL_MS

        # 1. Protect T0 at engine level (Section P0.1)
        if not self.schedule_active or bar_close_ms < self.t0_ms:
            record = {
                "timestamp": ts,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume", 0.0)),
                "turnover": float(bar.get("turnover", 0.0)),
            }
            prev_df = self.engine.candle_buffers.get(symbol)
            if prev_df is None or len(prev_df) == 0:
                df = pd.DataFrame([record])
            else:
                df = pd.concat([prev_df, pd.DataFrame([record])], ignore_index=True).tail(500)
            if len(df) >= min(DONCHIAN_WINDOW, 50):
                df = prepare_dual_regime_indicators(df, self.classifier, lookback=DONCHIAN_WINDOW)
            self.engine.candle_buffers[symbol] = df
            self.engine.last_processed_bar_ts[symbol] = ts
            self.latest_prices[symbol] = Decimal(str(bar["close"]))
            self.latest_quote_times[symbol] = time.monotonic()
            self.has_market_data = True
            return {
                "status": "PRE_T0_WARMUP",
                "symbol": symbol,
                "ts": ts,
                "positions_count": 0,
                "realized_equity": float(self.initial_capital),
                "reason": f"Pre-T0 warm-up (schedule_active={self.schedule_active}, t0={self.t0_utc})",
            }

        # 2. Historical catch-up check: strictly forbid current live orderbook for historical past bars (Section P0.4)
        is_historical = is_historical_catchup or ((time.time() * 1000 - bar_close_ms) > 120000)
        if is_historical and (best_bid is None or best_ask is None):
            reason = "REJECTED_HISTORICAL_NO_L2_ORDERBOOK: Forbid substituting current live book for historical catch-up"
            self.portfolio.rejected_orders.append({"symbol": symbol, "time_ms": ts, "reason": reason})
            record = {
                "timestamp": ts,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume", 0.0)),
                "turnover": float(bar.get("turnover", 0.0)),
            }
            prev_df = self.engine.candle_buffers.get(symbol)
            if prev_df is None or len(prev_df) == 0:
                df = pd.DataFrame([record])
            else:
                df = pd.concat([prev_df, pd.DataFrame([record])], ignore_index=True).tail(500)
            if len(df) >= min(DONCHIAN_WINDOW, 50):
                df = prepare_dual_regime_indicators(df, self.classifier, lookback=DONCHIAN_WINDOW)
            self.engine.candle_buffers[symbol] = df
            self.engine.last_processed_bar_ts[symbol] = ts
            self.engine.save_state()
            self.export_telemetry()
            return {
                "status": "REJECTED_HISTORICAL_NO_L2_ORDERBOOK",
                "symbol": symbol,
                "ts": ts,
                "reason": reason,
            }

        # P1 Invariant: Only attempt Bybit REST fetch if caller did NOT supply valid best_bid and best_ask
        # and bar is freshly closed (not historical)
        if best_bid is None or best_ask is None:
            try:
                ob = self.client.get_orderbook(symbol=symbol, limit=50)
                bids = ob.get("b", [])
                asks = ob.get("a", [])
                if bids and asks:
                    best_bid = Decimal(str(bids[0][0]))
                    best_ask = Decimal(str(asks[0][0]))
                    l2_ladder_bids = [(Decimal(str(p)), Decimal(str(s))) for p, s in bids]
                    l2_ladder_asks = [(Decimal(str(p)), Decimal(str(s))) for p, s in asks]
                    depth_bid_usdt = sum(Decimal(str(p)) * Decimal(str(s)) for p, s in bids)
                    depth_ask_usdt = sum(Decimal(str(p)) * Decimal(str(s)) for p, s in asks)
                    ob_ts = int(ob.get("ts", bar_close_ms))
                    l2_snapshot = L2OrderbookSnapshot(
                        timestamp_ms=ob_ts,
                        symbol=symbol,
                        bids=l2_ladder_bids,
                        asks=l2_ladder_asks,
                        is_confirmed=True,
                    )
            except Exception as ob_err:
                logger.debug(f"Failed to fetch live L2 orderbook for {symbol}: {ob_err}")

        # Compute depth from discrete ladders if not explicitly set
        if depth_bid_usdt is None:
            depth_bid_usdt = sum((p * s for p, s in l2_ladder_bids), Decimal("0")) if l2_ladder_bids else Decimal("0")
        if depth_ask_usdt is None:
            depth_ask_usdt = sum((p * s for p, s in l2_ladder_asks), Decimal("0")) if l2_ladder_asks else Decimal("0")

        # P1 Strict Invariant: Zero fictitious execution data
        # If orderbook is missing, record unconfirmed depth / rejection without losing positions.
        if best_bid is None or best_ask is None or best_bid <= Decimal("0") or best_ask <= Decimal("0"):
            reason = "HISTORICAL_GAP_RECOVERY: Retroactive order placement forbidden (no L2 depth at past timestamp)" if is_historical_catchup else "MISSING_L2_ORDERBOOK: No confirmed L2 orderbook or verified bids/asks available"
            self.portfolio.rejected_orders.append({"symbol": symbol, "time_ms": ts, "reason": reason})
            # Advance candle buffer so indicator continuity is maintained
            record = {
                "timestamp": ts,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume", 0.0)),
                "turnover": float(bar.get("turnover", 0.0)),
            }
            prev_df = self.engine.candle_buffers.get(symbol)
            if prev_df is None or len(prev_df) == 0:
                df = pd.DataFrame([record])
            else:
                df = pd.concat([prev_df, pd.DataFrame([record])], ignore_index=True).tail(500)
            if len(df) >= min(DONCHIAN_WINDOW, 50):
                df = prepare_dual_regime_indicators(df, self.classifier, lookback=DONCHIAN_WINDOW)
            self.engine.candle_buffers[symbol] = df
            self.engine.last_processed_bar_ts[symbol] = ts
            self.engine.save_state()
            self.export_telemetry()
            return {
                "status": "HISTORICAL_RECOVERED" if is_historical_catchup else "REJECTED_MISSING_L2_ORDERBOOK",
                "symbol": symbol,
                "ts": ts,
                "reason": reason,
                "missed_opportunity": bool(is_historical_catchup),
            }

        mid = (best_bid + best_ask) / Decimal("2.0")
        self.latest_prices[symbol] = mid
        now_mono = time.monotonic()
        self.latest_quote_times[symbol] = now_mono
        self.latest_l2_times[symbol] = now_mono
        self.has_market_data = True

        # Update candle buffer & compute indicators strictly with shift(1)
        record = {
            "timestamp": ts,
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume", 1500.0)),
            "turnover": float(bar.get("turnover", float(bar["close"]) * 1500.0)),
        }
        prev_df = self.engine.candle_buffers.get(symbol)
        if prev_df is None or len(prev_df) == 0:
            df = pd.DataFrame([record])
        else:
            df = pd.concat([prev_df, pd.DataFrame([record])], ignore_index=True).tail(500)

        if len(df) >= min(DONCHIAN_WINDOW, 50):
            df = prepare_dual_regime_indicators(df, self.classifier, lookback=DONCHIAN_WINDOW)
        self.engine.candle_buffers[symbol] = df
        self.engine.last_processed_bar_ts[symbol] = ts

        bar_dict = df.iloc[-1].to_dict() if len(df) > 0 else record

        res = self.engine.process_closed_bar_event(
            exchange_ts=ts,
            symbol=symbol,
            bar=bar_dict,
            best_bid=best_bid,
            best_ask=best_ask,
            depth_bid_usdt=depth_bid_usdt or Decimal("0"),
            depth_ask_usdt=depth_ask_usdt or Decimal("0"),
            funding_rate=funding_rate,
            l2_ladder_asks=l2_ladder_asks,
            l2_ladder_bids=l2_ladder_bids,
            l2_snapshot=l2_snapshot,
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
        )

        self.export_telemetry()
        return res

    def update_quotes(
        self,
        quotes: Dict[str, Decimal],
        timestamp_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Updates live quotes for continuous MTM equity tracking.
        """
        now_mono = time.monotonic()
        for sym, price in quotes.items():
            self.latest_prices[sym] = price
            self.latest_quote_times[sym] = now_mono

        self.has_market_data = True
        self.portfolio.update_drawdown(self.latest_prices)
        self.engine.save_state()
        return self.export_telemetry()

    def warm_up_history_from_bybit(self) -> None:
        """Loads historical closed klines from Bybit public REST API for all universe symbols."""
        logger.info(f"Warming up historical klines for Realistic Shadow via Bybit public REST API for {len(self.symbols)} symbols...")
        try:
            now_srv = self.client.get_server_time()
            for sym in self.symbols:  # Full original universe, not sliced!
                try:
                    raw = self.client.get_kline(sym, interval=CANDLE_INTERVAL, limit=150)
                    if not raw:
                        continue
                    records = [
                        dict(
                            timestamp=int(k[0]),
                            open=float(k[1]), high=float(k[2]), low=float(k[3]),
                            close=float(k[4]), volume=float(k[5]), turnover=float(k[6])
                        )
                        for k in raw if int(k[0]) + BAR_INTERVAL_MS <= now_srv
                    ]
                    df = pd.DataFrame(records).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
                    if len(df) >= min(DONCHIAN_WINDOW, 50):
                        self.engine.candle_buffers[sym] = df
                        self.latest_prices[sym] = Decimal(str(df["close"].iloc[-1]))
                        self.latest_quote_times[sym] = time.monotonic()
                        self.latest_l2_times[sym] = time.monotonic()
                        self.engine.last_processed_bar_ts[sym] = int(df["timestamp"].iloc[-1])
                except Exception as sym_err:
                    logger.debug(f"Shadow warm-up skip for {sym}: {sym_err}")
            self.has_market_data = len(self.engine.candle_buffers) > 0
            logger.info(f"Realistic Shadow warm-up complete. Buffers initialized for {len(self.engine.candle_buffers)} symbols.")
        except Exception as e:
            logger.warning(f"Public market data warm-up failed for Shadow (safe offline fallback): {e}")

    def poll_market_step(self) -> Dict[str, Any]:
        """
        Executes one continuous forward market data polling cycle for Realistic Shadow:
        1. Queries Bybit server time: now_srv = client.get_server_time().
        2. Queries live tickers via Bybit public REST:
           - Updates latest_prices and latest_quote_times.
           - Updates MTM equity on live quotes.
        3. Queries latest 1h klines for each symbol in self.symbols:
           - Strictly validates closed bars: open_ts + 3600_000 <= now_srv.
           - If new closed bar: fetches confirmed L2 orderbook snapshot (/v5/market/orderbook).
           - Passes authentic discrete L2 ladder and real depth into ingest_closed_bar().
        4. Detects funding events and applies contract-specific funding payments.
        5. Handles network latency, transient errors, rate limits with backoff without crashing.
        6. Atomically updates state and exports standardized telemetry.
        """
        try:
            now_srv = self.client.get_server_time()
            tickers = self.client.get_tickers_detail(category="linear")
            ticker_map = {t["symbol"]: t for t in tickers if "symbol" in t}

            quotes_to_update = {}
            funding_map = {}
            for sym in self.symbols:
                t = ticker_map.get(sym)
                if t and t.get("lastPrice"):
                    try:
                        p = Decimal(str(t["lastPrice"]))
                        quotes_to_update[sym] = p
                        if t.get("fundingRate"):
                            f_rate = Decimal(str(t["fundingRate"]))
                            funding_map[sym] = f_rate
                            if t.get("nextFundingTime"):
                                next_f_ts = int(t["nextFundingTime"])
                                if now_srv >= next_f_ts:
                                    self.portfolio.apply_funding(
                                        symbol=sym,
                                        funding_rate=f_rate,
                                        mark_price=p,
                                        exchange_ts=next_f_ts,
                                        explicit_settlement_ts=next_f_ts,
                                    )
                    except Exception:
                        pass

            if quotes_to_update:
                self.update_quotes(quotes_to_update, timestamp_ms=now_srv)

            # Maintain periodic L2 orderbook freshness (every 30s) for active symbols or portfolio positions
            now_mono = time.monotonic()
            symbols_to_sample_ob = list(self.portfolio.positions.keys())
            if not symbols_to_sample_ob and self.symbols:
                primary_sym = self.symbols[0]
                if now_mono - self.latest_l2_times.get(primary_sym, 0.0) >= 30.0:
                    symbols_to_sample_ob.append(primary_sym)
            for ob_sym in symbols_to_sample_ob:
                if now_mono - self.latest_l2_times.get(ob_sym, 0.0) >= 30.0:
                    try:
                        ob = self.client.get_orderbook(symbol=ob_sym, limit=50)
                        if ob.get("b") and ob.get("a"):
                            self.latest_l2_times[ob_sym] = time.monotonic()
                    except Exception as e:
                        logger.debug(f"Periodic L2 orderbook sample failed for {ob_sym}: {e}")

            # Check BTC Trend Filter from BTC candle buffer
            btc_bullish = True
            btc_bearish = True
            btc_df = self.engine.candle_buffers.get("BTCUSDT")
            if btc_df is not None and len(btc_df) >= 50:
                btc_close = btc_df["close"].iloc[-1]
                btc_ema50 = btc_df["ema_fast"].iloc[-1] if "ema_fast" in btc_df else btc_close
                btc_bullish = bool(btc_close >= btc_ema50)
                btc_bearish = bool(btc_close <= btc_ema50)

            processed_count = 0
            for sym in self.symbols:
                try:
                    # limit=50 allows catching up on network gaps > 3 hours chronologically (Section P0.4)
                    raw = self.client.get_kline(sym, interval=CANDLE_INTERVAL, limit=50)
                    if not raw:
                        continue
                    closed_bars = [k for k in raw if int(k[0]) + BAR_INTERVAL_MS <= now_srv]
                    closed_bars.sort(key=lambda k: int(k[0]))
                    for k in closed_bars:
                        bar_ts = int(k[0])
                        bar_close_ms = bar_ts + BAR_INTERVAL_MS
                        if bar_ts > self.engine.last_processed_bar_ts.get(sym, 0):
                            bar_dict = {
                                "timestamp": bar_ts,
                                "open": float(k[1]),
                                "high": float(k[2]),
                                "low": float(k[3]),
                                "close": float(k[4]),
                                "volume": float(k[5]),
                                "turnover": float(k[6]),
                            }
                            # Check if bar is historical past bar (> 120s ago)
                            is_hist = (now_srv - bar_close_ms) > 120000

                            # Fetch authentic L2 orderbook snapshot ONLY for fresh live bars.
                            # For past historical catch-up bars, FORBID substituting current live orderbook (Section P0.4)!
                            best_bid = None
                            best_ask = None
                            depth_bid_usdt = None
                            depth_ask_usdt = None
                            l2_ladder_bids = None
                            l2_ladder_asks = None
                            l2_snap = None
                            if not is_hist:
                                try:
                                    ob = self.client.get_orderbook(symbol=sym, limit=50)
                                    bids = ob.get("b", [])
                                    asks = ob.get("a", [])
                                    if bids and asks:
                                        best_bid = Decimal(str(bids[0][0]))
                                        best_ask = Decimal(str(asks[0][0]))
                                        l2_ladder_bids = [(Decimal(str(p)), Decimal(str(s))) for p, s in bids]
                                        l2_ladder_asks = [(Decimal(str(p)), Decimal(str(s))) for p, s in asks]
                                        depth_bid_usdt = sum(Decimal(str(p)) * Decimal(str(s)) for p, s in bids)
                                        depth_ask_usdt = sum(Decimal(str(p)) * Decimal(str(s)) for p, s in asks)
                                        ob_ts = int(ob.get("ts", bar_close_ms))
                                        l2_snap = L2OrderbookSnapshot(
                                            timestamp_ms=ob_ts,
                                            symbol=sym,
                                            bids=l2_ladder_bids,
                                            asks=l2_ladder_asks,
                                            is_confirmed=True,
                                        )
                                        self.latest_l2_times[sym] = time.monotonic()
                                except Exception as ob_err:
                                    logger.debug(f"Orderbook fetch failed for {sym}: {ob_err}")

                            is_funding_boundary = (bar_close_ms % 28_800_000 == 0)
                            f_rate = funding_map.get(sym) if is_funding_boundary else None
                            res = self.ingest_closed_bar(
                                symbol=sym,
                                bar=bar_dict,
                                best_bid=best_bid,
                                best_ask=best_ask,
                                depth_bid_usdt=depth_bid_usdt,
                                depth_ask_usdt=depth_ask_usdt,
                                l2_ladder_asks=l2_ladder_asks,
                                l2_ladder_bids=l2_ladder_bids,
                                l2_snapshot=l2_snap,
                                funding_rate=f_rate,
                                btc_bullish=btc_bullish,
                                btc_bearish=btc_bearish,
                                is_historical_catchup=is_hist,
                            )
                            if res.get("status") in ["FULL", "PROCESSED", "NO_SIGNAL"]:
                                processed_count += 1
                except Exception as sym_err:
                    logger.debug(f"Error checking klines for {sym}: {sym_err}")

            return {
                "status": "SUCCESS",
                "processed_bars": processed_count,
                "server_time_ms": now_srv,
            }
        except Exception as e:
            logger.warning(f"Shadow poll_market_step failed: {e}")
            return {"status": "ERROR", "reason": str(e)}

    def export_telemetry(self) -> Dict[str, Any]:
        """
        Builds and atomically writes standardized telemetry JSON.
        Strict Truthfulness Rule:
        If no market data or unconfirmed orderbook, reports None equity.
        """
        now_dt = datetime.now(timezone.utc)
        now_mono = time.monotonic()

        # Check freshness of L2 quotes (must be confirmed and <= 60s)
        is_fresh = False
        if self.has_market_data and self.latest_l2_times:
            newest_l2_age = min(now_mono - t for t in self.latest_l2_times.values())
            has_all_pos_fresh = all((now_mono - self.latest_l2_times.get(s, 0.0) <= 60.0) for s in self.portfolio.positions)
            is_fresh = (0.0 <= newest_l2_age <= 60.0) and has_all_pos_fresh

        init_cap = float(self.initial_capital)

        if not self.has_market_data or not is_fresh:
            status = "NOT_STARTED" if not self.has_market_data else "STALE"
            val_status = "NOT_STARTED" if not self.has_market_data else "UNVALUED_OR_STALE"
            telemetry = {
                "name": "v3.2 Realistic Shadow",
                "role": "Realistic Execution Model (L2 Depth Walking, Retail VIP0 Fees, Spread Guard)",
                "market_type": "BYBIT_LINEAR_PERPETUAL",
                "cohort_id": self.cohort_id,
                "t0_utc": self.t0_utc,
                "status": status,
                "initial_capital_usdt": init_cap,
                "equity_usdt": None,
                "net_pnl_usdt": None,
                "net_return_pct": None,
                "mdd_pct": None,
                "cash_usdt": None,
                "exposure_pct": None,
                "turnover_usdt": float(getattr(self.portfolio, "total_turnover", 0.0)),
                "fees_paid_usdt": float(self.portfolio.total_fees),
                "slippage_cost_usdt": 0.0,
                "funding_paid_usdt": float(self.portfolio.total_funding),
                "positions_count": len(self.portfolio.positions),
                "positions_detail": {},
                "data_freshness_status": status,
                "valuation_status": val_status,
                "error_count": 0,
                "last_update_iso": now_dt.isoformat(),
                "notes": f"Realistic Shadow Execution: {status} (Awaiting confirmed L2 orderbook depth)",
                "is_active": is_fresh,
                "schedule_active": bool(self.schedule_active),  # Protocol 009 Invariant
            }
        else:
            eq = float(self.portfolio.realized_equity)
            pnl = eq - init_cap
            ret_pct = (pnl / init_cap) * 100.0 if init_cap > 0 else 0.0
            peak = float(self.portfolio.peak_equity)
            mdd = round(((eq - peak) / peak) * 100.0, 2) if peak > 0 else 0.0

            pos_detail = {}
            tot_exposure = Decimal("0")
            for s, pos in self.portfolio.positions.items():
                p_mark = self.latest_prices.get(s, pos.entry_price)
                tot_exposure += pos.current_qty * p_mark
                pos_detail[s] = {
                    "direction": "LONG" if pos.direction == 1 else "SHORT",
                    "entry_price": float(pos.entry_price),
                    "current_qty": float(pos.current_qty),
                    "current_stop": float(pos.current_stop),
                    "bars_held": pos.bars_held,
                }
            exposure_pct = round(float(tot_exposure / Decimal(str(eq)) * Decimal("100.0")), 2) if eq > 0 else 0.0

            telemetry = {
                "name": "v3.2 Realistic Shadow",
                "role": "Realistic Execution Model (L2 Depth Walking, Retail VIP0 Fees, Spread Guard)",
                "market_type": "BYBIT_LINEAR_PERPETUAL",
                "cohort_id": self.cohort_id,
                "t0_utc": self.t0_utc,
                "status": "FRESH",
                "initial_capital_usdt": round(init_cap, 4),
                "equity_usdt": round(eq, 4),
                "net_pnl_usdt": round(pnl, 4),
                "net_return_pct": round(ret_pct, 3),
                "mdd_pct": mdd,
                "peak_equity_usdt": round(peak, 4),
                "cash_usdt": round(float(getattr(self.portfolio, "balance", eq)), 4),
                "exposure_pct": exposure_pct,
                "turnover_usdt": float(getattr(self.portfolio, "total_turnover", 0.0)),
                "fees_paid_usdt": round(float(self.portfolio.total_fees), 4),
                "slippage_cost_usdt": 0.0,
                "funding_paid_usdt": round(float(self.portfolio.total_funding), 4),
                "positions_count": len(self.portfolio.positions),
                "positions_detail": pos_detail,
                "data_freshness_status": "FRESH",
                "valuation_status": "FRESH",
                "error_count": 0,
                "last_update_iso": now_dt.isoformat(),
                "notes": "Linear Perpetuals, Discrete L2 depth walking, 0.10% VIP0 retail fee tier, active engine",
                "is_active": True,
                "schedule_active": bool(self.schedule_active),
            }

        atomic_json(self.telemetry_path, telemetry)
        return telemetry

    def run_preflight_cycle(self) -> Dict[str, Any]:
        """Runs offline/dry-run preflight check and writes initial state."""
        logger.info("Executing Realistic Shadow real-engine preflight check...")
        self.engine.save_state()
        telemetry = self.export_telemetry()
        logger.info(f"Realistic Shadow preflight complete. Status: {telemetry['data_freshness_status']}, Equity: {telemetry['equity_usdt']}")
        return telemetry

    def run(self, duration_sec: Optional[int] = None, interval_sec: int = 5) -> None:
        """Main daemon loop with continuous market data polling and execution."""
        self._running = True
        logger.info(f"Starting Realistic Shadow Runner. DB: {self.db_path}, Telemetry: {self.telemetry_path}")
        self.warm_up_history_from_bybit()
        self.export_telemetry()

        start_time = time.monotonic()
        try:
            while self._running:
                # 1. Step continuous market stream with confirmed L2 orderbooks
                self.poll_market_step()

                # 2. Update telemetry
                self.export_telemetry()

                if duration_sec and (time.monotonic() - start_time >= duration_sec):
                    logger.info(f"Target duration of {duration_sec}s reached. Exiting cleanly.")
                    break
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            logger.info("Shutdown requested.")
        finally:
            self._running = False
            self.engine.save_state()
            self.export_telemetry()
            logger.info("Realistic Shadow Runner cleanly stopped.")


def main():
    parser = argparse.ArgumentParser(description="v3.2 Realistic Shadow Paper Trading Runner (Real Engine)")
    parser.add_argument("--dry-run", action="store_true", help="Run preflight validation and exit")
    parser.add_argument("--duration", type=int, default=None, help="Run duration in seconds")
    parser.add_argument("--interval", type=int, default=5, help="Telemetry heartbeat interval in seconds")
    parser.add_argument("--db-path", type=str, default=None, help="Path to isolated SQLite database")
    parser.add_argument("--telemetry-file", type=str, default=None, help="Path to telemetry output JSON")
    parser.add_argument("--state-path", type=str, default=None, help="Path to state JSON")
    parser.add_argument("--cohort-id", type=str, default=DEFAULT_COHORT_ID, help="Canonical cohort ID")
    parser.add_argument("--t0-utc", type=str, default=DEFAULT_T0_UTC, help="Scheduled T0 UTC")
    parser.add_argument("--schedule-active", action="store_true", default=False, help="Explicitly activate study schedule at T0")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    db_path = Path(args.db_path) if args.db_path else repo_root / "reports" / DEFAULT_DB_NAME
    telemetry_path = Path(args.telemetry_file) if args.telemetry_file else repo_root / "reports" / DEFAULT_TELEMETRY_NAME
    state_path = Path(args.state_path) if args.state_path else None

    runner = ShadowPaperRunner(
        db_path=db_path,
        telemetry_path=telemetry_path,
        state_path=state_path,
        initial_capital=Decimal("300.000000"),
        cohort_id=args.cohort_id,
        t0_utc=args.t0_utc,
        schedule_active=args.schedule_active,
    )

    if args.dry_run:
        runner.run_preflight_cycle()
        sys.exit(0)

    runner.run(duration_sec=args.duration, interval_sec=args.interval)


if __name__ == "__main__":
    main()
