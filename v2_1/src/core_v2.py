"""
Core Engine v2.1: Unified Multi-Alpha Growth System.
Implements complete quantitative audit remediation:
- Mathematically rigorous weighted average entry price on pyramiding
- Basket cash breakeven trailing stop covering all fees and add-ons
- Gross capacity and leverage cap (3.5x) check prior to sizing and add-ons
- Automated trading halt on maximum drawdown limit (18%)
- BTC Delta Trend Filter
- 3-Bar Re-entry Cooldown
- Transactional state persistence with finite number validation
"""

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from .kelly_sizing import KellyRiskManager, KellySizingResult
from .multi_scanner import MultiCoinScanner, CoinCandidate
from .pyramiding_engine import PyramidingEngine, ActivePosition, PyramidingAction


@dataclass
class ClosedTrade:
    symbol: str
    direction: int
    entry_time: int
    exit_time: int
    entry_price: Decimal
    exit_price: Decimal
    initial_qty: Decimal
    final_qty: Decimal
    net_pnl: Decimal
    exit_reason: str
    was_pyramided: bool
    hold_hours: float


@dataclass
class PortfolioSnapshot:
    equity: Decimal
    peak_equity: Decimal
    max_drawdown_pct: Decimal
    open_positions: Dict[str, ActivePosition]
    closed_trades: List[ClosedTrade]
    is_kill_switch_active: bool
    total_net_pnl: Decimal
    win_rate_pct: float
    profit_factor: float


class CoreV2Engine:
    """
    Deep facade managing the complete v2.1 trading lifecycle.
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("300.0"),
        instrument_rules: Optional[Dict[str, Dict[str, Decimal]]] = None,
        base_risk_pct: Decimal = Decimal("0.025"),
        choppy_risk_pct: Decimal = Decimal("0.0075"),
        max_leverage: Decimal = Decimal("3.5"),
        atr_stop_mult: Decimal = Decimal("2.5"),
        taker_fee: Decimal = Decimal("0.0009"),  # 0.09% with MNT discount
        slippage: Decimal = Decimal("0.0002"),   # 0.02%
        portfolio_leverage: Decimal = Decimal("3.5"),
        drawdown_limit_pct: Decimal = Decimal("18.0"),
        partial_tp_ratio: Decimal = Decimal("0"),
        min_adx: float = 0.0,
        cooldown_bars: int = 3,
        enable_btc_delta_filter: bool = True,
    ):
        if (
            not initial_capital.is_finite()
            or initial_capital <= 0
            or not 0 <= partial_tp_ratio <= Decimal("0.4")
            or not 0 < drawdown_limit_pct < 100
        ):
            raise ValueError("Invalid engine configuration")

        self.initial_capital = initial_capital
        self.realized_equity = initial_capital
        self.peak_equity = initial_capital
        self.max_drawdown_pct = Decimal("0")
        self.portfolio_leverage = portfolio_leverage
        self.drawdown_limit_pct = drawdown_limit_pct
        self.partial_tp_ratio = partial_tp_ratio
        self.trading_halted = False
        self.last_bar_timestamp = -1
        self.bar_counter: int = 0
        self.cooldown_bars = cooldown_bars
        self.cooldown_tracker: Dict[str, int] = {}  # "SYMBOL_DIR" -> bar_index
        self.events: List[Dict[str, Any]] = []
        self.funding_receipts: Dict[str, int] = {}

        self.instrument_rules = instrument_rules or {
            "BTCUSDT": {
                "minOrderQty": Decimal("0.001"),
                "qtyStep": Decimal("0.001"),
                "minNotional": Decimal("5.0"),
            },
            "ETHUSDT": {
                "minOrderQty": Decimal("0.01"),
                "qtyStep": Decimal("0.01"),
                "minNotional": Decimal("5.0"),
            },
        }

        self.atr_stop_mult = atr_stop_mult
        self.taker_fee = taker_fee
        self.slippage = slippage

        # Sub-components
        self.risk_manager = KellyRiskManager(
            base_risk_pct=base_risk_pct,
            choppy_risk_pct=choppy_risk_pct,
            max_leverage=max_leverage,
        )
        self.pyramiding_engine = PyramidingEngine(
            pyramid_trigger_atr_mult=Decimal("2.0"),
            pyramid_add_ratio=Decimal("0.5"),
            trailing_atr_mult=atr_stop_mult,
            fee_rate=taker_fee,
            slippage_rate=slippage,
        )
        self.scanner = MultiCoinScanner(
            max_concurrent_positions=2,
            min_adx=min_adx,
            enable_btc_delta_filter=enable_btc_delta_filter,
        )

        self.open_positions: Dict[str, ActivePosition] = {}
        self.closed_trades: List[ClosedTrade] = []
        self.equity_history: List[Tuple[int, Decimal]] = []

    def get_equity(self, current_prices: Dict[str, Decimal]) -> Decimal:
        """Calculates current mark-to-market equity."""
        unrealized = Decimal("0")
        for sym, pos in self.open_positions.items():
            mark = current_prices.get(sym, pos.entry_price)
            if pos.direction == 1:
                gross = pos.current_qty * (mark - pos.entry_price)
            else:
                gross = pos.current_qty * (pos.entry_price - mark)
            est_fee = pos.current_qty * mark * self.taker_fee
            unrealized += gross - est_fee
        return self.realized_equity + unrealized

    def process_funding_event(
        self, timestamp: int, funding_rates: Dict[str, Decimal], current_prices: Dict[str, Decimal]
    ) -> None:
        """Deducts/adds 8-hour funding payments."""
        for sym, pos in self.open_positions.items():
            if timestamp <= self.funding_receipts.get(sym, -1):
                continue
            rate = funding_rates.get(sym, Decimal("0"))
            if rate != Decimal("0"):
                mark = current_prices.get(sym, pos.entry_price)
                funding_pnl = -1 * pos.direction * pos.current_qty * mark * rate
                self.realized_equity += funding_pnl
                pos.funding_pnl += funding_pnl
                self.funding_receipts[sym] = timestamp

    def _observe(self, prices: Dict[str, Decimal]) -> Decimal:
        eq = self.get_equity(prices)
        self.peak_equity = max(self.peak_equity, eq)
        dd = (self.peak_equity - eq) / self.peak_equity * Decimal("100") if self.peak_equity > 0 else Decimal("0")
        self.max_drawdown_pct = max(self.max_drawdown_pct, dd)
        if dd >= self.drawdown_limit_pct:
            self.trading_halted = True
        return eq

    def _capacity(self, symbol: str, price: Decimal, qty: Decimal, prices: Dict[str, Decimal]) -> bool:
        eq = self.get_equity(prices)
        if eq <= 0 or self.trading_halted:
            return False
        gross = sum(
            (p.current_qty * prices.get(s, p.entry_price) for s, p in self.open_positions.items()),
            Decimal("0"),
        )
        existing = self.open_positions.get(symbol)
        own = existing.current_qty * price if existing else Decimal("0")
        fee = qty * price * self.taker_fee
        return (
            own + qty * price <= (eq - fee) * self.risk_manager.state.max_notional_leverage
            and gross + qty * price <= (eq - fee) * self.portfolio_leverage
        )

    def _fill(self, mark: Decimal, direction: int) -> Decimal:
        return mark * (Decimal("1") + self.slippage * direction)

    def _reduce(self, sym: str, qty: Decimal, mark: Decimal, timestamp: int, reason: str) -> None:
        pos = self.open_positions[sym]
        price = self._fill(mark, -pos.direction)
        cash = qty * (price - pos.entry_price) * pos.direction - qty * price * self.taker_fee
        self.realized_equity += cash
        if qty < pos.current_qty:
            pos.current_qty -= qty
            pos.realized_partial += cash
            pos.partial_taken = True
            self.events.append(dict(kind="PARTIAL_TP", symbol=sym, qty=str(qty), price=str(price)))
            return

        net = cash + pos.realized_partial + pos.funding_pnl - pos.entry_fees
        self.risk_manager.record_trade_result(net > Decimal("0"))
        self.closed_trades.append(
            ClosedTrade(
                symbol=sym,
                direction=pos.direction,
                entry_time=pos.entry_time,
                exit_time=timestamp,
                entry_price=pos.original_entry or pos.entry_price,
                exit_price=price,
                initial_qty=pos.initial_qty,
                final_qty=qty,
                net_pnl=net,
                exit_reason=reason,
                was_pyramided=pos.is_pyramided,
                hold_hours=(timestamp - pos.entry_time) / 3600000.0,
            )
        )
        # Register re-entry cooldown
        key = f"{sym}_{pos.direction}"
        self.cooldown_tracker[key] = self.bar_counter + self.cooldown_bars

        del self.open_positions[sym]
        self.events.append(dict(kind="CLOSE", symbol=sym, net_pnl=str(net), reason=reason))

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
        bar_open: Optional[Decimal] = None,
    ) -> None:
        pos = self.open_positions[sym]
        rules = self.instrument_rules.get(sym, {})
        step = rules.get("qtyStep", Decimal("0.001"))
        minimum = rules.get("minOrderQty", Decimal("0.001"))

        proposal = deepcopy(pos)
        action = self.pyramiding_engine.evaluate_bar(proposal, high, low, close, atr, step, minimum)

        if action.action_type == "EXIT":
            fill = execution
            if bar_open is not None:
                fill = min(bar_open, pos.current_stop) if pos.direction == 1 else max(bar_open, pos.current_stop)
            self._reduce(sym, pos.current_qty, fill, timestamp, "STOP_LOSS")
            return

        pos.highest_price, pos.lowest_price = proposal.highest_price, proposal.lowest_price
        pos.current_stop = proposal.current_stop
        pos.is_breakeven_set = proposal.is_breakeven_set
        origin = pos.original_entry or pos.entry_price

        # Partial TP evaluation
        if (
            self.partial_tp_ratio
            and not pos.partial_taken
            and (execution - origin) * pos.direction >= Decimal("3.5") * pos.initial_atr
        ):
            qty = (pos.current_qty * self.partial_tp_ratio / step).to_integral_value(rounding=ROUND_FLOOR) * step
            if qty >= minimum and pos.current_qty - qty >= minimum and qty * execution >= rules.get("minNotional", Decimal("0")):
                self._reduce(sym, qty, execution, timestamp, "PARTIAL_TP")

        # Pyramiding Add evaluation
        if (
            action.action_type == "PYRAMID_ADD"
            and not pos.partial_taken
            and (execution - origin) * pos.direction >= Decimal("2.0") * pos.initial_atr
        ):
            qty = action.added_qty
            price = self._fill(execution, pos.direction)
            if (
                qty * price >= rules.get("minNotional", Decimal("0"))
                and self._capacity(sym, price, qty, prices)
                and qty <= rules.get("maxOrderQty", Decimal("Infinity"))
            ):
                old_qty = pos.current_qty
                # Rigorous weighted average entry price
                pos.entry_price = (pos.entry_price * old_qty + price * qty) / (old_qty + qty)
                pos.current_qty += qty
                fee = qty * price * self.taker_fee
                pos.entry_fees += fee
                self.realized_equity -= fee
                pos.is_pyramided = True

                # True basket cash breakeven covering fees, funding, and exit slippage
                target = (
                    pos.entry_price
                    + pos.direction * (pos.entry_fees - pos.funding_pnl - pos.realized_partial) / pos.current_qty
                )
                be = target / ((Decimal("1") - self.slippage * pos.direction) * (Decimal("1") - self.taker_fee * pos.direction))
                if (execution - be) * pos.direction > 0:
                    pos.current_stop = max(pos.current_stop, be) if pos.direction == 1 else min(pos.current_stop, be)
                pos.is_breakeven_set = (pos.current_stop - be) * pos.direction >= 0
                self.events.append(dict(kind="PYRAMID_ADD", symbol=sym, qty=str(qty), price=str(price)))

    def process_price_event(self, timestamp: int, prices: Dict[str, Decimal]) -> None:
        """Sequential executable quotes. Never replays historical extrema as stop inputs."""
        if any(not p.is_finite() or p <= 0 for p in prices.values()):
            raise ValueError("Invalid price")
        self._observe(prices)
        for sym in list(self.open_positions):
            if sym in prices:
                p = prices[sym]
                pos = self.open_positions[sym]
                self._manage(sym, timestamp, p, p, p, pos.initial_atr, p, prices)
        self._observe(prices)
        if self.trading_halted:
            for sym in list(self.open_positions):
                if sym in prices:
                    self._reduce(sym, self.open_positions[sym].current_qty, prices[sym], timestamp, "DRAWDOWN_HALT")

    def process_bar_event(
        self,
        timestamp: int,
        bars: Dict[str, Dict[str, Any]],
        next_open_prices: Dict[str, Decimal],
        manage_positions: bool = True,
        btc_bullish: bool = True,
        btc_bearish: bool = True,
    ) -> None:
        """Closed bar t -> explicit available open t+1."""
        if timestamp <= self.last_bar_timestamp:
            return
        self.last_bar_timestamp = timestamp
        self.bar_counter += 1

        current = {s: Decimal(str(b["close"])) for s, b in bars.items()}
        self._observe(current)

        if manage_positions:
            for sym in list(self.open_positions):
                if sym not in bars:
                    continue
                b = bars[sym]
                if not all(np.isfinite(b[k]) and b[k] > 0 for k in ("high", "low", "close", "atr")):
                    continue
                opening = Decimal(str(b.get("open", b["close"])))
                if (
                    sym in next_open_prices
                    or (current[sym] - self.open_positions[sym].current_stop) * self.open_positions[sym].direction <= 0
                    or (
                        Decimal(str(b["low"])) <= self.open_positions[sym].current_stop
                        if self.open_positions[sym].direction == 1
                        else Decimal(str(b["high"])) >= self.open_positions[sym].current_stop
                    )
                ):
                    self._manage(
                        sym,
                        timestamp + 3600000,
                        Decimal(str(b["high"])),
                        Decimal(str(b["low"])),
                        current[sym],
                        Decimal(str(b["atr"])),
                        next_open_prices.get(sym, current[sym]),
                        current,
                        opening,
                    )

        self._observe(current)
        if self.trading_halted:
            for sym in list(self.open_positions):
                if sym in next_open_prices:
                    self._reduce(sym, self.open_positions[sym].current_qty, next_open_prices[sym], timestamp + 3600000, "DRAWDOWN_HALT")
            return

        # Scanner breakout evaluation
        candidates = []
        for sym, b in bars.items():
            cand = self.scanner.evaluate_symbol_bar(
                symbol=sym,
                close=float(b["close"]),
                upper_channel=float(b.get("upper", np.nan)),
                lower_channel=float(b.get("lower", np.nan)),
                ema_fast=float(b.get("ema_fast", np.nan)),
                ema_slow=float(b.get("ema_slow", np.nan)),
                atr=float(b.get("atr", np.nan)),
                volume=float(b.get("volume", np.nan)),
                volume_sma=float(b.get("volume_sma", np.nan)),
                adx=float(b.get("adx", np.nan)),
            )
            if cand:
                candidates.append(cand)

        # Convert string keys cooldown to tuple keys
        cooldown_tuples = {}
        for k, exp_idx in self.cooldown_tracker.items():
            parts = k.split("_")
            cooldown_tuples[(parts[0], int(parts[1]))] = exp_idx

        ranked = self.scanner.rank_candidates(
            candidates=candidates,
            currently_held_symbols=list(self.open_positions),
            current_long_count=sum(p.direction == 1 for p in self.open_positions.values()),
            current_short_count=sum(p.direction == -1 for p in self.open_positions.values()),
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
            cooldown_tracker=cooldown_tuples,
            current_bar_idx=self.bar_counter,
        )

        for cand in ranked:
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
            sizing = self.risk_manager.calculate_position_size(
                equity=self.get_equity(current),
                entry_price=price,
                stop_price=price - distance if price > distance else price + distance,
                min_order_qty=rules["minOrderQty"],
                qty_step=rules["qtyStep"],
                min_notional=rules.get("minNotional", Decimal("5.0")),
            )
            if (
                sizing.allowed
                and sizing.qty <= rules.get("maxOrderQty", Decimal("Infinity"))
                and self._capacity(cand.symbol, price, sizing.qty, current)
            ):
                fee = sizing.qty * price * self.taker_fee
                self.realized_equity -= fee
                self.open_positions[cand.symbol] = ActivePosition(
                    symbol=cand.symbol,
                    direction=cand.direction,
                    initial_qty=sizing.qty,
                    current_qty=sizing.qty,
                    entry_price=price,
                    current_stop=stop,
                    initial_atr=cand.atr,
                    highest_price=price,
                    lowest_price=price,
                    entry_time=timestamp + 3600000,
                    original_entry=price,
                    entry_fees=fee,
                )
                self.events.append(dict(kind="OPEN", symbol=cand.symbol, qty=str(sizing.qty), price=str(price)))
                break

        eq = self._observe({**current, **next_open_prices})
        self.equity_history.append((timestamp + 3600000, eq))
        self.equity_history = self.equity_history[-10000:]

    def get_portfolio_snapshot(self, current_prices: Dict[str, Decimal]) -> PortfolioSnapshot:
        """Returns complete portfolio metrics and state."""
        eq = self.get_equity(current_prices)
        wins = [t.net_pnl for t in self.closed_trades if t.net_pnl > Decimal("0")]
        losses = [abs(t.net_pnl) for t in self.closed_trades if t.net_pnl <= Decimal("0")]
        tot_wins = sum(wins, Decimal("0"))
        tot_losses = sum(losses, Decimal("0"))

        wr = (len(wins) / len(self.closed_trades) * 100.0) if self.closed_trades else 0.0
        pf = (
            float(tot_wins / tot_losses)
            if tot_losses > Decimal("0")
            else (999.0 if tot_wins > Decimal("0") else 0.0)
        )

        return PortfolioSnapshot(
            equity=eq,
            peak_equity=self.peak_equity,
            max_drawdown_pct=self.max_drawdown_pct,
            open_positions=self.open_positions.copy(),
            closed_trades=self.closed_trades.copy(),
            is_kill_switch_active=self.risk_manager.state.is_kill_switch_active,
            total_net_pnl=eq - self.initial_capital,
            win_rate_pct=wr,
            profit_factor=pf,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes current engine state for atomic persistence."""
        return {
            "version": "2.1",
            "schema_version": 3,
            "trading_halted": self.trading_halted,
            "last_bar_timestamp": self.last_bar_timestamp,
            "bar_counter": self.bar_counter,
            "cooldown_tracker": self.cooldown_tracker,
            "funding_receipts": self.funding_receipts,
            "initial_capital": str(self.initial_capital),
            "realized_equity": str(self.realized_equity),
            "peak_equity": str(self.peak_equity),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "open_positions": {
                sym: {
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "initial_qty": str(p.initial_qty),
                    "current_qty": str(p.current_qty),
                    "entry_price": str(p.entry_price),
                    "current_stop": str(p.current_stop),
                    "initial_atr": str(p.initial_atr),
                    "highest_price": str(p.highest_price),
                    "lowest_price": str(p.lowest_price),
                    "entry_time": p.entry_time,
                    "is_pyramided": p.is_pyramided,
                    "is_breakeven_set": p.is_breakeven_set,
                    "original_entry": str(p.original_entry),
                    "entry_fees": str(p.entry_fees),
                    "realized_partial": str(p.realized_partial),
                    "funding_pnl": str(p.funding_pnl),
                    "partial_taken": p.partial_taken,
                }
                for sym, p in self.open_positions.items()
            },
            "closed_trades": [
                {
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "entry_price": str(t.entry_price),
                    "exit_price": str(t.exit_price),
                    "initial_qty": str(t.initial_qty),
                    "final_qty": str(t.final_qty),
                    "net_pnl": str(t.net_pnl),
                    "exit_reason": t.exit_reason,
                    "was_pyramided": t.was_pyramided,
                    "hold_hours": t.hold_hours,
                }
                for t in self.closed_trades
            ],
            "risk_manager": {
                "recent_trade_results": getattr(self.risk_manager.state, "recent_trade_results", []),
                "consecutive_losses": getattr(self.risk_manager.state, "consecutive_losses", 0),
                "consecutive_wins": getattr(self.risk_manager.state, "consecutive_wins", 0),
                "is_kill_switch_active": self.risk_manager.state.is_kill_switch_active,
            },
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """Transactional state restoration with strict finite validation."""
        candidate = deepcopy(self)
        candidate._load_state_unchecked(data)
        for value in (
            candidate.initial_capital,
            candidate.realized_equity,
            candidate.peak_equity,
            candidate.max_drawdown_pct,
        ):
            if not value.is_finite():
                raise ValueError("Non-finite snapshot")
        if candidate.initial_capital <= 0 or candidate.peak_equity <= 0:
            raise ValueError("Invalid account snapshot")
        for sym, p in candidate.open_positions.items():
            if p.symbol != sym or p.direction not in (-1, 1):
                raise ValueError("Invalid position identity")
            if not all(
                v.is_finite() and v > 0
                for v in (p.initial_qty, p.current_qty, p.entry_price, p.current_stop, p.initial_atr)
            ):
                raise ValueError("Invalid saved position")
            if not all(
                v.is_finite()
                for v in (p.original_entry, p.entry_fees, p.realized_partial, p.funding_pnl)
            ):
                raise ValueError("Invalid position accounting")
        if candidate.max_drawdown_pct >= candidate.drawdown_limit_pct:
            candidate.trading_halted = True
        self.__dict__.update(candidate.__dict__)

    def _load_state_unchecked(self, data: Dict[str, Any]) -> None:
        self.funding_receipts = {s: int(t) for s, t in data.get("funding_receipts", {}).items()}
        self.initial_capital = Decimal(str(data.get("initial_capital", self.initial_capital)))
        self.trading_halted = bool(data.get("trading_halted", False))
        self.last_bar_timestamp = int(data.get("last_bar_timestamp", -1))
        self.bar_counter = int(data.get("bar_counter", 0))
        self.cooldown_tracker = dict(data.get("cooldown_tracker", {}))
        if "realized_equity" in data:
            self.realized_equity = Decimal(str(data["realized_equity"]))
        if "peak_equity" in data:
            self.peak_equity = Decimal(str(data["peak_equity"]))
        if "max_drawdown_pct" in data:
            self.max_drawdown_pct = Decimal(str(data["max_drawdown_pct"]))

        if "open_positions" in data:
            self.open_positions = {}
            for sym, p in data["open_positions"].items():
                self.open_positions[sym] = ActivePosition(
                    symbol=p["symbol"],
                    direction=int(p["direction"]),
                    initial_qty=Decimal(str(p["initial_qty"])),
                    current_qty=Decimal(str(p["current_qty"])),
                    entry_price=Decimal(str(p["entry_price"])),
                    current_stop=Decimal(str(p["current_stop"])),
                    initial_atr=Decimal(str(p.get("initial_atr", "0"))),
                    highest_price=Decimal(str(p.get("highest_price", p["entry_price"]))),
                    lowest_price=Decimal(str(p.get("lowest_price", p["entry_price"]))),
                    entry_time=int(p.get("entry_time", 0)),
                    is_pyramided=bool(p.get("is_pyramided", False)),
                    is_breakeven_set=bool(p.get("is_breakeven_set", False)),
                    original_entry=Decimal(str(p.get("original_entry", p["entry_price"]))),
                    entry_fees=Decimal(str(p.get("entry_fees", "0"))),
                    realized_partial=Decimal(str(p.get("realized_partial", "0"))),
                    funding_pnl=Decimal(str(p.get("funding_pnl", "0"))),
                    partial_taken=bool(p.get("partial_taken", False)),
                )

        if "closed_trades" in data:
            self.closed_trades = [
                ClosedTrade(
                    symbol=t["symbol"],
                    direction=int(t["direction"]),
                    entry_time=int(t["entry_time"]),
                    exit_time=int(t["exit_time"]),
                    entry_price=Decimal(str(t["entry_price"])),
                    exit_price=Decimal(str(t["exit_price"])),
                    initial_qty=Decimal(str(t["initial_qty"])),
                    final_qty=Decimal(str(t["final_qty"])),
                    net_pnl=Decimal(str(t["net_pnl"])),
                    exit_reason=t.get("exit_reason", ""),
                    was_pyramided=bool(t.get("was_pyramided", False)),
                    hold_hours=float(t.get("hold_hours", 0.0)),
                )
                for t in data["closed_trades"]
            ]

        if "risk_manager" in data:
            rm = data["risk_manager"]
            self.risk_manager.state.recent_trade_results = list(rm.get("recent_trade_results", []))
            self.risk_manager.state.is_kill_switch_active = bool(rm.get("is_kill_switch_active", False))
            self.risk_manager.state.consecutive_losses = int(rm.get("consecutive_losses", 0))
            self.risk_manager.state.consecutive_wins = int(rm.get("consecutive_wins", 0))
