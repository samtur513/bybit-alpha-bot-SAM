"""
Multi-Alpha Growth Engine Core Facade v2.1.
Integrates Bybit MNT fee (0.09%), BTC Delta Filter, and Re-entry Cooldown.
Full serialization with atomic state persistence.
"""

from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Dict, List, Optional, Tuple, Any

from .kelly_sizing import KellyRiskManager
from .multi_scanner import MultiCoinScanner, CoinCandidate
from .pyramiding_engine import PyramidingEngine, ActivePosition

logger = logging.getLogger("CoreV2Engine")


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
    realized_equity: Decimal
    peak_equity: Decimal
    max_drawdown_pct: Decimal
    is_kill_switch_active: bool
    open_positions: Dict[str, ActivePosition]
    closed_trades: List[ClosedTrade]
    total_net_pnl: Decimal
    win_rate_pct: float
    profit_factor: float


class CoreV2Engine:
    def __init__(
        self,
        initial_capital: Decimal = Decimal("300.0"),
        instrument_rules: Optional[Dict[str, Dict[str, Decimal]]] = None,
        base_risk_pct: Decimal = Decimal("0.025"),
        choppy_risk_pct: Decimal = Decimal("0.0075"),
        max_leverage: Decimal = Decimal("3.5"),
        atr_stop_mult: Decimal = Decimal("2.5"),
        taker_fee: Decimal = Decimal("0.0009"),      # 0.09% with MNT discount
        slippage: Decimal = Decimal("0.0002"),       # 0.02%
        cooldown_bars: int = 3,
        enable_btc_delta_filter: bool = True,
    ):
        self.initial_capital = initial_capital
        self.realized_equity = initial_capital
        self.peak_equity = initial_capital
        self.max_drawdown_pct = Decimal("0")

        self.instrument_rules = instrument_rules or {}
        self.atr_stop_mult = atr_stop_mult
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.cooldown_bars = cooldown_bars

        self.risk_manager = KellyRiskManager(
            base_risk_pct=base_risk_pct,
            choppy_risk_pct=choppy_risk_pct,
            max_leverage=max_leverage,
        )
        self.scanner = MultiCoinScanner(
            max_long_positions=2,
            max_short_positions=2,
            enable_btc_delta_filter=enable_btc_delta_filter,
        )
        self.pyramiding_engine = PyramidingEngine(
            fee_rate=taker_fee,
            slippage_rate=slippage,
        )

        self.open_positions: Dict[str, ActivePosition] = {}
        self.closed_trades: List[ClosedTrade] = []

        # v2.1 Cooldown state: (symbol, direction) -> available_at_bar_idx
        self.bar_counter: int = 0
        self.cooldown_tracker: Dict[str, int] = {}

    def get_portfolio_snapshot(self, current_prices: Dict[str, Decimal]) -> PortfolioSnapshot:
        unrealized = Decimal("0")
        for sym, pos in self.open_positions.items():
            mark = current_prices.get(sym, pos.entry_price)
            gross = (
                pos.current_qty * (mark - pos.entry_price)
                if pos.direction == 1
                else pos.current_qty * (pos.entry_price - mark)
            )
            est_fee = pos.current_qty * mark * self.taker_fee
            unrealized += gross - est_fee

        total_equity = self.realized_equity + unrealized
        if total_equity > self.peak_equity:
            self.peak_equity = total_equity

        dd = (
            (self.peak_equity - total_equity) / self.peak_equity * Decimal("100")
            if self.peak_equity > Decimal("0")
            else Decimal("0")
        )
        if dd > self.max_drawdown_pct:
            self.max_drawdown_pct = dd

        wins = [t for t in self.closed_trades if t.net_pnl > Decimal("0")]
        losses = [t for t in self.closed_trades if t.net_pnl <= Decimal("0")]
        wr = (len(wins) / len(self.closed_trades) * 100.0) if self.closed_trades else 0.0

        gross_win = sum(t.net_pnl for t in wins)
        gross_loss = abs(sum(t.net_pnl for t in losses))
        pf = float(gross_win / gross_loss) if gross_loss > Decimal("0") else 0.0

        return PortfolioSnapshot(
            equity=total_equity,
            realized_equity=self.realized_equity,
            peak_equity=self.peak_equity,
            max_drawdown_pct=self.max_drawdown_pct,
            is_kill_switch_active=self.risk_manager.state.is_kill_switch_active,
            open_positions=dict(self.open_positions),
            closed_trades=list(self.closed_trades),
            total_net_pnl=total_equity - self.initial_capital,
            win_rate_pct=wr,
            profit_factor=pf,
        )

    def process_bar_event(
        self,
        bar_timestamp: int,
        bars_dict: Dict[str, Dict[str, float]],
        next_open_prices: Dict[str, Decimal],
        btc_bullish: bool = True,
        btc_bearish: bool = True,
    ) -> None:
        self.bar_counter += 1

        # 1. Evaluate open positions intra-bar
        for sym, pos in list(self.open_positions.items()):
            bar = bars_dict.get(sym)
            if not bar:
                continue

            rules = self.instrument_rules.get(sym, {})
            qty_step = rules.get("qtyStep", Decimal("0.01"))
            min_order_qty = rules.get("minOrderQty", Decimal("0.01"))

            action = self.pyramiding_engine.evaluate_bar(
                pos=pos,
                current_high=Decimal(str(bar["high"])),
                current_low=Decimal(str(bar["low"])),
                current_close=Decimal(str(bar["close"])),
                current_atr=Decimal(str(bar["atr"])),
                qty_step=qty_step,
                min_order_qty=min_order_qty,
            )

            if action.action_type == "EXIT":
                exit_price = (
                    pos.current_stop * (Decimal("1") - self.slippage)
                    if pos.direction == 1
                    else pos.current_stop * (Decimal("1") + self.slippage)
                )
                gross_pnl = (
                    pos.current_qty * (exit_price - pos.entry_price)
                    if pos.direction == 1
                    else pos.current_qty * (pos.entry_price - exit_price)
                )
                fee = pos.current_qty * exit_price * self.taker_fee
                net_pnl = gross_pnl - fee
                self.realized_equity += net_pnl

                is_win = net_pnl > Decimal("0")
                self.risk_manager.record_trade_result(is_win)

                self.closed_trades.append(
                    ClosedTrade(
                        symbol=sym,
                        direction=pos.direction,
                        entry_time=pos.entry_time,
                        exit_time=bar_timestamp,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        initial_qty=pos.initial_qty,
                        final_qty=pos.current_qty,
                        net_pnl=net_pnl,
                        exit_reason=action.exit_reason or "STOP_LOSS",
                        was_pyramided=pos.is_pyramided,
                        hold_hours=(bar_timestamp - pos.entry_time) / 3600000.0,
                    )
                )
                # Register Cooldown
                key = f"{sym}_{pos.direction}"
                self.cooldown_tracker[key] = self.bar_counter + self.cooldown_bars
                del self.open_positions[sym]

            elif action.action_type == "PYRAMID_ADD":
                add_exec_price = Decimal(str(bar["close"])) * (
                    Decimal("1") + self.slippage
                    if pos.direction == 1
                    else Decimal("1") - self.slippage
                )
                fee = action.added_qty * add_exec_price * self.taker_fee
                self.realized_equity -= fee

        # 2. Scanner: find breakout candidates
        long_candidates = []
        short_candidates = []

        for sym, bar in bars_dict.items():
            if sym in self.open_positions:
                continue

            cand = self.scanner.evaluate_symbol(
                symbol=sym,
                close=Decimal(str(bar["close"])),
                high=Decimal(str(bar["high"])),
                low=Decimal(str(bar["low"])),
                upper_channel=Decimal(str(bar["upper"])),
                lower_channel=Decimal(str(bar["lower"])),
                ema_fast=Decimal(str(bar["ema_fast"])),
                ema_slow=Decimal(str(bar["ema_slow"])),
                atr=Decimal(str(bar["atr"])),
                volume=Decimal(str(bar["volume"])),
                volume_sma=Decimal(str(bar["volume_sma"])),
            )
            if cand:
                if cand.direction == 1:
                    long_candidates.append(cand)
                else:
                    short_candidates.append(cand)

        curr_longs = sum(1 for p in self.open_positions.values() if p.direction == 1)
        curr_shorts = sum(1 for p in self.open_positions.values() if p.direction == -1)

        # Convert cooldown tracker to tuples for scanner
        cooldown_tuples = {}
        for k, exp_idx in self.cooldown_tracker.items():
            parts = k.split("_")
            cooldown_tuples[(parts[0], int(parts[1]))] = exp_idx

        ranked = self.scanner.rank_candidates(
            long_candidates=long_candidates,
            short_candidates=short_candidates,
            current_long_count=curr_longs,
            current_short_count=curr_shorts,
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
            cooldown_tracker=cooldown_tuples,
            current_bar_idx=self.bar_counter,
        )

        # 3. Size & execute signals strictly at next open (Zero Lookahead)
        for cand in ranked:
            rules = self.instrument_rules.get(cand.symbol, {})
            min_order_qty = rules.get("minOrderQty", Decimal("0.01"))
            qty_step = rules.get("qtyStep", Decimal("0.01"))
            min_notional = rules.get("minNotional", Decimal("5.0"))

            open_price = next_open_prices.get(cand.symbol)
            if open_price is None or open_price <= Decimal("0"):
                continue

            exec_entry_price = open_price * (
                Decimal("1") + self.slippage
                if cand.direction == 1
                else Decimal("1") - self.slippage
            )
            stop_distance = self.atr_stop_mult * cand.atr
            if stop_distance <= Decimal("0"):
                continue

            stop_price = (
                exec_entry_price - stop_distance
                if cand.direction == 1
                else exec_entry_price + stop_distance
            )

            sizing = self.risk_manager.calculate_position_size(
                equity=self.realized_equity,
                entry_price=exec_entry_price,
                stop_price=stop_price,
                min_order_qty=min_order_qty,
                qty_step=qty_step,
                min_notional=min_notional,
            )

            if sizing.allowed and sizing.qty > Decimal("0"):
                fee = sizing.qty * exec_entry_price * self.taker_fee
                self.realized_equity -= fee

                self.open_positions[cand.symbol] = ActivePosition(
                    symbol=cand.symbol,
                    direction=cand.direction,
                    initial_qty=sizing.qty,
                    current_qty=sizing.qty,
                    entry_price=exec_entry_price,
                    current_stop=stop_price,
                    initial_atr=cand.atr,
                    highest_price=exec_entry_price,
                    lowest_price=exec_entry_price,
                    entry_time=bar_timestamp,
                    is_pyramided=False,
                    is_breakeven_set=False,
                )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes current engine state for atomic persistence."""
        return {
            "version": "2.1",
            "initial_capital": str(self.initial_capital),
            "realized_equity": str(self.realized_equity),
            "peak_equity": str(self.peak_equity),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "bar_counter": self.bar_counter,
            "cooldown_tracker": self.cooldown_tracker,
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
                "consecutive_losses": getattr(self.risk_manager.state, "consecutive_losses", 0),
                "consecutive_wins": getattr(self.risk_manager.state, "consecutive_wins", 0),
                "is_kill_switch_active": self.risk_manager.state.is_kill_switch_active,
            },
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """Restores engine state from serialized dictionary."""
        if "realized_equity" in data:
            self.realized_equity = Decimal(str(data["realized_equity"]))
        if "peak_equity" in data:
            self.peak_equity = Decimal(str(data["peak_equity"]))
        if "max_drawdown_pct" in data:
            self.max_drawdown_pct = Decimal(str(data["max_drawdown_pct"]))
        if "bar_counter" in data:
            self.bar_counter = int(data["bar_counter"])
        if "cooldown_tracker" in data:
            self.cooldown_tracker = dict(data["cooldown_tracker"])

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
            self.risk_manager.state.is_kill_switch_active = bool(rm.get("is_kill_switch_active", False))
            self.risk_manager.state.consecutive_losses = int(rm.get("consecutive_losses", 0))
            self.risk_manager.state.consecutive_wins = int(rm.get("consecutive_wins", 0))
