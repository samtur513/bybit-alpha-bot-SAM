"""
Range Mean-Reversion Submodule v2.1 (Maker Engine).
Strict adherence to quantitative institutional protocol:
1. Pure Maker limit orders for all entries and Take Profits (0.0324% fee with MNT discount).
2. Zero-Lookahead execution: Limit buy at P fills on t+1 strictly if Low_{t+1} < P and Open_{t+1} >= P.
3. Limit sell at P fills on t+1 strictly if High_{t+1} > P and Open_{t+1} <= P.
4. Take Profit strictly on SMA(20) / middle Bollinger Band (or bounded +1.0%...+2.5% move).
5. Stop Loss placed strictly beyond range boundary at 1.0 * ATR(14) (Taker fee 0.09% + 0.02% slippage).
6. Conservative within-bar collision: if both SL and TP are touched on same bar, SL triggers.
7. Regime Handoff: if anomalous volume breakout occurs and ADX >= 20, range position closes to hand over to Trend module.
8. 1.0% fractional risk sizing, max 2 concurrent range positions, and 3.5x portfolio leverage cap.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from .config import (
    BASE_MAKER_FEE,
    BASE_TAKER_FEE,
    BASE_SLIPPAGE,
    PORTFOLIO_LEVERAGE,
)
from .regime_classifier import MarketRegime, RegimeData


@dataclass
class RangeLimitOrder:
    symbol: str
    direction: int  # +1 Long, -1 Short
    price: Decimal
    qty: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    created_time: int
    created_bar_idx: int
    atr: Decimal
    tms_score: float = 0.0


@dataclass
class RangeActivePosition:
    symbol: str
    direction: int  # +1 Long, -1 Short
    entry_price: Decimal
    current_qty: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    initial_atr: Decimal
    entry_time: int
    entry_bar_idx: int
    entry_fees: Decimal
    funding_pnl: Decimal = Decimal("0")


@dataclass
class RangeClosedTrade:
    symbol: str
    direction: int
    entry_time: int
    exit_time: int
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    net_pnl: Decimal
    exit_reason: str  # "TP", "SL", "REGIME_HANDOFF", "LEVERAGE_HALT"
    hold_hours: float
    fees_paid: Decimal
    funding_pnl: Decimal


class RangeMeanReversionEngine:
    """
    Autonomous Range Trading Submodule executing Maker Mean-Reversion in Chop.
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("300.0"),
        instrument_rules: Optional[Dict[str, Dict[str, Decimal]]] = None,
        risk_per_trade_pct: Decimal = Decimal("0.01"),  # 1.0% fixed fractional risk
        max_concurrent_positions: int = 2,              # Max 2 concurrent range positions
        max_portfolio_leverage: Decimal = Decimal("3.5"),
        maker_fee: Decimal = BASE_MAKER_FEE,            # 0.0324%
        taker_fee: Decimal = BASE_TAKER_FEE,            # 0.09%
        slippage: Decimal = BASE_SLIPPAGE,              # 0.02%
        funding_rate_8h: Decimal = Decimal("0.0001"),   # 0.01% / 8h
        sl_atr_mult: Decimal = Decimal("1.0"),          # 1.0 * ATR stop beyond boundary
        min_tp_dist_pct: Decimal = Decimal("0.010"),    # Minimum 1.0% target to SMA20
        max_tp_dist_pct: Decimal = Decimal("0.020"),    # Target clamp up to 2.0%
        cooldown_bars: int = 3,                         # 3-bar re-entry cooldown
        anomalous_vol_mult: float = 1.5,
    ):
        self.initial_capital = initial_capital
        self.realized_equity = initial_capital
        self.peak_equity = initial_capital
        self.max_drawdown_pct = Decimal("0")
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_concurrent_positions = max_concurrent_positions
        self.max_portfolio_leverage = max_portfolio_leverage

        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.funding_rate_8h = funding_rate_8h
        self.sl_atr_mult = sl_atr_mult
        self.min_tp_dist_pct = min_tp_dist_pct
        self.max_tp_dist_pct = max_tp_dist_pct
        self.cooldown_bars = cooldown_bars
        self.anomalous_vol_mult = anomalous_vol_mult

        self.instrument_rules = instrument_rules or {}
        self.open_positions: Dict[str, RangeActivePosition] = {}
        self.pending_orders: Dict[str, RangeLimitOrder] = {}
        self.closed_trades: List[RangeClosedTrade] = []
        self.cooldown_tracker: Dict[Tuple[str, int], int] = {}
        self.bar_counter: int = 0
        self.events: List[Dict[str, Any]] = []

    def get_equity(self, current_prices: Dict[str, Decimal]) -> Decimal:
        """Mark-to-market equity including unrealized PnL."""
        unrealized = Decimal("0")
        for sym, pos in self.open_positions.items():
            mark = current_prices.get(sym, pos.entry_price)
            if pos.direction == 1:
                gross = pos.current_qty * (mark - pos.entry_price)
            else:
                gross = pos.current_qty * (pos.entry_price - mark)
            est_fee = pos.current_qty * mark * self.maker_fee
            unrealized += gross - est_fee
        return self.realized_equity + unrealized

    def _observe_drawdown(self, prices: Dict[str, Decimal]) -> Decimal:
        eq = self.get_equity(prices)
        if eq > self.peak_equity:
            self.peak_equity = eq
        if self.peak_equity > 0:
            dd = (self.peak_equity - eq) / self.peak_equity * Decimal("100")
            if dd > self.max_drawdown_pct:
                self.max_drawdown_pct = dd
        return eq

    def _capacity_check(self, symbol: str, notional: Decimal, prices: Dict[str, Decimal]) -> bool:
        """Enforces 3.5x portfolio aggregate leverage cap."""
        eq = self.get_equity(prices)
        if eq <= 0:
            return False
        current_gross = sum(
            p.current_qty * prices.get(s, p.entry_price) for s, p in self.open_positions.items()
        )
        return (current_gross + notional) <= (eq * self.max_portfolio_leverage)

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: Decimal,
        stop_price: Decimal,
        current_equity: Decimal,
    ) -> Optional[Decimal]:
        """Calculates 1.0% risk position size adhering to qty steps."""
        rules = self.instrument_rules.get(
            symbol,
            {
                "minOrderQty": Decimal("0.001"),
                "qtyStep": Decimal("0.001"),
                "minNotional": Decimal("5.0"),
                "maxOrderQty": Decimal("1000000"),
            },
        )
        step = rules.get("qtyStep", Decimal("0.001"))
        min_qty = rules.get("minOrderQty", Decimal("0.001"))
        min_notional = rules.get("minNotional", Decimal("5.0"))
        max_qty = rules.get("maxOrderQty", Decimal("1000000"))

        risk_amount = current_equity * self.risk_per_trade_pct
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return None

        # Unit loss accounting: stop distance + entry maker fee + exit taker fee + slippage
        unit_risk = stop_distance + entry_price * self.maker_fee + stop_price * (self.taker_fee + self.slippage)
        raw_qty = risk_amount / unit_risk

        # Quantize to step
        qty = (raw_qty / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if qty < min_qty or qty > max_qty:
            return None
        if qty * entry_price < min_notional:
            return None
        return qty

    def process_funding(self, timestamp: int, funding_rate: Decimal, prices: Dict[str, Decimal]) -> None:
        """Deducts funding every 8 hours."""
        for sym, pos in self.open_positions.items():
            mark = prices.get(sym, pos.entry_price)
            # Long pays funding when rate > 0; Short receives
            fpnl = -1 * pos.direction * pos.current_qty * mark * funding_rate
            self.realized_equity += fpnl
            pos.funding_pnl += fpnl

    def evaluate_pending_fills(
        self,
        next_bars: Dict[str, Dict[str, Any]],
        next_open_prices: Dict[str, Decimal],
    ) -> List[str]:
        """
        Executes pending Limit Orders on bar t+1 according to strict Zero-Lookahead protocol:
        - Buy limit at P fills iff Low_{t+1} < P and Open_{t+1} >= P
        - Sell limit at P fills iff High_{t+1} > P and Open_{t+1} <= P
        """
        filled_symbols = []
        for sym, order in list(self.pending_orders.items()):
            if sym not in next_bars:
                continue
            b = next_bars[sym]
            next_low = Decimal(str(b["low"]))
            next_high = Decimal(str(b["high"]))
            next_open = next_open_prices.get(sym, Decimal(str(b["open"])))

            filled = False
            if order.direction == 1:
                # Buy limit fill condition: price penetrates below limit
                if next_low < order.price and next_open >= order.price:
                    filled = True
            elif order.direction == -1:
                # Sell limit fill condition: price penetrates above limit
                if next_high > order.price and next_open <= order.price:
                    filled = True

            if filled:
                entry_notional = order.qty * order.price
                # Check portfolio capacity
                current_marks = {s: Decimal(str(next_bars[s]["close"])) for s in next_bars}
                if (
                    len(self.open_positions) < self.max_concurrent_positions
                    and self._capacity_check(sym, entry_notional, current_marks)
                ):
                    entry_fee = entry_notional * self.maker_fee
                    self.realized_equity -= entry_fee
                    self.open_positions[sym] = RangeActivePosition(
                        symbol=sym,
                        direction=order.direction,
                        entry_price=order.price,
                        current_qty=order.qty,
                        stop_loss=order.stop_loss,
                        take_profit=order.take_profit,
                        initial_atr=order.atr,
                        entry_time=order.created_time + 3600000,
                        entry_bar_idx=self.bar_counter,
                        entry_fees=entry_fee,
                    )
                    filled_symbols.append(sym)
                    self.events.append(dict(kind="RANGE_LIMIT_FILL", symbol=sym, dir=order.direction, price=str(order.price), qty=str(order.qty)))
            # Pending orders refresh every bar
            del self.pending_orders[sym]

        return filled_symbols

    def manage_positions_on_bar(
        self,
        timestamp: int,
        bars: Dict[str, Dict[str, Any]],
        regimes: Dict[str, RegimeData],
    ) -> List[RangeClosedTrade]:
        """
        Manages open range positions on closed bar:
        - Evaluates Stop Loss (Taker fee + Slippage)
        - Evaluates Take Profit on SMA(20) / target (Maker fee)
        - Worst-case collision resolution (SL takes precedence)
        - Evaluates Regime Handoff to Trend module
        """
        closed_this_bar = []

        for sym in list(self.open_positions):
            if sym not in bars:
                continue
            pos = self.open_positions[sym]
            # Must not exit on the same exact bar index as entry
            if self.bar_counter <= pos.entry_bar_idx:
                continue

            b = bars[sym]
            bar_open = Decimal(str(b["open"]))
            bar_high = Decimal(str(b["high"]))
            bar_low = Decimal(str(b["low"]))
            bar_close = Decimal(str(b["close"]))

            reg = regimes.get(sym)
            adx_val = reg.adx if reg else float(b.get("adx", 0.0))
            vol_val = reg.volume if reg else float(b.get("volume", 0.0))
            vol_sma = reg.volume_sma if reg else float(b.get("volume_sma", 1.0))

            direction = pos.direction
            sl = pos.stop_loss
            tp = pos.take_profit

            hit_sl = (bar_low <= sl) if direction == 1 else (bar_high >= sl)
            hit_tp = (bar_high > tp) if direction == 1 else (bar_low < tp)

            # Regime handoff check: anomalous breakout and ADX >= 20
            regime_handoff = False
            if adx_val >= 20.0 and vol_val >= (self.anomalous_vol_mult * vol_sma):
                # Breakout moving against position
                if reg:
                    if direction == 1 and bar_close < Decimal(str(reg.bb_lower)):
                        regime_handoff = True
                    elif direction == -1 and bar_close > Decimal(str(reg.bb_upper)):
                        regime_handoff = True

            # Intra-bar worst-case collision: Stop Loss triggers first
            if hit_sl and hit_tp:
                hit_tp = False

            if hit_sl:
                # Execution price at stop or open gap
                fill_p = min(bar_open, sl) if direction == 1 else max(bar_open, sl)
                exec_price = fill_p * (Decimal("1") - self.slippage * direction)
                exit_fee = pos.current_qty * exec_price * self.taker_fee
                gross_pnl = pos.current_qty * (exec_price - pos.entry_price) * direction
                net_pnl = gross_pnl - exit_fee + pos.funding_pnl
                self.realized_equity += (pos.current_qty * (exec_price - pos.entry_price) * direction - exit_fee)

                closed = RangeClosedTrade(
                    symbol=sym,
                    direction=direction,
                    entry_time=pos.entry_time,
                    exit_time=timestamp,
                    entry_price=pos.entry_price,
                    exit_price=exec_price,
                    qty=pos.current_qty,
                    net_pnl=net_pnl,
                    exit_reason="SL",
                    hold_hours=(timestamp - pos.entry_time) / 3600000.0,
                    fees_paid=pos.entry_fees + exit_fee,
                    funding_pnl=pos.funding_pnl,
                )
                self.closed_trades.append(closed)
                closed_this_bar.append(closed)
                self.cooldown_tracker[(sym, direction)] = self.bar_counter + self.cooldown_bars
                del self.open_positions[sym]
                self.events.append(dict(kind="RANGE_CLOSE", symbol=sym, reason="SL", net=str(net_pnl)))

            elif hit_tp:
                # Limit Maker exit at TP
                exec_price = tp
                exit_fee = pos.current_qty * exec_price * self.maker_fee
                gross_pnl = pos.current_qty * (exec_price - pos.entry_price) * direction
                net_pnl = gross_pnl - exit_fee + pos.funding_pnl
                self.realized_equity += (pos.current_qty * (exec_price - pos.entry_price) * direction - exit_fee)

                closed = RangeClosedTrade(
                    symbol=sym,
                    direction=direction,
                    entry_time=pos.entry_time,
                    exit_time=timestamp,
                    entry_price=pos.entry_price,
                    exit_price=exec_price,
                    qty=pos.current_qty,
                    net_pnl=net_pnl,
                    exit_reason="TP",
                    hold_hours=(timestamp - pos.entry_time) / 3600000.0,
                    fees_paid=pos.entry_fees + exit_fee,
                    funding_pnl=pos.funding_pnl,
                )
                self.closed_trades.append(closed)
                closed_this_bar.append(closed)
                self.cooldown_tracker[(sym, direction)] = self.bar_counter + 1
                del self.open_positions[sym]
                self.events.append(dict(kind="RANGE_CLOSE", symbol=sym, reason="TP", net=str(net_pnl)))

            elif regime_handoff:
                # Exit at close with Taker fee and slippage to hand off to Trend
                exec_price = bar_close * (Decimal("1") - self.slippage * direction)
                exit_fee = pos.current_qty * exec_price * self.taker_fee
                gross_pnl = pos.current_qty * (exec_price - pos.entry_price) * direction
                net_pnl = gross_pnl - exit_fee + pos.funding_pnl
                self.realized_equity += (pos.current_qty * (exec_price - pos.entry_price) * direction - exit_fee)

                closed = RangeClosedTrade(
                    symbol=sym,
                    direction=direction,
                    entry_time=pos.entry_time,
                    exit_time=timestamp,
                    entry_price=pos.entry_price,
                    exit_price=exec_price,
                    qty=pos.current_qty,
                    net_pnl=net_pnl,
                    exit_reason="REGIME_HANDOFF",
                    hold_hours=(timestamp - pos.entry_time) / 3600000.0,
                    fees_paid=pos.entry_fees + exit_fee,
                    funding_pnl=pos.funding_pnl,
                )
                self.closed_trades.append(closed)
                closed_this_bar.append(closed)
                self.cooldown_tracker[(sym, direction)] = self.bar_counter + self.cooldown_bars
                del self.open_positions[sym]
                self.events.append(dict(kind="RANGE_CLOSE", symbol=sym, reason="REGIME_HANDOFF", net=str(net_pnl)))

        return closed_this_bar

    def scan_and_generate_orders(
        self,
        timestamp: int,
        bars: Dict[str, Dict[str, Any]],
        regimes: Dict[str, RegimeData],
        btc_bullish: bool = True,
        btc_bearish: bool = True,
        active_trend_symbols: Optional[List[str]] = None,
    ) -> List[RangeLimitOrder]:
        """
        Scans all symbols in RANGE regime and generates limit entry orders.
        Strict conflict avoidance: does not trade symbols active in Trend module.
        """
        active_trend = set(active_trend_symbols or [])
        available_slots = self.max_concurrent_positions - len(self.open_positions)
        if available_slots <= 0:
            return []

        candidates: List[RangeLimitOrder] = []
        current_marks = {s: Decimal(str(b["close"])) for s, b in bars.items()}
        current_equity = self.get_equity(current_marks)

        for sym, b in bars.items():
            # Check mutual exclusion: no overlap with trend positions or already held range positions
            if sym in active_trend or sym in self.open_positions:
                continue

            reg = regimes.get(sym)
            if not reg or not reg.allows_range_trading:
                continue

            # Check bandwidth (range width)
            bb_mid = Decimal(str(reg.bb_mid))
            bb_up = Decimal(str(reg.bb_upper))
            bb_lo = Decimal(str(reg.bb_lower))
            if bb_mid <= 0:
                continue

            bw = (bb_up - bb_lo) / bb_mid
            if bw < Decimal("0.015"):
                continue

            atr = Decimal(str(reg.atr))
            close = Decimal(str(b["close"]))
            rsi = reg.rsi

            # LONG candidate:
            # RSI oversold (< 35), price inside range (close >= bb_lower)
            # Altcoins filter against strong BTC bear trend
            if (
                rsi < 35.0
                and close >= bb_lo
                and (sym == "BTCUSDT" or not btc_bearish)
                and (self.bar_counter >= self.cooldown_tracker.get((sym, 1), 0))
            ):
                limit_p = bb_lo
                sl = limit_p - self.sl_atr_mult * atr
                tp = min(bb_mid, limit_p * (Decimal("1") + self.max_tp_dist_pct))
                if (tp - limit_p) / limit_p >= self.min_tp_dist_pct:
                    qty = self.calculate_position_size(sym, limit_p, sl, current_equity)
                    if qty and self._capacity_check(sym, qty * limit_p, current_marks):
                        tms = (35.0 - rsi) * float(bw)
                        candidates.append(
                            RangeLimitOrder(
                                symbol=sym,
                                direction=1,
                                price=limit_p,
                                qty=qty,
                                stop_loss=sl,
                                take_profit=tp,
                                created_time=timestamp,
                                created_bar_idx=self.bar_counter,
                                atr=atr,
                                tms_score=tms,
                            )
                        )

            # SHORT candidate:
            # RSI overbought (> 65), price inside range (close <= bb_upper)
            if (
                rsi > 65.0
                and close <= bb_up
                and (sym == "BTCUSDT" or not btc_bullish)
                and (self.bar_counter >= self.cooldown_tracker.get((sym, -1), 0))
            ):
                limit_p = bb_up
                sl = limit_p + self.sl_atr_mult * atr
                tp = max(bb_mid, limit_p * (Decimal("1") - self.max_tp_dist_pct))
                if (limit_p - tp) / limit_p >= self.min_tp_dist_pct:
                    qty = self.calculate_position_size(sym, limit_p, sl, current_equity)
                    if qty and self._capacity_check(sym, qty * limit_p, current_marks):
                        tms = (rsi - 65.0) * float(bw)
                        candidates.append(
                            RangeLimitOrder(
                                symbol=sym,
                                direction=-1,
                                price=limit_p,
                                qty=qty,
                                stop_loss=sl,
                                take_profit=tp,
                                created_time=timestamp,
                                created_bar_idx=self.bar_counter,
                                atr=atr,
                                tms_score=tms,
                            )
                        )

        # Rank candidates by opportunity score
        candidates.sort(key=lambda c: c.tms_score, reverse=True)
        selected = candidates[:available_slots]

        for order in selected:
            self.pending_orders[order.symbol] = order

        return selected

    def step_bar(
        self,
        timestamp: int,
        bars: Dict[str, Dict[str, Any]],
        next_bars: Dict[str, Dict[str, Any]],
        next_open_prices: Dict[str, Decimal],
        regimes: Dict[str, RegimeData],
        btc_bullish: bool = True,
        btc_bearish: bool = True,
        active_trend_symbols: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Unified bar execution step for Range Engine:
        1. Fill pending orders on bar t+1 (Zero-lookahead Maker)
        2. Manage open positions on bar t
        3. Scan and register new pending limit orders for next bar
        """
        self.bar_counter += 1
        current_marks = {s: Decimal(str(b["close"])) for s, b in bars.items()}

        # 1. Fill pending orders on incoming bar t+1
        filled = self.evaluate_pending_fills(next_bars, next_open_prices)

        # 2. Manage existing open positions on closed bar t
        closed = self.manage_positions_on_bar(timestamp, bars, regimes)

        # 3. Observe equity & drawdown
        eq = self._observe_drawdown(current_marks)

        # 4. Generate new limit orders for next bar
        orders = self.scan_and_generate_orders(
            timestamp=timestamp,
            bars=bars,
            regimes=regimes,
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
            active_trend_symbols=active_trend_symbols,
        )

        return {
            "filled": filled,
            "closed": closed,
            "equity": eq,
            "pending_orders": len(self.pending_orders),
            "open_positions": len(self.open_positions),
        }
