"""
Event-Driven Chronological Market Simulator v1.0.
Enhanced with:
- Stepped exit system: initial stop → breakeven → partial TP → trailing
- Partial position closing (TP at 50% of qty)
- Loss cooldown enforcement
- Profile-based risk parameters
- All SPEC v1.0 Section 5 compliance
"""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.config import (
    BASE_SLIPPAGE,
    BASE_TAKER_FEE,
    BREAKEVEN_ATR_MULT,
    TP_ATR_MULT,
    TRAILING_ATR_MULT,
    INSTRUMENT_RULES,
    MAX_HOLD_HOURS,
    DEFAULT_RISK_PROFILE,
    RISK_PROFILES,
    StrategyParams,
)
from src.risk_manager import RiskManager, SizingResult
from src.strategy import Signal, StrategyV10

logger = logging.getLogger("Simulator")

@dataclass
class Position:
    symbol: str
    direction: int            # +1 Long, -1 Short
    qty: Decimal              # Current remaining qty (decreases after partial TP)
    initial_qty: Decimal      # Original qty at entry
    entry_price: Decimal
    entry_time: int
    stop_price: Decimal
    highest_price: Decimal
    lowest_price: Decimal
    initial_stop_price: Decimal
    atr_at_entry: Decimal
    k_mult: float
    n_lookback: int
    entry_fee: Decimal
    total_funding_paid: Decimal = Decimal("0")
    pending_new_stop: Optional[Decimal] = None
    # Stepped exit state
    breakeven_triggered: bool = False
    partial_tp_triggered: bool = False
    # Accumulated partial PnL (from partial closes)
    partial_realized_pnl: Decimal = Decimal("0")
    partial_exit_fees: Decimal = Decimal("0")

@dataclass
class TradeRecord:
    trade_id: int
    symbol: str
    direction: str
    entry_time: int
    exit_time: int
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal              # Total qty (initial)
    entry_fee: Decimal
    exit_fee: Decimal
    funding_paid: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    return_pct: Decimal
    exit_reason: str
    hold_hours: float
    equity_after: Decimal
    partial_tp_pnl: Decimal = Decimal("0")  # PnL from partial TP

@dataclass
class SimulationResult:
    initial_capital: Decimal
    final_equity: Decimal
    peak_equity: Decimal
    net_profit: Decimal
    return_pct: Decimal
    max_drawdown_pct: Decimal
    trades: List[TradeRecord] = field(default_factory=list)
    equity_curve: List[Tuple[int, Decimal]] = field(default_factory=list)
    daily_returns: List[Tuple[str, float]] = field(default_factory=list)
    skipped_min_size: int = 0
    skipped_risk_budget: int = 0
    skipped_cooldown: int = 0
    halt_reason: Optional[str] = None
    stop_out_triggered: bool = False
    risk_profile: str = ""

class MarketSimulator:
    """Event-driven simulator v1.0 with stepped exits and partial TP."""

    def __init__(
        self,
        strategy_params: StrategyParams,
        initial_capital: Decimal,
        fee_rate: Decimal = BASE_TAKER_FEE,
        slippage_rate: Decimal = BASE_SLIPPAGE,
        delay_minutes: int = 0,
        monthly_infra_cost: Decimal = Decimal("0.0"),
        risk_profile: str = DEFAULT_RISK_PROFILE,
    ):
        self.strategy_params = strategy_params
        self.initial_capital = Decimal(str(initial_capital))
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.delay_minutes = delay_minutes
        self.monthly_infra_cost = monthly_infra_cost
        self.risk_profile_name = risk_profile
        self.profile = RISK_PROFILES[risk_profile]

        self.risk_manager = RiskManager(
            initial_capital=self.initial_capital,
            profile_name=risk_profile,
        )
        self.strategy = StrategyV10(params=self.strategy_params)

        # State
        self.realized_equity = self.initial_capital
        self.current_equity = self.initial_capital
        self.position: Optional[Position] = None
        self.trades: List[TradeRecord] = []
        self.equity_curve: List[Tuple[int, Decimal]] = []
        self.trade_counter = 0

        # Pending orders
        self.pending_entry: Optional[Tuple[Signal, SizingResult, int]] = None
        self.pending_exit_reason: Optional[str] = None

    def run(
        self,
        df_1m_dict: Dict[str, pd.DataFrame],
        df_1h_prepared_dict: Dict[str, pd.DataFrame],
        funding_dict: Dict[str, pd.DataFrame],
        start_ts: int,
        end_ts: int,
        entry_cutoff_ms: int = 0,
    ) -> SimulationResult:
        """Execute chronological simulation over [start_ts, end_ts)."""

        # Map funding events
        funding_events: Dict[int, Dict[str, Decimal]] = {}
        for sym, fdf in funding_dict.items():
            for _, r in fdf.iterrows():
                ts = int(r["fundingRateTimestamp"])
                if start_ts <= ts <= end_ts:
                    if ts not in funding_events:
                        funding_events[ts] = {}
                    funding_events[ts][sym] = Decimal(str(r["fundingRate"]))

        # Pre-index 1h data by close_time
        h1_by_close_time: Dict[str, Dict[int, pd.Series]] = {}
        for sym, hdf in df_1h_prepared_dict.items():
            h1_by_close_time[sym] = {}
            for _, row in hdf.iterrows():
                h1_by_close_time[sym][int(row["close_time"])] = row

        # Align 1m timestamps
        syms = list(df_1m_dict.keys())
        df0 = df_1m_dict[syms[0]]
        m1_timestamps = df0[
            (df0["timestamp"] >= start_ts) & (df0["timestamp"] < end_ts)
        ]["timestamp"].to_numpy()

        # Pre-index 1m data
        m1_indexed: Dict[str, Dict[int, Tuple[float, float, float, float]]] = {}
        for sym in syms:
            df = df_1m_dict[sym]
            filtered = df[(df["timestamp"] >= start_ts) & (df["timestamp"] < end_ts)]
            ts_arr = filtered["timestamp"].to_numpy(dtype=np.int64)
            o_arr = filtered["open"].to_numpy(dtype=np.float64)
            h_arr = filtered["high"].to_numpy(dtype=np.float64)
            l_arr = filtered["low"].to_numpy(dtype=np.float64)
            c_arr = filtered["close"].to_numpy(dtype=np.float64)
            m1_indexed[sym] = {
                ts_arr[i]: (o_arr[i], h_arr[i], l_arr[i], c_arr[i])
                for i in range(len(ts_arr))
            }

        cutoff_ts = end_ts - entry_cutoff_ms if entry_cutoff_ms > 0 else end_ts
        last_month_checked = -1

        # ========== Chronological minute loop ==========
        for t in m1_timestamps:
            t = int(t)

            # Monthly infra cost
            if self.monthly_infra_cost > 0:
                dt_utc = pd.to_datetime(t, unit="ms", utc=True)
                if dt_utc.month != last_month_checked:
                    if last_month_checked != -1:
                        self.realized_equity -= self.monthly_infra_cost
                        self.current_equity -= self.monthly_infra_cost
                    last_month_checked = dt_utc.month

            # 1. Funding settlement
            if t in funding_events and self.position is not None:
                rate = funding_events[t].get(self.position.symbol, Decimal("0"))
                if rate != Decimal("0"):
                    bar = m1_indexed[self.position.symbol].get(t)
                    if bar:
                        mark_p = Decimal(str(bar[0]))
                        payment = (
                            self.position.qty * mark_p * rate
                            * Decimal(str(-self.position.direction))
                        )
                        self.realized_equity += payment
                        self.position.total_funding_paid -= payment

            # Activate pending trailing stop
            if self.position is not None and self.position.pending_new_stop is not None:
                self.position.stop_price = self.position.pending_new_stop
                self.position.pending_new_stop = None

            # 2. Intra-minute Stop Loss check
            if self.position is not None:
                self._check_stop_loss(t, m1_indexed)

            # 3. Process pending exit
            if self.position is not None and self.pending_exit_reason is not None:
                pos = self.position
                bar = m1_indexed[pos.symbol].get(t)
                if bar is not None:
                    b_open = Decimal(str(bar[0]))
                    if pos.direction == 1:
                        exit_price = b_open * (Decimal("1") - self.slippage_rate)
                    else:
                        exit_price = b_open * (Decimal("1") + self.slippage_rate)
                    self._close_position(exit_price, self.pending_exit_reason, t)
                self.pending_exit_reason = None

            # 4. Process pending entry
            if self.position is None and self.pending_entry is not None:
                sig, sizing, sched_t = self.pending_entry
                if t >= sched_t:
                    bar = m1_indexed[sig.symbol].get(t)
                    if bar is not None and not self.risk_manager.is_permanently_halted:
                        b_open = Decimal(str(bar[0]))
                        if sig.direction == 1:
                            exec_price = b_open * (Decimal("1") + self.slippage_rate)
                        else:
                            exec_price = b_open * (Decimal("1") - self.slippage_rate)

                        # Re-verify sizing
                        curr_f_rate = funding_events.get(t, {}).get(sig.symbol, Decimal("0"))
                        actual_sizing = self.risk_manager.calculate_order_size(
                            symbol=sig.symbol,
                            direction=sig.direction,
                            entry_price=exec_price,
                            atr=Decimal(str(sig.atr)),
                            k_mult=sig.k_mult,
                            current_funding_rate=curr_f_rate,
                            current_ts=t,
                            fee_rate=self.fee_rate,
                            slippage_rate=self.slippage_rate,
                        )

                        if actual_sizing.allowed:
                            entry_fee = actual_sizing.qty * exec_price * self.fee_rate
                            self.realized_equity -= entry_fee
                            self.position = Position(
                                symbol=sig.symbol,
                                direction=sig.direction,
                                qty=actual_sizing.qty,
                                initial_qty=actual_sizing.qty,
                                entry_price=exec_price,
                                entry_time=t,
                                stop_price=actual_sizing.stop_price,
                                highest_price=exec_price,
                                lowest_price=exec_price,
                                initial_stop_price=actual_sizing.stop_price,
                                atr_at_entry=Decimal(str(sig.atr)),
                                k_mult=sig.k_mult,
                                n_lookback=sig.n_lookback,
                                entry_fee=entry_fee,
                            )
                self.pending_entry = None

            # 5. Check 1H Candle Close
            next_t = t + 60000
            if next_t % 3600000 == 0:
                close_time = next_t

                if self.position is not None:
                    self._process_1h_close(close_time, h1_by_close_time, m1_indexed, t)

                elif (
                    close_time < cutoff_ts
                    and not self.risk_manager.is_permanently_halted
                    and not self.risk_manager.is_cooldown_active(close_time)
                ):
                    # Evaluate new signals
                    candidate_signals: List[Signal] = []
                    for sym in syms:
                        h1_row = h1_by_close_time.get(sym, {}).get(close_time)
                        if h1_row is not None:
                            sig = self.strategy.generate_signals_for_bar(h1_row, sym)
                            if sig is not None:
                                candidate_signals.append(sig)

                    if candidate_signals:
                        candidate_signals.sort(
                            key=lambda s: (
                                s.normalized_distance,
                                1 if s.symbol == "BTCUSDT" else 0,
                            ),
                            reverse=True,
                        )

                        for sig in candidate_signals:
                            curr_f_rate = funding_events.get(next_t, {}).get(
                                sig.symbol, Decimal("0")
                            )
                            ref_p = Decimal(str(sig.entry_ref_price))

                            sizing = self.risk_manager.calculate_order_size(
                                symbol=sig.symbol,
                                direction=sig.direction,
                                entry_price=ref_p,
                                atr=Decimal(str(sig.atr)),
                                k_mult=sig.k_mult,
                                current_funding_rate=curr_f_rate,
                                current_ts=next_t,
                                fee_rate=self.fee_rate,
                                slippage_rate=self.slippage_rate,
                            )

                            if sizing.allowed:
                                exec_ts = next_t + (self.delay_minutes * 60000)
                                self.pending_entry = (sig, sizing, exec_ts)
                                break

            # 6. Mark-to-market & Circuit Breakers
            unrealized_pnl = Decimal("0")
            if self.position is not None:
                pos = self.position
                bar = m1_indexed[pos.symbol].get(t)
                if bar is not None:
                    curr_p = Decimal(str(bar[3]))
                    if pos.direction == 1:
                        gross_unreal = pos.qty * (curr_p - pos.entry_price)
                    else:
                        gross_unreal = pos.qty * (pos.entry_price - curr_p)
                    est_exit_fee = pos.qty * curr_p * self.fee_rate
                    unrealized_pnl = gross_unreal - est_exit_fee + pos.partial_realized_pnl

            current_eq = self.realized_equity + unrealized_pnl
            self.current_equity = current_eq
            self.risk_manager.update_calendar_and_equity(t, current_eq)

            # Permanent halt → close position
            if self.risk_manager.is_permanently_halted and self.position is not None:
                bar = m1_indexed[self.position.symbol].get(t)
                if bar is not None:
                    b_open = Decimal(str(bar[0]))
                    if self.position.direction == 1:
                        exit_p = b_open * (Decimal("1") - self.slippage_rate)
                    else:
                        exit_p = b_open * (Decimal("1") + self.slippage_rate)
                    self._close_position(exit_p, "CIRCUIT_BREAKER_HALT", t)

            # Record equity curve every hour
            if next_t % 3600000 == 0:
                self.equity_curve.append((next_t, self.current_equity))

        # End of period: force-close
        if self.position is not None:
            last_t = m1_timestamps[-1]
            bar = m1_indexed[self.position.symbol].get(int(last_t))
            if bar is not None:
                exit_p = Decimal(str(bar[3]))
                self._close_position(exit_p, "PERIOD_END", int(last_t))

        # Build results
        net_profit = self.realized_equity - self.initial_capital
        ret_pct = (
            (net_profit / self.initial_capital) * Decimal("100")
            if self.initial_capital > 0
            else Decimal("0")
        )

        peak = self.initial_capital
        max_dd = Decimal("0")
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (peak - eq) / peak
                if dd > max_dd:
                    max_dd = dd

        return SimulationResult(
            initial_capital=self.initial_capital,
            final_equity=self.realized_equity,
            peak_equity=self.risk_manager.peak_equity,
            net_profit=net_profit,
            return_pct=ret_pct,
            max_drawdown_pct=max_dd * Decimal("100"),
            trades=self.trades,
            equity_curve=self.equity_curve,
            skipped_min_size=self.risk_manager.trades_skipped_min_size,
            skipped_risk_budget=self.risk_manager.trades_skipped_risk_budget,
            skipped_cooldown=self.risk_manager.trades_skipped_cooldown,
            halt_reason=self.risk_manager.halt_reason,
            stop_out_triggered=self.risk_manager.is_permanently_halted,
            risk_profile=self.risk_profile_name,
        )

    def _check_stop_loss(
        self, t: int, m1_indexed: Dict[str, Dict[int, Tuple[float, float, float, float]]]
    ) -> None:
        """Check intra-minute stop loss and stepped exit logic."""
        pos = self.position
        if pos is None:
            return

        bar = m1_indexed[pos.symbol].get(t)
        if bar is None:
            return

        b_open = Decimal(str(bar[0]))
        b_high = Decimal(str(bar[1]))
        b_low = Decimal(str(bar[2]))

        stop_triggered = False
        exit_price = Decimal("0")
        exit_reason = ""

        if pos.direction == 1:
            # Long stop check
            if b_open <= pos.stop_price:
                stop_triggered = True
                exit_price = b_open * (Decimal("1") - self.slippage_rate)
                exit_reason = "STOP_GAP"
            elif b_low <= pos.stop_price:
                stop_triggered = True
                exit_price = pos.stop_price * (Decimal("1") - self.slippage_rate)
                exit_reason = "STOP_LOSS"
            else:
                if b_high > pos.highest_price:
                    pos.highest_price = b_high

                # Stepped exit checks (intra-minute)
                favorable_dist = pos.highest_price - pos.entry_price
                atr = pos.atr_at_entry

                # Breakeven: move stop when price moves 1×ATR in favor
                if (
                    not pos.breakeven_triggered
                    and favorable_dist >= atr * Decimal(str(BREAKEVEN_ATR_MULT))
                ):
                    # Move stop to breakeven + commission buffer
                    be_stop = pos.entry_price + (
                        Decimal("2") * self.fee_rate * pos.entry_price
                        + Decimal("2") * self.slippage_rate * pos.entry_price
                    )
                    if be_stop > pos.stop_price:
                        pos.stop_price = be_stop
                    pos.breakeven_triggered = True

                # Partial TP: close 50% at 2×ATR profit
                if (
                    not pos.partial_tp_triggered
                    and favorable_dist >= atr * Decimal(str(self.profile.tp_atr_mult))
                ):
                    self._execute_partial_tp(t, bar, pos)

        else:
            # Short stop check
            if b_open >= pos.stop_price:
                stop_triggered = True
                exit_price = b_open * (Decimal("1") + self.slippage_rate)
                exit_reason = "STOP_GAP"
            elif b_high >= pos.stop_price:
                stop_triggered = True
                exit_price = pos.stop_price * (Decimal("1") + self.slippage_rate)
                exit_reason = "STOP_LOSS"
            else:
                if b_low < pos.lowest_price:
                    pos.lowest_price = b_low

                # Stepped exit checks
                favorable_dist = pos.entry_price - pos.lowest_price
                atr = pos.atr_at_entry

                if (
                    not pos.breakeven_triggered
                    and favorable_dist >= atr * Decimal(str(BREAKEVEN_ATR_MULT))
                ):
                    be_stop = pos.entry_price - (
                        Decimal("2") * self.fee_rate * pos.entry_price
                        + Decimal("2") * self.slippage_rate * pos.entry_price
                    )
                    if be_stop < pos.stop_price:
                        pos.stop_price = be_stop
                    pos.breakeven_triggered = True

                if (
                    not pos.partial_tp_triggered
                    and favorable_dist >= atr * Decimal(str(self.profile.tp_atr_mult))
                ):
                    self._execute_partial_tp(t, bar, pos)

        if stop_triggered:
            self._close_position(exit_price, exit_reason, t)

    def _execute_partial_tp(
        self,
        t: int,
        bar: Tuple[float, float, float, float],
        pos: Position,
    ) -> None:
        """Execute partial take-profit: close tp_close_pct of position."""
        rules = INSTRUMENT_RULES.get(pos.symbol, {})
        qty_step = rules.get("qtyStep", Decimal("0.001"))
        min_order_qty = rules.get("minOrderQty", Decimal("0.001"))

        # Qty to close
        close_qty_raw = pos.qty * self.profile.tp_close_pct
        close_qty = (close_qty_raw / qty_step).quantize(
            Decimal("1"), rounding=ROUND_FLOOR
        ) * qty_step

        remaining = pos.qty - close_qty
        if remaining < min_order_qty:
            # Can't split — remainder too small. Skip partial TP.
            pos.partial_tp_triggered = True
            return

        if close_qty < min_order_qty:
            pos.partial_tp_triggered = True
            return

        # Execute partial close at current price
        curr_close = Decimal(str(bar[3]))
        if pos.direction == 1:
            exec_price = curr_close * (Decimal("1") - self.slippage_rate)
            partial_gross = close_qty * (exec_price - pos.entry_price)
        else:
            exec_price = curr_close * (Decimal("1") + self.slippage_rate)
            partial_gross = close_qty * (pos.entry_price - exec_price)

        partial_fee = close_qty * exec_price * self.fee_rate
        partial_net = partial_gross - partial_fee

        # Update position
        pos.qty = remaining
        pos.partial_realized_pnl += partial_net
        pos.partial_exit_fees += partial_fee
        pos.partial_tp_triggered = True

        # Credit realized equity
        self.realized_equity += partial_gross - partial_fee

    def _process_1h_close(
        self,
        close_time: int,
        h1_by_close_time: Dict[str, Dict[int, pd.Series]],
        m1_indexed: Dict[str, Dict[int, Tuple[float, float, float, float]]],
        current_t: int,
    ) -> None:
        """Process 1H close: update trailing stop, check exits."""
        pos = self.position
        if pos is None:
            return

        h1_row = h1_by_close_time.get(pos.symbol, {}).get(close_time)
        if h1_row is None or pd.isna(h1_row.get("atr14")):
            return

        c_atr = Decimal(str(h1_row["atr14"]))

        # Trailing stop uses TRAILING_ATR_MULT (1.5) after partial TP, K before
        if pos.partial_tp_triggered:
            trail_k = Decimal(str(TRAILING_ATR_MULT))
        else:
            trail_k = Decimal(str(pos.k_mult))

        k_dist = trail_k * c_atr

        if pos.direction == 1:
            trail_stop = pos.highest_price - k_dist
            if trail_stop > pos.stop_price:
                pos.pending_new_stop = trail_stop
        else:
            trail_stop = pos.lowest_price + k_dist
            if trail_stop < pos.stop_price:
                pos.pending_new_stop = trail_stop

        # Check channel exit / timeout
        h_close = Decimal(str(h1_row["close"]))
        exit_high = (
            Decimal(str(h1_row["exit_channel_high"]))
            if not pd.isna(h1_row.get("exit_channel_high"))
            else None
        )
        exit_low = (
            Decimal(str(h1_row["exit_channel_low"]))
            if not pd.isna(h1_row.get("exit_channel_low"))
            else None
        )

        hold_hours = (close_time - pos.entry_time) / 3600000.0

        if hold_hours >= MAX_HOLD_HOURS:
            self.pending_exit_reason = "TIMEOUT"
        elif pos.direction == 1 and exit_low is not None and h_close < exit_low:
            self.pending_exit_reason = "CHANNEL_EXIT"
        elif pos.direction == -1 and exit_high is not None and h_close > exit_high:
            self.pending_exit_reason = "CHANNEL_EXIT"

    def _close_position(
        self, exit_price: Decimal, exit_reason: str, exit_time: int
    ) -> None:
        """Close remaining position, record trade."""
        pos = self.position
        if pos is None:
            return

        exit_fee = pos.qty * exit_price * self.fee_rate
        if pos.direction == 1:
            gross_pnl = pos.qty * (exit_price - pos.entry_price)
        else:
            gross_pnl = pos.qty * (pos.entry_price - exit_price)

        # Total net PnL includes partial TP PnL
        total_exit_fees = exit_fee + pos.partial_exit_fees
        total_gross = gross_pnl + pos.partial_realized_pnl + pos.partial_exit_fees  # add back fees that were subtracted
        net_pnl = (
            gross_pnl + pos.partial_realized_pnl
            - pos.entry_fee - exit_fee
            + pos.total_funding_paid
        )

        self.realized_equity += gross_pnl - exit_fee
        self.trade_counter += 1

        cost_basis = pos.initial_qty * pos.entry_price
        ret_pct = (
            (net_pnl / cost_basis) * Decimal("100") if cost_basis > 0 else Decimal("0")
        )
        hold_hours = (exit_time - pos.entry_time) / 3600000.0

        trade = TradeRecord(
            trade_id=self.trade_counter,
            symbol=pos.symbol,
            direction="LONG" if pos.direction == 1 else "SHORT",
            entry_time=pos.entry_time,
            exit_time=exit_time,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=pos.initial_qty,
            entry_fee=pos.entry_fee,
            exit_fee=total_exit_fees,
            funding_paid=pos.total_funding_paid,
            gross_pnl=gross_pnl + pos.partial_realized_pnl + pos.partial_exit_fees,
            net_pnl=net_pnl,
            return_pct=ret_pct,
            exit_reason=exit_reason,
            hold_hours=hold_hours,
            equity_after=self.realized_equity,
            partial_tp_pnl=pos.partial_realized_pnl,
        )
        self.trades.append(trade)

        # Record trade result for streak tracking and cooldown
        self.risk_manager.record_trade_result(net_pnl, exit_time)

        self.position = None
