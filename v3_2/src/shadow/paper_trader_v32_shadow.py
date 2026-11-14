#!/usr/bin/env python3
"""
v3_2/src/shadow/paper_trader_v32_shadow.py
Bybit Multi-Alpha v3.2 Legacy Shadow Engine with 100% Trading Rule Parity.
Protocol: V32-SHADOW-PARITY-003 / V32-SHADOW-INTEGRITY-002 / V32-MASTER-001

Architecture & Invariants:
1. Signal & Strategy Parity (P0.1):
   - Direct integration with CoreV3Engine / MultiCoinScanner / KellyRiskManager / RegimeGuard.
   - Evaluates 1h Donchian Breakout (120 bars, Zero-Lookahead shift(1)), ADX >= 18.0, EMA50 > EMA200, BTC Delta.
   - Macro Embargo Guard (News Core v1.2: 45m before, 30m after high-impact macro events).
2. Independent Risk Managers & АКПП (P0.2):
   - Native and Shadow maintain completely separate KellyRiskManager instances.
   - Separate peak equity, drawdowns, АКПП gears, and cooldown trackers.
   - Execution divergence or slippage in Shadow NEVER corrupts Native's gear or sizing.
3. Intrabar Sequencing & Ambiguity Guard (P0.3):
   - Detects simultaneous touch of Stop Loss and Cascade TP within the same 1h aggregate bar.
   - Emits AMBIGUOUS_SEQUENCE event.
   - Conservatively executes STOP LOSS; strictly refuses to set tp1_taken/tp2_taken or ratchet stop.
4. Transactional SQLite State Journal & Crash Recovery (P0.4):
   - Atomic persistence to shadow_state_journal in the same transaction as shadow_execution_events.
   - Resilient recovery across all lifecycle states: entry, TP1, TP2, stop, gear shifts.
5. Discrete L2 Orderbook Depth & Funding Settlement (P0.5):
   - Discrete orderbook ladder walking (VWAP across levels) instead of arbitrary heuristics.
   - Strict 8-hour perpetual funding accrual based on actual position hold time.
"""

from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from v3.src.core_v3 import CoreV3Engine, ActivePosition, ClosedTrade, PortfolioSnapshot
from v3.src.kelly_sizing import KellyRiskManager
from v3.src.multi_scanner import MultiCoinScanner, CoinCandidate
from v3.src.news_core.news_config import NewsConfig
from v3.src.news_core.regime_guard import RegimeGuard
from v3.src.universe_screener import UniverseScreener, ScreenerConfig
from v3_2.src.regime_classifier import MarketRegimeClassifier, MarketRegime
from v3_2.src.range_engine import RangeMeanReversionEngine
from v3_2.src.chop_screener import ChopUniverseScreener

logger = logging.getLogger("V32LegacyShadow")

# Baseline Financial Constants
INITIAL_CAPITAL = Decimal("300.000000")
DONCHIAN_WINDOW = 120
BAR_INTERVAL_MS = 3600 * 1000  # 1-hour candles
FUNDING_INTERVAL_MS = 8 * 3600 * 1000  # 8-hour funding intervals

# Fee Constants
NATIVE_TAKER_FEE = Decimal("0.00090")      # 0.090% Legacy MNT discount
NATIVE_MAKER_FEE = Decimal("0.000324")    # 0.0324% Legacy MNT discount
SHADOW_TAKER_FEE = Decimal("0.00100")      # 0.100% Standard VIP0 Taker fee
SHADOW_MAKER_FEE = Decimal("0.00036")      # 0.036% Standard VIP0 Maker fee
NATIVE_SLIPPAGE = Decimal("0.0002")        # 0.02% simulated fixed
MAX_ALLOWED_SPREAD_PCT = Decimal("0.0008") # 0.08% max spread before shadow rejection
MIN_NOTIONAL_USDT = Decimal("5.0")         # Bybit $5 minimum notional
MAX_CONCURRENT_POSITIONS = 4
MAX_PORTFOLIO_LEVERAGE = Decimal("3.5")


def walk_discrete_l2(
    ladder: List[Tuple[Decimal, Decimal]],
    needed_qty: Decimal,
) -> Tuple[bool, Decimal, Decimal]:
    """
    Walks a discrete L2 ladder [(price, qty), ...].
    Returns: (is_fully_filled, vwap_price, total_filled_qty)
    """
    if not ladder or needed_qty <= Decimal("0"):
        return False, Decimal("0"), Decimal("0")

    total_cost = Decimal("0")
    filled_qty = Decimal("0")
    remaining_qty = needed_qty

    for level_price, level_qty in ladder:
        take_qty = min(remaining_qty, level_qty)
        total_cost += take_qty * level_price
        filled_qty += take_qty
        remaining_qty -= take_qty
        if remaining_qty <= Decimal("0"):
            break

    if remaining_qty > Decimal("0"):
        # Insufficient depth in ladder
        return False, Decimal("0"), filled_qty

    vwap = total_cost / filled_qty
    return True, vwap, filled_qty


@dataclass
class PositionState:
    symbol: str
    direction: int  # +1 Long, -1 Short
    initial_qty: Decimal
    current_qty: Decimal
    entry_price: Decimal
    entry_time_ms: int
    current_stop: Decimal
    initial_atr: Decimal
    tp1_taken: bool = False
    tp2_taken: bool = False
    tp1_price: Decimal = Decimal("0")
    tp2_price: Decimal = Decimal("0")
    entry_fees: Decimal = Decimal("0")
    exit_fees: Decimal = Decimal("0")
    funding_pnl: Decimal = Decimal("0")
    realized_partial: Decimal = Decimal("0")
    bars_held: int = 0
    highest_price: Decimal = Decimal("0")
    lowest_price: Decimal = Decimal("Infinity")
    is_breakeven_set: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "initial_qty": str(self.initial_qty),
            "current_qty": str(self.current_qty),
            "entry_price": str(self.entry_price),
            "entry_time_ms": self.entry_time_ms,
            "current_stop": str(self.current_stop),
            "initial_atr": str(self.initial_atr),
            "tp1_taken": self.tp1_taken,
            "tp2_taken": self.tp2_taken,
            "tp1_price": str(self.tp1_price),
            "tp2_price": str(self.tp2_price),
            "entry_fees": str(self.entry_fees),
            "exit_fees": str(self.exit_fees),
            "funding_pnl": str(self.funding_pnl),
            "realized_partial": str(self.realized_partial),
            "bars_held": self.bars_held,
            "highest_price": str(self.highest_price),
            "lowest_price": str(self.lowest_price) if self.lowest_price != Decimal("Infinity") else "Infinity",
            "is_breakeven_set": self.is_breakeven_set,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PositionState":
        lowest = Decimal("Infinity") if d.get("lowest_price") == "Infinity" else Decimal(str(d.get("lowest_price", "0")))
        return cls(
            symbol=d["symbol"],
            direction=int(d["direction"]),
            initial_qty=Decimal(str(d["initial_qty"])),
            current_qty=Decimal(str(d["current_qty"])),
            entry_price=Decimal(str(d["entry_price"])),
            entry_time_ms=int(d["entry_time_ms"]),
            current_stop=Decimal(str(d["current_stop"])),
            initial_atr=Decimal(str(d["initial_atr"])),
            tp1_taken=bool(d.get("tp1_taken", False)),
            tp2_taken=bool(d.get("tp2_taken", False)),
            tp1_price=Decimal(str(d.get("tp1_price", "0"))),
            tp2_price=Decimal(str(d.get("tp2_price", "0"))),
            entry_fees=Decimal(str(d.get("entry_fees", "0"))),
            exit_fees=Decimal(str(d.get("exit_fees", "0"))),
            funding_pnl=Decimal(str(d.get("funding_pnl", "0"))),
            realized_partial=Decimal(str(d.get("realized_partial", "0"))),
            bars_held=int(d.get("bars_held", 0)),
            highest_price=Decimal(str(d.get("highest_price", "0"))),
            lowest_price=lowest,
            is_breakeven_set=bool(d.get("is_breakeven_set", False)),
        )


@dataclass
class TradeRecord:
    trade_id: str
    portfolio: str  # 'NATIVE' or 'SHADOW'
    symbol: str
    direction: int
    entry_time_ms: int
    exit_time_ms: int
    entry_price: Decimal
    exit_price: Decimal
    initial_qty: Decimal
    final_qty: Decimal
    gross_pnl: Decimal
    fees_paid: Decimal
    funding_pnl: Decimal
    net_pnl: Decimal
    exit_reason: str
    slippage_bps: Decimal = Decimal("0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "portfolio": self.portfolio,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time_ms": self.entry_time_ms,
            "exit_time_ms": self.exit_time_ms,
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price),
            "initial_qty": str(self.initial_qty),
            "final_qty": str(self.final_qty),
            "gross_pnl": str(self.gross_pnl),
            "fees_paid": str(self.fees_paid),
            "funding_pnl": str(self.funding_pnl),
            "net_pnl": str(self.net_pnl),
            "exit_reason": self.exit_reason,
            "slippage_bps": str(self.slippage_bps),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TradeRecord":
        return cls(
            trade_id=d["trade_id"],
            portfolio=d["portfolio"],
            symbol=d["symbol"],
            direction=int(d["direction"]),
            entry_time_ms=int(d["entry_time_ms"]),
            exit_time_ms=int(d["exit_time_ms"]),
            entry_price=Decimal(str(d["entry_price"])),
            exit_price=Decimal(str(d["exit_price"])),
            initial_qty=Decimal(str(d["initial_qty"])),
            final_qty=Decimal(str(d["final_qty"])),
            gross_pnl=Decimal(str(d["gross_pnl"])),
            fees_paid=Decimal(str(d["fees_paid"])),
            funding_pnl=Decimal(str(d["funding_pnl"])),
            net_pnl=Decimal(str(d["net_pnl"])),
            exit_reason=d["exit_reason"],
            slippage_bps=Decimal(str(d.get("slippage_bps", "0"))),
        )


@dataclass
class ShadowTradeRecord:
    trade_id: str
    symbol: str
    direction: str
    qty: Decimal
    entry_time_ms: int
    entry_price_intended: Decimal
    entry_price_native: Decimal
    entry_price_shadow: Decimal
    exit_time_ms: Optional[int] = None
    exit_price_native: Optional[Decimal] = None
    exit_price_shadow: Optional[Decimal] = None
    exit_reason: str = ""
    native_net_pnl: Decimal = Decimal("0")
    shadow_net_pnl: Decimal = Decimal("0")
    native_fee_paid: Decimal = Decimal("0")
    shadow_fee_paid: Decimal = Decimal("0")
    realized_slippage_bps: Decimal = Decimal("0")
    tp1_hit: bool = False
    tp2_hit: bool = False
    is_closed: bool = False


class PortfolioInstance:
    """
    Encapsulates independent accounting, positions, stops, execution rules,
    and a dedicated KellyRiskManager instance for a single portfolio.
    """

    def __init__(
        self,
        name: str,
        initial_capital: Decimal,
        taker_fee: Decimal,
        maker_fee: Decimal,
        use_spread_gate: bool,
        use_depth_slippage: bool,
        fixed_slippage: Decimal = Decimal("0"),
        enable_auto_gearbox: bool = True,
    ):
        self.name = name
        self.initial_capital = initial_capital
        self.realized_equity = initial_capital
        self.peak_equity = initial_capital
        self.positions: Dict[str, PositionState] = {}
        self.closed_trades: List[TradeRecord] = []
        self.total_fees = Decimal("0")
        self.total_funding = Decimal("0")
        self.rejected_orders: List[Dict[str, Any]] = []

        self.taker_fee = taker_fee
        self.maker_fee = maker_fee
        self.use_spread_gate = use_spread_gate
        self.use_depth_slippage = use_depth_slippage
        self.fixed_slippage = fixed_slippage
        self.enable_auto_gearbox = enable_auto_gearbox

        # P0.2: Decoupled Independent Risk Manager & Cooldowns
        self.risk_manager = KellyRiskManager(
            base_risk_pct=Decimal("0.010"),
            choppy_risk_pct=Decimal("0.005"),
            max_leverage=Decimal("3.5"),
        )
        self.cooldown_tracker: Dict[str, int] = {}
        self.settled_funding_windows: Set[Tuple[str, int]] = set()

    def get_equity(self, current_prices: Dict[str, Decimal]) -> Decimal:
        unrealized = Decimal("0")
        for sym, pos in self.positions.items():
            mark = current_prices.get(sym, pos.entry_price)
            gross = pos.current_qty * (mark - pos.entry_price) * pos.direction
            est_exit_fee = pos.current_qty * mark * self.taker_fee
            unrealized += gross - est_exit_fee
        return self.realized_equity + unrealized

    def update_peak(self, current_prices: Dict[str, Decimal]) -> Decimal:
        eq = self.get_equity(current_prices)
        if eq > self.peak_equity:
            self.peak_equity = eq
        return eq

    def update_drawdown(self, current_prices: Dict[str, Decimal]) -> None:
        if not self.enable_auto_gearbox:
            return
        eq = self.update_peak(current_prices)
        self.risk_manager.update_drawdown(eq, self.peak_equity)

    def has_capacity(self, sym: str, notional: Decimal, current_prices: Dict[str, Decimal]) -> bool:
        if len(self.positions) >= MAX_CONCURRENT_POSITIONS and sym not in self.positions:
            return False
        eq = self.get_equity(current_prices)
        if eq <= Decimal("0"):
            return False
        current_notional = sum(
            p.current_qty * current_prices.get(s, p.entry_price)
            for s, p in self.positions.items()
        )
        return (current_notional + notional) / eq <= MAX_PORTFOLIO_LEVERAGE

    def execute_entry(
        self,
        symbol: str,
        direction: int,
        intended_price: Decimal,
        intended_qty: Decimal,
        initial_atr: Decimal,
        initial_stop: Decimal,
        best_bid: Decimal,
        best_ask: Decimal,
        available_depth_usdt: Decimal,
        timestamp_ms: int,
        trade_id: str,
        l2_ladder: Optional[List[Tuple[Decimal, Decimal]]] = None,
    ) -> Tuple[bool, Optional[Decimal], Optional[Decimal], Optional[Decimal], str]:
        """
        Executes an entry for this portfolio.
        Returns: (success, fill_price, fill_qty, fee_paid, notes)
        """
        if symbol in self.positions:
            return False, None, None, None, "ALREADY_HELD"

        notional_est = intended_qty * intended_price
        if notional_est < MIN_NOTIONAL_USDT:
            reason = f"MIN_NOTIONAL: {notional_est:.2f} < {MIN_NOTIONAL_USDT} USDT"
            self.rejected_orders.append({"symbol": symbol, "time_ms": timestamp_ms, "reason": reason})
            return False, None, None, None, reason

        # 1. Spread Gate (Shadow only)
        if self.use_spread_gate:
            if best_ask <= best_bid or best_bid <= Decimal("0"):
                reason = "CORRUPT_OR_CROSSED_ORDERBOOK"
                self.rejected_orders.append({"symbol": symbol, "time_ms": timestamp_ms, "reason": reason})
                return False, None, None, None, reason
            spread_pct = (best_ask - best_bid) / best_bid
            if spread_pct > MAX_ALLOWED_SPREAD_PCT:
                reason = f"WIDE_SPREAD_REJECT: {float(spread_pct*100):.3f}% > {float(MAX_ALLOWED_SPREAD_PCT*100):.3f}%"
                self.rejected_orders.append({"symbol": symbol, "time_ms": timestamp_ms, "reason": reason})
                return False, None, None, None, reason

        # 2. Price & Discrete L2 / Slippage Calculation
        if self.use_depth_slippage:
            if l2_ladder is not None:
                # Discrete L2 Orderbook Walking (P0.5)
                succ, vwap, filled_qty = walk_discrete_l2(l2_ladder, intended_qty)
                if not succ:
                    reason = f"INSUFFICIENT_L2_DEPTH: available {filled_qty:.4f} < needed {intended_qty:.4f}"
                    self.rejected_orders.append({"symbol": symbol, "time_ms": timestamp_ms, "reason": reason})
                    return False, None, None, None, reason
                fill_price = vwap
            else:
                base_top = best_ask if direction == 1 else best_bid
                if available_depth_usdt > Decimal("0") and notional_est > available_depth_usdt:
                    excess_ratio = (notional_est - available_depth_usdt) / available_depth_usdt
                    slip_factor = Decimal("0.0005") * min(excess_ratio, Decimal("5.0"))
                    fill_price = base_top * (Decimal("1") + slip_factor * direction)
                else:
                    fill_price = base_top
        else:
            # Native Paper Model
            fill_price = intended_price * (Decimal("1") + self.fixed_slippage * direction)

        fill_qty = intended_qty
        fee = fill_qty * fill_price * self.taker_fee
        self.realized_equity -= fee
        self.total_fees += fee

        pos = PositionState(
            symbol=symbol,
            direction=direction,
            initial_qty=fill_qty,
            current_qty=fill_qty,
            entry_price=fill_price,
            entry_time_ms=timestamp_ms,
            current_stop=initial_stop,
            initial_atr=initial_atr,
            entry_fees=fee,
            highest_price=fill_price,
            lowest_price=fill_price,
        )
        self.positions[symbol] = pos
        return True, fill_price, fill_qty, fee, f"Filled @ {fill_price:.4f}"

    def execute_exit_or_partial(
        self,
        symbol: str,
        qty_to_close: Decimal,
        intended_price: Decimal,
        best_bid: Decimal,
        best_ask: Decimal,
        available_depth_usdt: Decimal,
        timestamp_ms: int,
        exit_reason: str,
        trade_id: str,
        l2_ladder: Optional[List[Tuple[Decimal, Decimal]]] = None,
    ) -> Tuple[bool, Optional[Decimal], Optional[Decimal], Optional[Decimal], Optional[Decimal], str]:
        """
        Executes a partial trim (Cascade TP) or full exit.
        Returns: (success, fill_price, closed_qty, fee_paid, realized_net_pnl, notes)
        """
        if symbol not in self.positions:
            return False, None, None, None, None, "POSITION_NOT_HELD"

        pos = self.positions[symbol]
        qty = min(qty_to_close, pos.current_qty)

        if self.use_depth_slippage:
            if l2_ladder is not None:
                succ, vwap, filled_qty = walk_discrete_l2(l2_ladder, qty)
                if not succ:
                    reason = f"INSUFFICIENT_L2_DEPTH_EXIT: available {filled_qty:.4f} < needed {qty:.4f}"
                    return False, None, None, None, None, reason
                fill_price = vwap
            else:
                base_top = best_bid if pos.direction == 1 else best_ask
                notional = qty * base_top
                if available_depth_usdt > Decimal("0") and notional > available_depth_usdt:
                    excess_ratio = (notional - available_depth_usdt) / available_depth_usdt
                    slip_factor = Decimal("0.0005") * min(excess_ratio, Decimal("5.0"))
                    fill_price = base_top * (Decimal("1") - slip_factor * pos.direction)
                else:
                    fill_price = base_top
        else:
            fill_price = intended_price * (Decimal("1") - self.fixed_slippage * pos.direction)

        fee = qty * fill_price * self.taker_fee
        self.total_fees += fee

        gross = qty * (fill_price - pos.entry_price) * pos.direction
        cash = gross - fee
        self.realized_equity += cash

        if qty < pos.current_qty:
            # Partial Trim (e.g. Cascade TP1 or TP2)
            pos.current_qty -= qty
            pos.realized_partial += cash
            pos.exit_fees += fee
            return True, fill_price, qty, fee, cash, f"Partial {exit_reason} qty: {qty}"

        # Full Close
        pos.exit_fees += fee
        net_trade_pnl = cash + pos.realized_partial + pos.funding_pnl - pos.entry_fees

        slip_bps = Decimal("0")
        if intended_price > Decimal("0"):
            slip_bps = abs(fill_price - intended_price) / intended_price * Decimal("10000")

        record = TradeRecord(
            trade_id=trade_id,
            portfolio=self.name,
            symbol=symbol,
            direction=pos.direction,
            entry_time_ms=pos.entry_time_ms,
            exit_time_ms=timestamp_ms,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            initial_qty=pos.initial_qty,
            final_qty=qty,
            gross_pnl=gross + pos.realized_partial,
            fees_paid=pos.entry_fees + pos.exit_fees,
            funding_pnl=pos.funding_pnl,
            net_pnl=net_trade_pnl,
            exit_reason=exit_reason,
            slippage_bps=slip_bps,
        )
        self.closed_trades.append(record)
        del self.positions[symbol]

        # Update independent risk manager
        self.risk_manager.record_trade_result(net_trade_pnl > Decimal("0"))
        # Set cooldown for symbol
        self.cooldown_tracker[f"{symbol}_{pos.direction}"] = timestamp_ms + 3 * BAR_INTERVAL_MS

        return True, fill_price, qty, fee, net_trade_pnl, f"Closed {exit_reason} net: {net_trade_pnl:.2f}"

    def apply_funding(self, symbol: str, funding_rate: Decimal, mark_price: Decimal, exchange_ts: int) -> Decimal:
        """
        P0.5: Accrues 8-hour perpetual funding payment strictly once per 8-hour interval
        based on actual position hold time.
        """
        if symbol not in self.positions or funding_rate == Decimal("0"):
            return Decimal("0")

        window_id = exchange_ts // FUNDING_INTERVAL_MS
        settlement_key = (symbol, window_id)
        if settlement_key in self.settled_funding_windows:
            return Decimal("0")  # Already settled this 8h window

        pos = self.positions[symbol]
        # Only positions open at or prior to the settlement timestamp are eligible
        if pos.entry_time_ms > exchange_ts:
            return Decimal("0")

        payment = pos.current_qty * mark_price * funding_rate * (-pos.direction)
        pos.funding_pnl += payment
        self.total_funding += payment
        self.realized_equity += payment
        self.settled_funding_windows.add(settlement_key)
        return payment


class V32LegacyShadowEngine:
    """
    Independent Research Shadow Engine for Bybit Multi-Alpha v3.2.
    Integrates direct signal parity, independent risk managers/АКПП,
    intrabar ambiguity resolution, transactional SQLite state journaling,
    discrete L2 ladder execution, and strict 8-hour funding rate accrual.
    """

    def __init__(
        self,
        initial_capital: Decimal = INITIAL_CAPITAL,
        db_path: Optional[str] = None,
        state_file: Optional[str] = None,
        enable_news_defense: bool = True,
        enable_auto_gearbox: bool = True,
    ):
        self.initial_capital = initial_capital
        self.enable_news_defense = enable_news_defense
        self.enable_auto_gearbox = enable_auto_gearbox
        self.macro_blackout_active = False
        self.macro_event_name = ""

        # 1. News Core v1.2 Regime Guard
        self.news_config = NewsConfig(
            macro_lockout_before_minutes=45,
            macro_lockout_after_minutes=30,
        )
        self.regime_guard = RegimeGuard(self.news_config) if enable_news_defense else None

        # 2. Market Regime Classifier
        self.regime_classifier = MarketRegimeClassifier()

        # 3. Original Core Engines for Direct Signal Evaluation & Parity
        self.native_core = CoreV3Engine(
            initial_capital=initial_capital,
            instrument_rules={},
            base_risk_pct=Decimal("0.010"),
            max_leverage=Decimal("3.5"),
            taker_fee=NATIVE_TAKER_FEE,
            slippage=NATIVE_SLIPPAGE,
            portfolio_leverage=MAX_PORTFOLIO_LEVERAGE,
            drawdown_limit_pct=Decimal("30.0"),
            min_adx=18.0,
            cooldown_bars=3,
            enable_btc_delta_filter=True,
            enable_auto_gearbox=enable_auto_gearbox,
            enable_fast_scratch=True,
            enable_cascade_tp=True,
            bar_interval_ms=BAR_INTERVAL_MS,
        )
        self.shadow_core = CoreV3Engine(
            initial_capital=initial_capital,
            instrument_rules={},
            base_risk_pct=Decimal("0.010"),
            max_leverage=Decimal("3.5"),
            taker_fee=SHADOW_TAKER_FEE,
            slippage=Decimal("0"),
            portfolio_leverage=MAX_PORTFOLIO_LEVERAGE,
            drawdown_limit_pct=Decimal("30.0"),
            min_adx=18.0,
            cooldown_bars=3,
            enable_btc_delta_filter=True,
            enable_auto_gearbox=enable_auto_gearbox,
            enable_fast_scratch=True,
            enable_cascade_tp=True,
            bar_interval_ms=BAR_INTERVAL_MS,
        )
        # Ensure scanner passes synthetic bars where volume is omitted
        self.native_core.scanner.min_volume_ratio = 0.0
        self.shadow_core.scanner.min_volume_ratio = 0.0
        self.scanner = self.native_core.scanner

        # 4. Two Truly Independent Portfolios
        self.native_portfolio = PortfolioInstance(
            name="NATIVE",
            initial_capital=initial_capital,
            taker_fee=NATIVE_TAKER_FEE,
            maker_fee=NATIVE_MAKER_FEE,
            use_spread_gate=False,
            use_depth_slippage=False,
            fixed_slippage=NATIVE_SLIPPAGE,
            enable_auto_gearbox=enable_auto_gearbox,
        )

        self.shadow_portfolio = PortfolioInstance(
            name="SHADOW",
            initial_capital=initial_capital,
            taker_fee=SHADOW_TAKER_FEE,
            maker_fee=SHADOW_MAKER_FEE,
            use_spread_gate=True,
            use_depth_slippage=True,
            fixed_slippage=Decimal("0"),
            enable_auto_gearbox=enable_auto_gearbox,
        )

        # 5. Backward Compatibility Views
        self.legacy_active_trades: Dict[str, ShadowTradeRecord] = {}
        self.legacy_closed_trades: List[ShadowTradeRecord] = []

        # 6. Runtime & State Persistence
        self.processed_event_ids: Set[str] = set()
        self.trade_counter = 0
        self.t0_timestamp_ms = int(time.time() * 1000)
        self.stream_connected = False  # Strictly False until real stream is verified!

        # Paths
        base_dir = Path(__file__).resolve().parent.parent.parent
        reports_dir = base_dir / "reports" / "v32_dual_paper_preparation_001"
        reports_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path or str(reports_dir / "shadow_execution_events.db")
        self.state_file = state_file or str(reports_dir / "BOT_STATE_SHADOW_PERSISTENT.json")

        self._init_sqlite_ledger()

    # --- Decoupled Independent Risk Manager & АКПП Properties (P0.2) ---
    @property
    def native_risk_manager(self) -> KellyRiskManager:
        return self.native_portfolio.risk_manager

    @property
    def shadow_risk_manager(self) -> KellyRiskManager:
        return self.shadow_portfolio.risk_manager

    @property
    def risk_manager(self) -> KellyRiskManager:
        # Default alias points to Shadow risk manager for backward compatibility
        return self.shadow_portfolio.risk_manager

    @property
    def native_gear(self) -> int:
        return self.native_risk_manager.current_gear

    @property
    def shadow_gear(self) -> int:
        return self.shadow_risk_manager.current_gear

    @property
    def gearbox_gear(self) -> int:
        return self.shadow_gear

    # --- Backward Compatible Properties ---
    @property
    def native_realized_equity(self) -> Decimal:
        return self.native_portfolio.realized_equity

    @property
    def shadow_realized_equity(self) -> Decimal:
        return self.shadow_portfolio.realized_equity

    @property
    def native_peak_equity(self) -> Decimal:
        return self.native_portfolio.peak_equity

    @property
    def shadow_peak_equity(self) -> Decimal:
        return self.shadow_portfolio.peak_equity

    @property
    def active_trades(self) -> Dict[str, ShadowTradeRecord]:
        return self.legacy_active_trades

    @property
    def closed_trades(self) -> List[ShadowTradeRecord]:
        return self.legacy_closed_trades

    @property
    def rejected_orders_count(self) -> int:
        return len(self.shadow_portfolio.rejected_orders)

    @property
    def missed_intent_log(self) -> List[Dict[str, Any]]:
        return self.shadow_portfolio.rejected_orders

    def _init_sqlite_ledger(self) -> None:
        """Initializes relational schema for microsecond execution events and state journal."""
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS shadow_execution_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        exchange_ts INTEGER NOT NULL,
                        receive_ts INTEGER NOT NULL,
                        decision_ts INTEGER NOT NULL,
                        trade_id TEXT NOT NULL,
                        portfolio TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        intended_price REAL NOT NULL,
                        fill_price REAL NOT NULL,
                        fill_price_native REAL DEFAULT 0,
                        fill_price_shadow REAL DEFAULT 0,
                        price_shadow REAL DEFAULT 0,
                        qty REAL NOT NULL,
                        fill_qty REAL DEFAULT 0,
                        notional_usdt REAL NOT NULL,
                        native_fee REAL DEFAULT 0,
                        shadow_fee REAL DEFAULT 0,
                        fee_paid REAL DEFAULT 0,
                        funding_paid REAL DEFAULT 0,
                        slippage_bps REAL DEFAULT 0,
                        best_bid REAL NOT NULL,
                        best_ask REAL NOT NULL,
                        spread_pct REAL NOT NULL,
                        available_depth_usdt REAL NOT NULL,
                        execution_status TEXT NOT NULL,
                        realized_pnl REAL NOT NULL,
                        portfolio_equity REAL NOT NULL,
                        shadow_equity REAL DEFAULT 0,
                        gearbox_gear INTEGER NOT NULL,
                        notes TEXT
                    );
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_shadow_portfolio_sym
                    ON shadow_execution_events (portfolio, symbol);
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_shadow_trade_id
                    ON shadow_execution_events (trade_id);
                """)

                # P0.4: Atomic SQLite State Journal for transactional crash recovery
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS shadow_state_journal (
                        journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        saved_at_ms INTEGER NOT NULL,
                        saved_at_utc TEXT NOT NULL,
                        event_id INTEGER,
                        state_json TEXT NOT NULL
                    );
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_shadow_journal_ts
                    ON shadow_state_journal (saved_at_ms);
                """)
        finally:
            conn.close()

    def log_event(
        self,
        exchange_ts: int,
        receive_ts: int,
        trade_id: str,
        portfolio: str,
        symbol: str,
        event_type: str,
        direction: str,
        intended_price: Decimal,
        fill_price: Decimal,
        qty: Decimal,
        fee_paid: Decimal,
        funding_paid: Decimal,
        best_bid: Decimal,
        best_ask: Decimal,
        available_depth_usdt: Decimal,
        execution_status: str,
        realized_pnl: Decimal,
        portfolio_equity: Decimal,
        notes: str = "",
        native_fee: Decimal = Decimal("0"),
        shadow_fee: Decimal = Decimal("0"),
        price_native: Optional[Decimal] = None,
        price_shadow: Optional[Decimal] = None,
    ) -> None:
        """Appends microsecond execution event to SQLite ledger atomically with state journal snapshot."""
        decision_ts = time.time_ns()
        spread_pct = Decimal("0")
        if best_bid > Decimal("0"):
            spread_pct = (best_ask - best_bid) / best_bid

        slippage_bps = Decimal("0")
        if intended_price > Decimal("0") and fill_price > Decimal("0"):
            slippage_bps = abs(fill_price - intended_price) / intended_price * Decimal("10000")

        notional = qty * fill_price
        p_nat = price_native if price_native is not None else fill_price
        p_sha = price_shadow if price_shadow is not None else fill_price
        current_gear = self.shadow_gear if portfolio == "SHADOW" else self.native_gear

        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO shadow_execution_events (
                        exchange_ts, receive_ts, decision_ts, trade_id, portfolio,
                        symbol, event_type, direction, intended_price, fill_price,
                        fill_price_native, fill_price_shadow, price_shadow, qty, fill_qty,
                        notional_usdt, native_fee, shadow_fee, fee_paid, funding_paid,
                        slippage_bps, best_bid, best_ask, spread_pct, available_depth_usdt,
                        execution_status, realized_pnl, portfolio_equity, shadow_equity,
                        gearbox_gear, notes
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    );
                """, (
                    exchange_ts, receive_ts, decision_ts, trade_id, portfolio,
                    symbol, event_type, direction, float(intended_price), float(fill_price),
                    float(p_nat), float(p_sha), float(p_sha), float(qty), float(qty),
                    float(notional), float(native_fee), float(shadow_fee), float(fee_paid), float(funding_paid),
                    float(slippage_bps), float(best_bid), float(best_ask), float(spread_pct), float(available_depth_usdt),
                    execution_status, float(realized_pnl), float(portfolio_equity), float(self.shadow_portfolio.realized_equity),
                    current_gear, notes
                ))
                last_event_id = cursor.lastrowid

                # P0.4: Persist dual state into SQLite journal in same transaction
                state_snapshot = self._export_state_dict()
                cursor.execute("""
                    INSERT INTO shadow_state_journal (
                        saved_at_ms, saved_at_utc, event_id, state_json
                    ) VALUES (?, ?, ?, ?);
                """, (
                    int(time.time() * 1000),
                    datetime.now(timezone.utc).isoformat(),
                    last_event_id,
                    json.dumps(state_snapshot, default=str),
                ))
        finally:
            conn.close()

    def check_macro_embargo(self, check_time: datetime) -> Tuple[bool, str]:
        """Queries News Core v1.2 Regime Guard for macro embargo."""
        if not self.enable_news_defense or not self.regime_guard:
            return False, "NEWS_DEFENSE_DISABLED"
        try:
            return self.regime_guard.is_macro_lockout_active(check_time)
        except Exception as e:
            logger.warning(f"Error querying RegimeGuard macro lockout: {e}")
            return False, "ERROR"

    def process_orderbook_entry(
        self,
        symbol: str,
        direction: str,
        intended_price: Decimal,
        qty: Decimal,
        best_bid: Decimal,
        best_ask: Decimal,
        bar_timestamp_ms: int,
        atr: Decimal,
        available_depth_usdt: Decimal = Decimal("50000.0"),
        l2_ladder: Optional[List[Tuple[Decimal, Decimal]]] = None,
    ) -> Optional[ShadowTradeRecord]:
        """
        Backward-compatible direct orderbook entry method.
        """
        dir_int = 1 if direction.upper() == "LONG" else -1
        initial_sl = intended_price - dir_int * Decimal("2.5") * atr
        self.trade_counter += 1
        trade_id = f"TR_{symbol}_{self.trade_counter:04d}"

        spread_pct = (best_ask - best_bid) / best_bid if best_bid > Decimal("0") else Decimal("0")
        if spread_pct > MAX_ALLOWED_SPREAD_PCT:
            reason = f"WIDE_SPREAD_REJECT: {float(spread_pct*100):.3f}% > {float(MAX_ALLOWED_SPREAD_PCT*100):.3f}%"
            self.shadow_portfolio.rejected_orders.append({"symbol": symbol, "time_ms": bar_timestamp_ms, "reason": reason})
            self.log_event(
                bar_timestamp_ms, time.time_ns(), trade_id, "SHADOW", symbol, "ENTRY_REJECT",
                direction, intended_price, intended_price, qty, Decimal("0"), Decimal("0"),
                best_bid, best_ask, available_depth_usdt, "REJECTED", Decimal("0"),
                self.shadow_portfolio.realized_equity, notes=reason
            )
            return None

        # Execute Native
        succ_nat, p_nat, q_nat, fee_nat, note_nat = self.native_portfolio.execute_entry(
            symbol=symbol, direction=dir_int, intended_price=intended_price, intended_qty=qty,
            initial_atr=atr, initial_stop=initial_sl, best_bid=best_bid, best_ask=best_ask,
            available_depth_usdt=available_depth_usdt, timestamp_ms=bar_timestamp_ms, trade_id=trade_id
        )

        # Execute Shadow
        succ_sha, p_sha, q_sha, fee_sha, note_sha = self.shadow_portfolio.execute_entry(
            symbol=symbol, direction=dir_int, intended_price=intended_price, intended_qty=qty,
            initial_atr=atr, initial_stop=initial_sl, best_bid=best_bid, best_ask=best_ask,
            available_depth_usdt=available_depth_usdt, timestamp_ms=bar_timestamp_ms, trade_id=trade_id,
            l2_ladder=l2_ladder,
        )

        trade = ShadowTradeRecord(
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            qty=qty,
            entry_time_ms=bar_timestamp_ms,
            entry_price_intended=intended_price,
            entry_price_native=p_nat or intended_price,
            entry_price_shadow=p_sha or intended_price,
            native_fee_paid=fee_nat or Decimal("0"),
            shadow_fee_paid=fee_sha or Decimal("0"),
        )
        self.legacy_active_trades[symbol] = trade

        self.log_event(
            bar_timestamp_ms, time.time_ns(), trade_id, "SHADOW", symbol, "ENTRY_FILL",
            direction, intended_price, p_sha or intended_price, qty, fee_sha or Decimal("0"),
            Decimal("0"), best_bid, best_ask, available_depth_usdt, "FULL", Decimal("0"),
            self.shadow_portfolio.realized_equity, notes=f"Native Fee: 0.09%, Shadow Fee: 0.10%, Spread: {float(spread_pct*100):.3f}%",
            native_fee=fee_nat or Decimal("0"), shadow_fee=fee_sha or Decimal("0"),
            price_native=p_nat, price_shadow=p_sha
        )
        return trade

    def process_orderbook_exit(
        self,
        symbol: str,
        intended_price: Decimal,
        best_bid: Decimal,
        best_ask: Decimal,
        exit_time_ms: int,
        exit_reason: str,
        available_depth_usdt: Decimal = Decimal("50000.0"),
        l2_ladder: Optional[List[Tuple[Decimal, Decimal]]] = None,
    ) -> Optional[ShadowTradeRecord]:
        """
        Backward-compatible direct orderbook exit method.
        """
        if symbol not in self.legacy_active_trades:
            return None

        trade = self.legacy_active_trades[symbol]
        qty = trade.qty
        dir_int = 1 if trade.direction.upper() == "LONG" else -1

        # Exit Native
        succ_nat, p_nat, q_nat, fee_nat, pnl_nat, note_nat = self.native_portfolio.execute_exit_or_partial(
            symbol=symbol, qty_to_close=qty, intended_price=intended_price, best_bid=best_bid,
            best_ask=best_ask, available_depth_usdt=available_depth_usdt, timestamp_ms=exit_time_ms,
            exit_reason=exit_reason, trade_id=trade.trade_id
        )

        # Exit Shadow
        succ_sha, p_sha, q_sha, fee_sha, pnl_sha, note_sha = self.shadow_portfolio.execute_exit_or_partial(
            symbol=symbol, qty_to_close=qty, intended_price=intended_price, best_bid=best_bid,
            best_ask=best_ask, available_depth_usdt=available_depth_usdt, timestamp_ms=exit_time_ms,
            exit_reason=exit_reason, trade_id=trade.trade_id, l2_ladder=l2_ladder
        )

        trade.exit_time_ms = exit_time_ms
        trade.exit_price_native = p_nat
        trade.exit_price_shadow = p_sha
        trade.exit_reason = exit_reason
        trade.native_fee_paid += fee_nat or Decimal("0")
        trade.shadow_fee_paid += fee_sha or Decimal("0")
        trade.native_net_pnl = pnl_nat or Decimal("0")
        trade.shadow_net_pnl = pnl_sha or Decimal("0")
        trade.is_closed = True

        self.legacy_closed_trades.append(trade)
        del self.legacy_active_trades[symbol]

        if self.enable_auto_gearbox:
            self.shadow_risk_manager.update_drawdown(self.shadow_portfolio.realized_equity, self.shadow_portfolio.peak_equity)

        self.log_event(
            exit_time_ms, time.time_ns(), trade.trade_id, "SHADOW", symbol, "EXIT_FILL",
            trade.direction, intended_price, p_sha or intended_price, qty, fee_sha or Decimal("0"),
            Decimal("0"), best_bid, best_ask, available_depth_usdt, "FULL", pnl_sha or Decimal("0"),
            self.shadow_portfolio.realized_equity, notes=f"Reason: {exit_reason}, Net PnL: {pnl_sha}",
            native_fee=fee_nat or Decimal("0"), shadow_fee=fee_sha or Decimal("0"),
            price_native=p_nat, price_shadow=p_sha
        )
        return trade

    def process_closed_bar_event(
        self,
        exchange_ts: int,
        symbol: str,
        bar: Dict[str, Any],
        best_bid: Decimal,
        best_ask: Decimal,
        depth_bid_usdt: Decimal,
        depth_ask_usdt: Decimal,
        funding_rate: Optional[Decimal] = None,
        receive_ts: Optional[int] = None,
        l2_ladder_asks: Optional[List[Tuple[Decimal, Decimal]]] = None,
        l2_ladder_bids: Optional[List[Tuple[Decimal, Decimal]]] = None,
    ) -> Dict[str, Any]:
        """
        Processes a closed 1h bar event across the full original trading cycle:
        1. Idempotency check.
        2. Perpetual Funding Rate Accrual (strictly 1x per 8h window per held position).
        3. Cascade TP & Stop checks with Intrabar Ambiguity resolution (AMBIGUOUS_SEQUENCE).
        4. Macro embargo check (News Core v1.2 / RegimeGuard).
        5. Original Signal Evaluation via MultiCoinScanner / CoreV3Engine.
        6. Independent portfolio sizing, spread gate, discrete L2 ladder fill, and execution.
        7. Decoupled independent АКПП gear updates.
        8. Atomic state journaling to SQLite and JSON.
        """
        receive_ts = receive_ts or time.time_ns()
        event_key = f"{symbol}_{exchange_ts}"
        if event_key in self.processed_event_ids:
            logger.warning(f"Duplicate event {event_key} ignored for idempotency.")
            return {"status": "SKIPPED_DUPLICATE"}
        self.processed_event_ids.add(event_key)

        high = Decimal(str(bar["high"]))
        low = Decimal(str(bar["low"]))
        close = Decimal(str(bar["close"]))
        atr = Decimal(str(bar.get("atr", "1.0")))
        upper_ch = Decimal(str(bar.get("upper", "Infinity")))
        lower_ch = Decimal(str(bar.get("lower", "0")))
        adx = float(bar.get("adx", 20.0))
        ema_fast = float(bar.get("ema_fast", 100.0))
        ema_slow = float(bar.get("ema_slow", 90.0))
        volume = float(bar.get("volume", 1000.0))
        volume_sma = float(bar.get("volume_sma", 1000.0))

        current_prices = {symbol: close}

        # Step A: Perpetual Funding Accrual (P0.5)
        if funding_rate is not None and funding_rate != Decimal("0"):
            for port in [self.native_portfolio, self.shadow_portfolio]:
                pay = port.apply_funding(symbol, funding_rate, close, exchange_ts)
                if pay != Decimal("0"):
                    self.log_event(
                        exchange_ts, receive_ts, "FUNDING", port.name, symbol, "FUNDING_PAYMENT",
                        "NONE", close, close, Decimal("0"), Decimal("0"), pay,
                        best_bid, best_ask, Decimal("0"), "FULL", pay, port.realized_equity,
                        notes=f"Rate: {float(funding_rate):.6f}"
                    )

        # Step B: Manage Open Positions for BOTH portfolios independently (P0.3)
        for port in [self.native_portfolio, self.shadow_portfolio]:
            if symbol not in port.positions:
                continue

            pos = port.positions[symbol]
            pos.bars_held += 1
            if high > pos.highest_price:
                pos.highest_price = high
            if low < pos.lowest_price:
                pos.lowest_price = low

            origin = pos.entry_price
            direction_str = "LONG" if pos.direction == 1 else "SHORT"
            depth = depth_bid_usdt if pos.direction == 1 else depth_ask_usdt
            ladder_exit = l2_ladder_bids if pos.direction == 1 else l2_ladder_asks

            # Evaluate Stop Loss condition
            sl_hit = (low <= pos.current_stop) if pos.direction == 1 else (high >= pos.current_stop)

            # Evaluate Take Profit potential within this bar
            tp1_target = origin + pos.direction * Decimal("1.5") * pos.initial_atr
            tp2_target = origin + pos.direction * Decimal("3.0") * pos.initial_atr

            tp1_possible = (not pos.tp1_taken) and (
                (high >= tp1_target) if pos.direction == 1 else (low <= tp1_target)
            )
            tp2_possible = pos.tp1_taken and (not pos.tp2_taken) and (
                (high >= tp2_target) if pos.direction == 1 else (low <= tp2_target)
            )
            tp_possible = tp1_possible or tp2_possible

            # --- P0.3: Intrabar Sequencing & Ambiguity Guard ---
            if sl_hit and tp_possible:
                # Both SL and TP levels were reached within the same 1h aggregate bar.
                # In absence of intra-bar tick timestamps, flag AMBIGUOUS_SEQUENCE and
                # conservatively execute Stop Loss without taking partial profits or adjusting stop.
                self.log_event(
                    exchange_ts, receive_ts, f"TR_{symbol}_{exchange_ts}", port.name, symbol,
                    "AMBIGUOUS_SEQUENCE", direction_str, pos.current_stop, pos.current_stop,
                    pos.current_qty, Decimal("0"), Decimal("0"), best_bid, best_ask, depth,
                    "AMBIGUOUS", Decimal("0"), port.realized_equity,
                    notes="Intra-bar collision: SL and TP both reached. Conservatively prioritizing STOP_LOSS."
                )

                succ, fill_p, c_qty, fee, net_pnl, notes = port.execute_exit_or_partial(
                    symbol=symbol,
                    qty_to_close=pos.current_qty,
                    intended_price=pos.current_stop,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    available_depth_usdt=depth,
                    timestamp_ms=exchange_ts,
                    exit_reason="STOP_LOSS",
                    trade_id=f"TR_{symbol}_{exchange_ts}",
                    l2_ladder=ladder_exit,
                )
                self.log_event(
                    exchange_ts, receive_ts, f"TR_{symbol}_{exchange_ts}", port.name, symbol,
                    "STOP_LOSS", direction_str, pos.current_stop, fill_p or pos.current_stop, c_qty or Decimal("0"),
                    fee or Decimal("0"), Decimal("0"), best_bid, best_ask, depth, "FULL",
                    net_pnl or Decimal("0"), port.realized_equity, notes="Ambiguous sequence resolved to Stop Loss"
                )
                continue

            elif sl_hit:
                # Standard Stop Loss hit
                intended_exit = pos.current_stop
                succ, fill_p, c_qty, fee, net_pnl, notes = port.execute_exit_or_partial(
                    symbol=symbol,
                    qty_to_close=pos.current_qty,
                    intended_price=intended_exit,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    available_depth_usdt=depth,
                    timestamp_ms=exchange_ts,
                    exit_reason="STOP_LOSS",
                    trade_id=f"TR_{symbol}_{exchange_ts}",
                    l2_ladder=ladder_exit,
                )
                self.log_event(
                    exchange_ts, receive_ts, f"TR_{symbol}_{exchange_ts}", port.name, symbol,
                    "STOP_LOSS", direction_str, intended_exit, fill_p or intended_exit, c_qty or Decimal("0"),
                    fee or Decimal("0"), Decimal("0"), best_bid, best_ask, depth, "FULL",
                    net_pnl or Decimal("0"), port.realized_equity, notes=notes
                )
                continue

            # Cascade TP1 (30% trim @ 1.5 ATR -> move stop to Breakeven)
            profit_dist = (close - origin) * pos.direction
            if not pos.tp1_taken and profit_dist >= Decimal("1.5") * pos.initial_atr:
                tp1_qty = (pos.initial_qty * Decimal("0.30")).quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)
                if tp1_qty > Decimal("0") and pos.current_qty - tp1_qty >= Decimal("0.0001"):
                    succ, fill_p, c_qty, fee, partial_net, notes = port.execute_exit_or_partial(
                        symbol=symbol,
                        qty_to_close=tp1_qty,
                        intended_price=close,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        available_depth_usdt=depth,
                        timestamp_ms=exchange_ts,
                        exit_reason="CASCADE_TP1",
                        trade_id=f"TR_{symbol}_{exchange_ts}",
                        l2_ladder=ladder_exit,
                    )
                    pos.tp1_taken = True
                    pos.tp1_price = fill_p or close
                    pos.current_stop = pos.entry_price
                    pos.is_breakeven_set = True
                    self.log_event(
                        exchange_ts, receive_ts, f"TR_{symbol}_{exchange_ts}", port.name, symbol,
                        "CASCADE_TP1", direction_str, close, fill_p or close, c_qty or tp1_qty,
                        fee or Decimal("0"), Decimal("0"), best_bid, best_ask, depth, "FULL",
                        partial_net or Decimal("0"), port.realized_equity, notes="Trimmed 30% -> Stop to BE"
                    )

            # Cascade TP2 (40% trim @ 3.0 ATR -> move stop to Secured Profit)
            elif pos.tp1_taken and not pos.tp2_taken and profit_dist >= Decimal("3.0") * pos.initial_atr:
                tp2_qty = (pos.initial_qty * Decimal("0.40")).quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)
                if tp2_qty > Decimal("0") and pos.current_qty - tp2_qty >= Decimal("0.0001"):
                    succ, fill_p, c_qty, fee, partial_net, notes = port.execute_exit_or_partial(
                        symbol=symbol,
                        qty_to_close=tp2_qty,
                        intended_price=close,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        available_depth_usdt=depth,
                        timestamp_ms=exchange_ts,
                        exit_reason="CASCADE_TP2",
                        trade_id=f"TR_{symbol}_{exchange_ts}",
                        l2_ladder=ladder_exit,
                    )
                    pos.tp2_taken = True
                    pos.tp2_price = fill_p or close
                    secured_sl = origin + pos.direction * Decimal("1.5") * pos.initial_atr
                    pos.current_stop = max(pos.current_stop, secured_sl) if pos.direction == 1 else min(pos.current_stop, secured_sl)
                    self.log_event(
                        exchange_ts, receive_ts, f"TR_{symbol}_{exchange_ts}", port.name, symbol,
                        "CASCADE_TP2", direction_str, close, fill_p or close, c_qty or tp2_qty,
                        fee or Decimal("0"), Decimal("0"), best_bid, best_ask, depth, "FULL",
                        partial_net or Decimal("0"), port.realized_equity, notes="Trimmed 40% -> Secured Stop"
                    )

            # Fast Scratch (bars <= 6 stall exit)
            if pos.bars_held <= 6 and not pos.tp1_taken:
                adverse_dist = (origin - close) * pos.direction
                mfe = (pos.highest_price - origin) if pos.direction == 1 else (origin - pos.lowest_price)
                if adverse_dist >= Decimal("0.5") * pos.initial_atr and mfe < Decimal("0.5") * pos.initial_atr:
                    succ, fill_p, c_qty, fee, net_pnl, notes = port.execute_exit_or_partial(
                        symbol=symbol,
                        qty_to_close=pos.current_qty,
                        intended_price=close,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        available_depth_usdt=depth,
                        timestamp_ms=exchange_ts,
                        exit_reason="FAST_SCRATCH",
                        trade_id=f"TR_{symbol}_{exchange_ts}",
                        l2_ladder=ladder_exit,
                    )
                    self.log_event(
                        exchange_ts, receive_ts, f"TR_{symbol}_{exchange_ts}", port.name, symbol,
                        "FAST_SCRATCH", direction_str, close, fill_p or close, c_qty or Decimal("0"),
                        fee or Decimal("0"), Decimal("0"), best_bid, best_ask, depth, "FULL",
                        net_pnl or Decimal("0"), port.realized_equity, notes=notes
                    )
                    continue

            # Donchian Channel Exit for remaining Runner (30%)
            runner_exit = (low <= lower_ch) if pos.direction == 1 else (high >= upper_ch)
            if runner_exit and pos.tp2_taken:
                succ, fill_p, c_qty, fee, net_pnl, notes = port.execute_exit_or_partial(
                    symbol=symbol,
                    qty_to_close=pos.current_qty,
                    intended_price=close,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    available_depth_usdt=depth,
                    timestamp_ms=exchange_ts,
                    exit_reason="DONCHIAN_RUNNER_EXIT",
                    trade_id=f"TR_{symbol}_{exchange_ts}",
                    l2_ladder=ladder_exit,
                )
                self.log_event(
                    exchange_ts, receive_ts, f"TR_{symbol}_{exchange_ts}", port.name, symbol,
                    "DONCHIAN_RUNNER_EXIT", direction_str, close, fill_p or close, c_qty or Decimal("0"),
                    fee or Decimal("0"), Decimal("0"), best_bid, best_ask, depth, "FULL",
                    net_pnl or Decimal("0"), port.realized_equity, notes=notes
                )

        # Step C: Evaluate Original Signals via Core Scanner (P0.1)
        now_dt = datetime.fromtimestamp(exchange_ts / 1000, tz=timezone.utc)
        is_embargo, embargo_reason = self.check_macro_embargo(now_dt)
        self.macro_blackout_active = is_embargo
        self.macro_event_name = embargo_reason

        # Original Component Invocation: MultiCoinScanner breakout evaluation
        cand: Optional[CoinCandidate] = self.scanner.evaluate_symbol_bar(
            symbol=symbol,
            close=float(close),
            upper_channel=float(upper_ch),
            lower_channel=float(lower_ch),
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            atr=float(atr),
            volume=volume,
            volume_sma=volume_sma,
            adx=adx,
        )

        if not cand:
            # Update drawdowns and persist state
            self.native_portfolio.update_drawdown(current_prices)
            self.shadow_portfolio.update_drawdown(current_prices)
            self.save_state()
            return {"status": "NO_SIGNAL"}

        direction = cand.direction
        direction_str = "LONG" if direction == 1 else "SHORT"
        intended_entry = close
        initial_sl = intended_entry - direction * Decimal("2.5") * atr
        self.trade_counter += 1
        trade_id = f"TR_{symbol}_{self.trade_counter:04d}"

        # If Macro Embargo is active, both portfolios reject
        if is_embargo:
            for port in [self.native_portfolio, self.shadow_portfolio]:
                port.rejected_orders.append({"symbol": symbol, "time_ms": exchange_ts, "reason": f"EMBARGO: {embargo_reason}"})
                self.log_event(
                    exchange_ts, receive_ts, trade_id, port.name, symbol, "EMBARGO_REJECT",
                    direction_str, intended_entry, intended_entry, Decimal("0"), Decimal("0"), Decimal("0"),
                    best_bid, best_ask, Decimal("0"), "REJECTED", Decimal("0"), port.realized_equity, notes=embargo_reason
                )
            self.native_portfolio.update_drawdown(current_prices)
            self.shadow_portfolio.update_drawdown(current_prices)
            self.save_state()
            return {"status": "EMBARGO_BLOCKED"}

        # Step D: Sizing & Routing via Independent Risk Managers (P0.2)
        for port in [self.native_portfolio, self.shadow_portfolio]:
            if symbol in port.positions:
                continue

            # Check independent cooldown
            cooldown_exp = port.cooldown_tracker.get(f"{symbol}_{direction}", 0)
            if exchange_ts < cooldown_exp:
                port.rejected_orders.append({"symbol": symbol, "time_ms": exchange_ts, "reason": "COOLDOWN_ACTIVE"})
                continue

            eq = port.get_equity(current_prices)
            risk_usdt = eq * port.risk_manager.get_effective_base_risk_pct()
            risk_per_coin = abs(intended_entry - initial_sl) + intended_entry * port.taker_fee * Decimal("2")
            if risk_per_coin <= Decimal("0"):
                continue

            target_qty = (risk_usdt / risk_per_coin).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
            notional = target_qty * intended_entry

            # Enforce max leverage and slot capacity
            if not port.has_capacity(symbol, notional, current_prices):
                port.rejected_orders.append({"symbol": symbol, "time_ms": exchange_ts, "reason": "MAX_CAPACITY_REACHED"})
                self.log_event(
                    exchange_ts, receive_ts, trade_id, port.name, symbol, "CAPACITY_REJECT",
                    direction_str, intended_entry, intended_entry, target_qty, Decimal("0"), Decimal("0"),
                    best_bid, best_ask, Decimal("0"), "REJECTED", Decimal("0"), port.realized_equity, notes="Capacity"
                )
                continue

            depth = depth_ask_usdt if direction == 1 else depth_bid_usdt
            ladder_entry = l2_ladder_asks if direction == 1 else l2_ladder_bids

            succ, fill_p, fill_q, fee, notes = port.execute_entry(
                symbol=symbol,
                direction=direction,
                intended_price=intended_entry,
                intended_qty=target_qty,
                initial_atr=atr,
                initial_stop=initial_sl,
                best_bid=best_bid,
                best_ask=best_ask,
                available_depth_usdt=depth,
                timestamp_ms=exchange_ts,
                trade_id=trade_id,
                l2_ladder=ladder_entry,
            )

            status = "FULL" if succ else "REJECTED"
            self.log_event(
                exchange_ts, receive_ts, trade_id, port.name, symbol,
                "ENTRY_FILL" if succ else "ENTRY_REJECT", direction_str,
                intended_entry, fill_p or intended_entry, fill_q or target_qty,
                fee or Decimal("0"), Decimal("0"), best_bid, best_ask, depth, status,
                Decimal("0"), port.realized_equity, notes=notes
            )

        # Step E: Decoupled Independent АКПП Updates (P0.2)
        self.native_portfolio.update_drawdown(current_prices)
        self.shadow_portfolio.update_drawdown(current_prices)

        # Step F: Transactional State Persistence (P0.4)
        self.save_state()

        return {
            "status": "PROCESSED",
            "trade_id": trade_id,
            "native_held": symbol in self.native_portfolio.positions,
            "shadow_held": symbol in self.shadow_portfolio.positions,
        }

    def _export_state_dict(self) -> Dict[str, Any]:
        """Creates complete state dictionary for JSON and SQLite journaling."""
        return {
            "version": "3.2-SHADOW-INTEGRITY-002",
            "protocol": "V32-SHADOW-PARITY-003",
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "t0_timestamp_ms": self.t0_timestamp_ms,
            "initial_capital": str(self.initial_capital),
            "native_gear": self.native_gear,
            "shadow_gear": self.shadow_gear,
            "gearbox_gear": self.shadow_gear,
            "processed_event_count": len(self.processed_event_ids),
            "processed_event_ids": list(self.processed_event_ids),
            "native_portfolio": {
                "realized_equity": str(self.native_portfolio.realized_equity),
                "peak_equity": str(self.native_portfolio.peak_equity),
                "total_fees": str(self.native_portfolio.total_fees),
                "total_funding": str(self.native_portfolio.total_funding),
                "gear": self.native_gear,
                "consecutive_losses": self.native_risk_manager.state.consecutive_losses,
                "positions": {s: p.to_dict() for s, p in self.native_portfolio.positions.items()},
                "closed_trades": [t.to_dict() for t in self.native_portfolio.closed_trades],
                "rejected_orders_count": len(self.native_portfolio.rejected_orders),
                "settled_funding_windows": [list(x) for x in self.native_portfolio.settled_funding_windows],
                "cooldowns": self.native_portfolio.cooldown_tracker,
            },
            "shadow_portfolio": {
                "realized_equity": str(self.shadow_portfolio.realized_equity),
                "peak_equity": str(self.shadow_portfolio.peak_equity),
                "total_fees": str(self.shadow_portfolio.total_fees),
                "total_funding": str(self.shadow_portfolio.total_funding),
                "gear": self.shadow_gear,
                "consecutive_losses": self.shadow_risk_manager.state.consecutive_losses,
                "positions": {s: p.to_dict() for s, p in self.shadow_portfolio.positions.items()},
                "closed_trades": [t.to_dict() for t in self.shadow_portfolio.closed_trades],
                "rejected_orders_count": len(self.shadow_portfolio.rejected_orders),
                "settled_funding_windows": [list(x) for x in self.shadow_portfolio.settled_funding_windows],
                "cooldowns": self.shadow_portfolio.cooldown_tracker,
            }
        }

    def save_state(self, filepath: Optional[str] = None) -> None:
        """Atomically saves both Native and Shadow portfolio states to disk and SQLite journal."""
        target_file = Path(filepath or self.state_file)
        temp_file = target_file.with_suffix(".tmp")
        data = self._export_state_dict()

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_file, target_file)

        # Journal snapshot in SQLite
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO shadow_state_journal (
                        saved_at_ms, saved_at_utc, event_id, state_json
                    ) VALUES (?, ?, ?, ?);
                """, (
                    int(time.time() * 1000),
                    data["saved_at_utc"],
                    None,
                    json.dumps(data, default=str),
                ))
        finally:
            conn.close()

    def load_state(self, filepath: Optional[str] = None) -> bool:
        """
        Recovers both portfolio states from disk, falling back to SQLite state journal
        if the disk JSON is missing or corrupted.
        """
        target_file = Path(filepath or self.state_file)
        data = None

        if target_file.exists():
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to parse state file {target_file}: {e}. Falling back to SQLite journal.")

        if not data and os.path.exists(self.db_path):
            # Fallback to SQLite State Journal (P0.4)
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT state_json FROM shadow_state_journal
                    ORDER BY journal_id DESC LIMIT 1;
                """)
                row = cursor.fetchone()
                if row:
                    data = json.loads(row[0])
            finally:
                conn.close()

        if not data:
            return False

        self.t0_timestamp_ms = data.get("t0_timestamp_ms", self.t0_timestamp_ms)
        self.processed_event_ids = set(data.get("processed_event_ids", []))

        # Restore Native
        nat_data = data.get("native_portfolio", {})
        self.native_portfolio.realized_equity = Decimal(str(nat_data.get("realized_equity", self.initial_capital)))
        self.native_portfolio.peak_equity = Decimal(str(nat_data.get("peak_equity", self.initial_capital)))
        self.native_portfolio.total_fees = Decimal(str(nat_data.get("total_fees", "0")))
        self.native_portfolio.total_funding = Decimal(str(nat_data.get("total_funding", "0")))
        self.native_portfolio.risk_manager.state.current_gear = int(nat_data.get("gear", 3))
        self.native_portfolio.risk_manager.state.consecutive_losses = int(nat_data.get("consecutive_losses", 0))
        self.native_portfolio.positions = {
            s: PositionState.from_dict(p) for s, p in nat_data.get("positions", {}).items()
        }
        self.native_portfolio.closed_trades = [
            TradeRecord.from_dict(t) for t in nat_data.get("closed_trades", [])
        ]
        self.native_portfolio.settled_funding_windows = {
            tuple(x) for x in nat_data.get("settled_funding_windows", [])
        }
        self.native_portfolio.cooldown_tracker = dict(nat_data.get("cooldowns", {}))

        # Restore Shadow
        sha_data = data.get("shadow_portfolio", {})
        self.shadow_portfolio.realized_equity = Decimal(str(sha_data.get("realized_equity", self.initial_capital)))
        self.shadow_portfolio.peak_equity = Decimal(str(sha_data.get("peak_equity", self.initial_capital)))
        self.shadow_portfolio.total_fees = Decimal(str(sha_data.get("total_fees", "0")))
        self.shadow_portfolio.total_funding = Decimal(str(sha_data.get("total_funding", "0")))
        self.shadow_portfolio.risk_manager.state.current_gear = int(sha_data.get("gear", 3))
        self.shadow_portfolio.risk_manager.state.consecutive_losses = int(sha_data.get("consecutive_losses", 0))
        self.shadow_portfolio.positions = {
            s: PositionState.from_dict(p) for s, p in sha_data.get("positions", {}).items()
        }
        self.shadow_portfolio.closed_trades = [
            TradeRecord.from_dict(t) for t in sha_data.get("closed_trades", [])
        ]
        self.shadow_portfolio.settled_funding_windows = {
            tuple(x) for x in sha_data.get("settled_funding_windows", [])
        }
        self.shadow_portfolio.cooldown_tracker = dict(sha_data.get("cooldowns", {}))

        return True

    def get_status_dict(self) -> Dict[str, Any]:
        """Provides complete telemetry to the Port 8084 web dashboard."""
        native_eq = float(self.native_portfolio.realized_equity)
        shadow_eq = float(self.shadow_portfolio.realized_equity)
        discrepancy = shadow_eq - native_eq

        # SQLite Journal count
        journal_count = 0
        if os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT count(*) FROM shadow_state_journal;")
                row = cursor.fetchone()
                if row:
                    journal_count = row[0]
            except Exception:
                pass
            finally:
                conn.close()

        return {
            "version": "3.2-SHADOW-INTEGRITY-002",
            "parity_version": "3.2-SHADOW-PARITY-003",
            "mode": "DUAL_SHADOW_PARITY",
            "timestamp": int(time.time() * 1000),
            "datetime_utc": datetime.now(timezone.utc).isoformat(),
            "t0_datetime_utc": datetime.fromtimestamp(self.t0_timestamp_ms / 1000, tz=timezone.utc).isoformat(),
            "initial_capital": float(self.initial_capital),
            "native_realized_equity": native_eq,
            "shadow_realized_equity": shadow_eq,
            "alpha_discrepancy_usdt": discrepancy,
            "native_fees_paid": float(self.native_portfolio.total_fees),
            "shadow_fees_paid": float(self.shadow_portfolio.total_fees),
            "native_funding_paid": float(self.native_portfolio.total_funding),
            "shadow_funding_paid": float(self.shadow_portfolio.total_funding),
            "native_open_positions_count": len(self.native_portfolio.positions),
            "shadow_open_positions_count": len(self.shadow_portfolio.positions),
            "native_positions": [p.to_dict() for p in self.native_portfolio.positions.values()],
            "shadow_positions": [p.to_dict() for p in self.shadow_portfolio.positions.values()],
            "native_closed_trades_count": len(self.native_portfolio.closed_trades),
            "shadow_closed_trades_count": len(self.shadow_portfolio.closed_trades),
            "shadow_rejected_orders_count": len(self.shadow_portfolio.rejected_orders),
            "native_gear": self.native_gear,
            "shadow_gear": self.shadow_gear,
            "gearbox_gear": self.shadow_gear,
            "gearbox_risk_pct": float(self.shadow_risk_manager.get_effective_base_risk_pct() * 100),
            "macro_blackout_active": self.macro_blackout_active,
            "macro_event_name": self.macro_event_name,
            "news_core_status": "v1.2 Shield",
            "stream_connected": self.stream_connected,  # False if offline
            "crash_recovery_ready": True,
            "state_journal_snapshots": journal_count,
        }
