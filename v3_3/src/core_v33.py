"""
Core Quantitative Execution Engine v3.3 Turbo.
Extends CoreV3Engine with Streak-Based Adaptive Risk & Dynamic R:R:
- Dynamically assigns Cascade TP targets (1:3 -> 1:4 -> 1:5) at order entry.
- Scales position size (1.5% -> 2.2% -> 3.2%) and pyramiding (+0% -> +50% -> +75%) based on winning streaks.
- Smoothly steps down gears on losses and preserves streaks on scratch/breakeven trades.
- Strictly adheres to trade accounting invariants and zero-lookahead Point-in-Time execution.
"""

from decimal import Decimal, ROUND_FLOOR
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from v3.src.core_v3 import CoreV3Engine, ClosedTrade, ActivePosition
from v3.src.config import BAR_INTERVAL_MS
from v3_3.src.streak_risk_manager import StreakRiskManager, GearConfig, GEAR_CONFIGS
from v3_3.src.config import (
    INITIAL_CAPITAL,
    BASE_RISK_PCT,
    MAX_LEVERAGE_CAP,
    PORTFOLIO_LEVERAGE,
    DRAWDOWN_LIMIT_PCT,
    CIRCUIT_BREAKER_DD_PCT,
    ATR_STOP_MULT,
    ENABLE_CASCADE_TP,
    MIN_ADX_TREND,
    REENTRY_COOLDOWN_BARS,
    ENABLE_BTC_DELTA_FILTER,
    ENABLE_FAST_SCRATCH_EXIT,
    FAST_SCRATCH_BARS,
    FAST_SCRATCH_MFE_R,
    BASE_TAKER_FEE,
    BASE_SLIPPAGE,
)

logger = logging.getLogger("CoreV33Engine")


class CoreV33Engine(CoreV3Engine):
    """
    Quantitative trading engine integrating streak-based adaptive risk into the Donchian breakout strategy.
    """

    def __init__(
        self,
        initial_capital: Decimal = INITIAL_CAPITAL,
        instrument_rules: Optional[Dict[str, Dict[str, Decimal]]] = None,
        max_leverage: Decimal = MAX_LEVERAGE_CAP,
        atr_stop_mult: Decimal = ATR_STOP_MULT,
        taker_fee: Decimal = BASE_TAKER_FEE,
        slippage: Decimal = BASE_SLIPPAGE,
        portfolio_leverage: Decimal = PORTFOLIO_LEVERAGE,
        drawdown_limit_pct: Decimal = DRAWDOWN_LIMIT_PCT,
        cooldown_bars: int = REENTRY_COOLDOWN_BARS,
        min_adx: float = MIN_ADX_TREND,
        enable_btc_delta_filter: bool = ENABLE_BTC_DELTA_FILTER,
        enable_fast_scratch: bool = ENABLE_FAST_SCRATCH_EXIT,
        fast_scratch_bars: int = FAST_SCRATCH_BARS,
        fast_scratch_mfe_r: Decimal = FAST_SCRATCH_MFE_R,
        enable_cascade_tp: bool = ENABLE_CASCADE_TP,
        bar_interval_ms: int = BAR_INTERVAL_MS,
    ):
        super().__init__(
            initial_capital=initial_capital,
            instrument_rules=instrument_rules,
            base_risk_pct=BASE_RISK_PCT,
            max_leverage=max_leverage,
            atr_stop_mult=atr_stop_mult,
            taker_fee=taker_fee,
            slippage=slippage,
            portfolio_leverage=portfolio_leverage,
            drawdown_limit_pct=drawdown_limit_pct,
            cooldown_bars=cooldown_bars,
            min_adx=min_adx,
            enable_btc_delta_filter=enable_btc_delta_filter,
            enable_auto_gearbox=False,  # Replaced by StreakRiskManager
            enable_fast_scratch=enable_fast_scratch,
            fast_scratch_bars=fast_scratch_bars,
            fast_scratch_mfe_r=fast_scratch_mfe_r,
            enable_cascade_tp=enable_cascade_tp,
            bar_interval_ms=bar_interval_ms,
        )

        self.streak_manager = StreakRiskManager(
            initial_capital=initial_capital,
            initial_gear=1,
            max_leverage_cap=max_leverage,
            circuit_breaker_dd_pct=CIRCUIT_BREAKER_DD_PCT,
        )

    def _rank_and_enter(
        self,
        timestamp: int,
        candidates: Dict[str, Any],
        current: Dict[str, Decimal],
        next_open_prices: Dict[str, Decimal],
        blocked_symbols: Optional[Set[str]] = None,
        btc_bullish: bool = True,
        btc_bearish: bool = False,
    ) -> None:
        if self.trading_halted:
            return

        cooldown_tuples = [
            (c.symbol, c.exit_time, getattr(c, "exit_bar_idx", 0))
            for c in self.closed_trades
        ]
        ranked = self.scanner.rank_and_filter(
            candidates,
            current,
            set(self.open_positions.keys()),
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
            cooldown_tracker=cooldown_tuples,
            current_bar_idx=self.bar_counter,
        )

        current_dd = self.max_drawdown_pct
        active_gear = self.streak_manager.get_effective_gear(current_dd)
        gear_cfg = GEAR_CONFIGS.get(active_gear, GEAR_CONFIGS[1])

        for cand in ranked:
            if blocked_symbols and cand.symbol in blocked_symbols:
                continue
            rules = self.instrument_rules.get(cand.symbol)
            if not rules or cand.symbol not in next_open_prices:
                continue
            mark = next_open_prices[cand.symbol]
            if not mark.is_finite() or mark <= 0:
                continue

            price = self._fill(mark, cand.direction)
            stop = price - cand.direction * cand.atr * self.atr_stop_mult
            if stop <= 0:
                continue

            distance = (
                abs(price - self._fill(stop, -cand.direction))
                + price * self.taker_fee
                + self._fill(stop, -cand.direction) * self.taker_fee
            )

            # Compute sizing via StreakRiskManager
            sizing = self.streak_manager.calculate_position_size(
                equity=self.get_equity(current),
                entry_price=price,
                stop_price=price - distance if price > distance else price + distance,
                min_order_qty=rules["minOrderQty"],
                qty_step=rules["qtyStep"],
                min_notional=rules.get("minNotional", Decimal("5.0")),
                current_dd=current_dd,
            )

            if (
                sizing.allowed
                and sizing.qty <= rules.get("maxOrderQty", Decimal("Infinity"))
                and self._capacity(cand.symbol, price, sizing.qty, current)
            ):
                fee = sizing.qty * price * self.taker_fee
                self.realized_equity -= fee
                ch_level = Decimal(str(cand.breakout_boundary)) if hasattr(cand, "breakout_boundary") and cand.breakout_boundary else price

                pos = ActivePosition(
                    symbol=cand.symbol,
                    direction=cand.direction,
                    initial_qty=sizing.qty,
                    current_qty=sizing.qty,
                    entry_price=price,
                    current_stop=stop,
                    initial_atr=cand.atr,
                    highest_price=price,
                    lowest_price=price,
                    entry_time=timestamp + self.bar_interval_ms,
                    original_entry=price,
                    entry_fees=fee,
                    bars_held=0,
                    breakout_level=ch_level,
                )

                # Attach dynamic R:R cascade parameters to position
                pos.tp1_r_mult = gear_cfg.tp1_r_mult
                pos.tp2_r_mult = gear_cfg.tp2_r_mult
                pos.tp1_ratio = gear_cfg.tp1_ratio
                pos.tp2_ratio = gear_cfg.tp2_ratio
                pos.entry_gear = gear_cfg.gear

                self.open_positions[cand.symbol] = pos
                self.events.append(dict(kind="OPEN", symbol=cand.symbol, qty=str(sizing.qty), price=str(price), gear=gear_cfg.gear))
                break

        eq = self._observe({**current, **next_open_prices})
        self.equity_history.append((timestamp + self.bar_interval_ms, eq))
        self.equity_history = self.equity_history[-10000:]

    def _manage(
        self,
        sym: str,
        timestamp: int,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        atr: Decimal,
        execution: Decimal,
        prices: Dict[str, Decimal],
    ) -> None:
        pos = self.open_positions.get(sym)
        if not pos:
            return

        # Synchronize pyramiding ratio with active gear
        active_gear = self.streak_manager.get_effective_gear(self.max_drawdown_pct)
        gear_cfg = GEAR_CONFIGS.get(active_gear, GEAR_CONFIGS[1])
        self.pyramiding_engine.pyramid_add_ratio = gear_cfg.pyramid_ratio

        # Pyramiding check
        if gear_cfg.pyramiding_allowed:
            rules = self.instrument_rules.get(sym, {})
            action = self.pyramiding_engine.evaluate(
                pos=pos,
                high=high,
                low=low,
                close=close,
                atr=atr,
                min_order_qty=rules.get("minOrderQty", Decimal("0.001")),
                qty_step=rules.get("qtyStep", Decimal("0.001")),
                min_notional=rules.get("minNotional", Decimal("5.0")),
            )
            if action.action_type == "PYRAMID_ADD" and action.added_qty > Decimal("0"):
                cost = action.added_qty * execution
                current_equity = self.get_equity(prices)
                if cost / current_equity <= self.max_leverage and self._capacity(sym, execution, action.added_qty, prices):
                    fee = cost * self.taker_fee
                    self.realized_equity -= fee
                    self.pyramiding_engine.apply_pyramid(pos, action.added_qty, execution, fee, action.new_stop)
                    self.events.append(dict(kind="PYRAMID", symbol=sym, qty=str(action.added_qty), price=str(execution)))
            elif action.action_type == "STOP_UPDATE":
                pos.current_stop = action.new_stop

        # Stop-loss execution
        is_stopped = (execution <= pos.current_stop) if pos.direction == 1 else (execution >= pos.current_stop)
        if is_stopped:
            fill = self._fill(pos.current_stop, -pos.direction)
            self._reduce(sym, pos.current_qty, fill, timestamp, "STOP_LOSS")
            return

        # Fast Scratch defense
        if self.enable_fast_scratch and pos.bars_held <= self.fast_scratch_bars and not pos.is_pyramided:
            origin = pos.original_entry if pos.original_entry > Decimal("0") else pos.entry_price
            adverse_dist = (origin - execution) * pos.direction
            mfe_dist = (pos.highest_price - origin) if pos.direction == 1 else (origin - pos.lowest_price)
            if adverse_dist >= Decimal("0.5") * pos.initial_atr and mfe_dist < self.fast_scratch_mfe_r * pos.initial_atr:
                fill = execution
                self._reduce(sym, pos.current_qty, fill, timestamp, "FAST_SCRATCH")
                return

        # Dynamic Cascade Take-Profit evaluation
        if self.enable_cascade_tp and not pos.is_pyramided:
            origin = pos.original_entry if pos.original_entry > Decimal("0") else pos.entry_price
            profit_dist = (execution - origin) * pos.direction
            rules = self.instrument_rules.get(sym, {})
            step = rules.get("qtyStep", Decimal("0.001"))
            minimum = rules.get("minOrderQty", Decimal("0.001"))

            tp1_target = getattr(pos, "tp1_r_mult", gear_cfg.tp1_r_mult)
            tp2_target = getattr(pos, "tp2_r_mult", gear_cfg.tp2_r_mult)
            tp1_ratio = getattr(pos, "tp1_ratio", gear_cfg.tp1_ratio)
            tp2_ratio = getattr(pos, "tp2_ratio", gear_cfg.tp2_ratio)

            # TP1
            if not pos.tp1_taken and profit_dist >= tp1_target * pos.initial_atr:
                qty_tp1 = (pos.initial_qty * tp1_ratio / step).to_integral_value(rounding=ROUND_FLOOR) * step
                if qty_tp1 >= minimum and pos.current_qty - qty_tp1 >= minimum and qty_tp1 * execution >= rules.get("minNotional", Decimal("0")):
                    pos.tp1_taken = True
                    self._reduce(sym, qty_tp1, execution, timestamp, "CASCADE_TP1")
                    # Tighten stop to Breakeven
                    target = (
                        pos.entry_price
                        + pos.direction * (pos.entry_fees - pos.funding_pnl - pos.realized_partial) / pos.current_qty
                    )
                    be = target / ((Decimal("1") - self.slippage * pos.direction) * (Decimal("1") - self.taker_fee * pos.direction))
                    if pos.direction == 1:
                        pos.current_stop = max(pos.current_stop, be, pos.entry_price)
                    else:
                        pos.current_stop = min(pos.current_stop, be, pos.entry_price)
                    pos.is_breakeven_set = True

            # TP2
            elif pos.tp1_taken and not pos.tp2_taken and profit_dist >= tp2_target * pos.initial_atr:
                qty_tp2 = (pos.initial_qty * tp2_ratio / step).to_integral_value(rounding=ROUND_FLOOR) * step
                qty_tp2 = min(qty_tp2, pos.current_qty - minimum)
                if qty_tp2 >= minimum and pos.current_qty - qty_tp2 >= minimum and qty_tp2 * execution >= rules.get("minNotional", Decimal("0")):
                    pos.tp2_taken = True
                    self._reduce(sym, qty_tp2, execution, timestamp, "CASCADE_TP2")
                    secured_stop = origin + pos.direction * Decimal("1.5") * pos.initial_atr
                    if pos.direction == 1:
                        pos.current_stop = max(pos.current_stop, secured_stop)
                    else:
                        pos.current_stop = min(pos.current_stop, secured_stop)

    def _reduce(self, sym: str, qty: Decimal, price: Decimal, timestamp: int, reason: str) -> None:
        pos = self.open_positions.get(sym)
        if not pos:
            return

        fill = self._fill(price, -pos.direction)
        exit_fee = qty * fill * self.taker_fee
        raw_pnl = qty * (fill - pos.entry_price) * pos.direction
        portion = qty / pos.current_qty if pos.current_qty > Decimal("0") else Decimal("1")
        allocated_entry_fee = pos.entry_fees * portion
        allocated_funding = pos.funding_pnl * portion
        net_trade_pnl = raw_pnl - exit_fee - allocated_entry_fee + allocated_funding

        self.realized_equity += (raw_pnl - exit_fee)
        pos.realized_partial += net_trade_pnl
        pos.entry_fees -= allocated_entry_fee
        pos.funding_pnl -= allocated_funding
        pos.current_qty -= qty

        if pos.current_qty <= Decimal("0"):
            self.open_positions.pop(sym, None)
            total_net_trade = pos.realized_partial
            hold_hrs = (timestamp - pos.entry_time) / (3600 * 1000)
            closed_trade = ClosedTrade(
                symbol=sym,
                direction=pos.direction,
                entry_time=pos.entry_time,
                exit_time=timestamp,
                entry_price=pos.original_entry if pos.original_entry > Decimal("0") else pos.entry_price,
                exit_price=fill,
                initial_qty=pos.initial_qty,
                final_qty=qty,
                net_pnl=total_net_trade,
                exit_reason=reason,
                was_pyramided=pos.is_pyramided,
                hold_hours=round(hold_hrs, 2),
            )
            closed_trade.exit_bar_idx = self.bar_counter
            self.closed_trades.append(closed_trade)

            # Update streak risk manager with trade outcome
            initial_risk_usd = pos.initial_qty * (pos.initial_atr * self.atr_stop_mult)
            self.streak_manager.record_closed_trade(
                net_pnl=total_net_trade,
                initial_risk_usd=initial_risk_usd,
                current_dd=self.max_drawdown_pct,
            )

        self.events.append(dict(kind="REDUCE", symbol=sym, qty=str(qty), price=str(fill), reason=reason))

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["streak_manager"] = self.streak_manager.to_dict()
        for sym, p in self.open_positions.items():
            if sym in data.get("open_positions", {}):
                data["open_positions"][sym]["tp1_r_mult"] = str(getattr(p, "tp1_r_mult", "3.0"))
                data["open_positions"][sym]["tp2_r_mult"] = str(getattr(p, "tp2_r_mult", "4.0"))
                data["open_positions"][sym]["tp1_ratio"] = str(getattr(p, "tp1_ratio", "0.35"))
                data["open_positions"][sym]["tp2_ratio"] = str(getattr(p, "tp2_ratio", "0.35"))
                data["open_positions"][sym]["entry_gear"] = getattr(p, "entry_gear", 1)
        return data

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        super().load_from_dict(data)
        if "streak_manager" in data:
            self.streak_manager.from_dict(data["streak_manager"])
        for sym, p_dict in data.get("open_positions", {}).items():
            if sym in self.open_positions:
                p = self.open_positions[sym]
                p.tp1_r_mult = Decimal(str(p_dict.get("tp1_r_mult", "3.0")))
                p.tp2_r_mult = Decimal(str(p_dict.get("tp2_r_mult", "4.0")))
                p.tp1_ratio = Decimal(str(p_dict.get("tp1_ratio", "0.35")))
                p.tp2_ratio = Decimal(str(p_dict.get("tp2_ratio", "0.35")))
                p.entry_gear = int(p_dict.get("entry_gear", 1))
