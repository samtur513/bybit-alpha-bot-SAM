"""
Core Engine v2.0: Unified Multi-Alpha Growth System.
A deep module implementing SPEC_V2.md.
Combines:
- Multi-Coin Scanner & Ranking
- Fractional Kelly Position Sizing with Regime Kill-Switch
- Anti-Martingale Pyramiding & Trailing Engine
- Strict Bybit V5 precision & fee accounting
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from src.kelly_sizing import KellyRiskManager, KellySizingResult
from src.multi_scanner import MultiCoinScanner, CoinCandidate
from src.pyramiding_engine import PyramidingEngine, ActivePosition, PyramidingAction


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
    Deep facade managing the complete v2.0 trading lifecycle.
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("300.0"),
        instrument_rules: Optional[Dict[str, Dict[str, Decimal]]] = None,
        base_risk_pct: Decimal = Decimal("0.025"),
        choppy_risk_pct: Decimal = Decimal("0.0075"),
        max_leverage: Decimal = Decimal("3.5"),
        atr_stop_mult: Decimal = Decimal("2.5"),
        taker_fee: Decimal = Decimal("0.0006"),
        slippage: Decimal = Decimal("0.0002"),
    ):
        self.initial_capital = initial_capital
        self.realized_equity = initial_capital
        self.peak_equity = initial_capital
        self.max_drawdown_pct = Decimal("0")

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

        # Deep sub-components
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
        self.scanner = MultiCoinScanner(max_concurrent_positions=3)

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
            rate = funding_rates.get(sym, Decimal("0"))
            if rate != Decimal("0"):
                mark = current_prices.get(sym, pos.entry_price)
                # Long pays if rate > 0; Short receives if rate > 0
                funding_pnl = -1 * pos.direction * pos.current_qty * mark * rate
                self.realized_equity += funding_pnl

    def process_bar_event(
        self,
        timestamp: int,
        bars: Dict[str, Dict[str, float]],
        next_open_prices: Dict[str, Decimal],
    ) -> None:
        """
        Processes a closed bar across all symbols:
        1. Manages open positions (stops, trailing, pyramiding).
        2. Scans for new breakout entries.
        3. Records equity & updates kill-switch.
        """
        # Convert prices for open positions
        current_closes = {
            s: Decimal(str(round(b["close"], 6)))
            for s, b in bars.items()
        }

        # 1. Manage Open Positions
        symbols_to_close = []
        for sym, pos in list(self.open_positions.items()):
            if sym not in bars:
                continue
            b = bars[sym]
            rules = self.instrument_rules.get(sym, {})
            qty_step = rules.get("qtyStep", Decimal("0.001"))
            min_order_qty = rules.get("minOrderQty", Decimal("0.001"))

            action = self.pyramiding_engine.evaluate_bar(
                pos=pos,
                current_high=Decimal(str(round(b["high"], 6))),
                current_low=Decimal(str(round(b["low"], 6))),
                current_close=Decimal(str(round(b["close"], 6))),
                current_atr=Decimal(str(round(b["atr"], 6))),
                qty_step=qty_step,
                min_order_qty=min_order_qty,
            )

            if action.action_type == "EXIT":
                symbols_to_close.append((sym, action.exit_reason))
            elif action.action_type == "PYRAMID_ADD":
                # Execute pyramid add-on fee
                add_exec_price = next_open_prices.get(sym, pos.entry_price)
                fee = action.added_qty * add_exec_price * self.taker_fee
                self.realized_equity -= fee

        # Execute Exits
        for sym, exit_reason in symbols_to_close:
            pos = self.open_positions[sym]
            next_open = next_open_prices.get(sym, pos.current_stop)
            # Exit execution price with slippage
            if pos.direction == 1:
                exit_price = min(next_open, pos.current_stop) * (Decimal("1") - self.slippage)
                gross_pnl = pos.current_qty * (exit_price - pos.entry_price)
            else:
                exit_price = max(next_open, pos.current_stop) * (Decimal("1") + self.slippage)
                gross_pnl = pos.current_qty * (pos.entry_price - exit_price)

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
                    exit_time=timestamp,
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    initial_qty=pos.initial_qty,
                    final_qty=pos.current_qty,
                    net_pnl=net_pnl,
                    exit_reason=exit_reason,
                    was_pyramided=pos.is_pyramided,
                    hold_hours=(timestamp - pos.entry_time) / 3600000.0,
                )
            )
            del self.open_positions[sym]

        # 2. Scan & Rank Candidates for Entry
        candidates: List[CoinCandidate] = []
        for sym, b in bars.items():
            cand = self.scanner.evaluate_symbol_bar(
                symbol=sym,
                close=b["close"],
                upper_channel=b.get("upper", np.nan),
                lower_channel=b.get("lower", np.nan),
                ema_fast=b.get("ema_fast", np.nan),
                ema_slow=b.get("ema_slow", np.nan),
                atr=b.get("atr", np.nan),
                volume=b.get("volume", np.nan),
                volume_sma=b.get("volume_sma", np.nan),
            )
            if cand is not None:
                candidates.append(cand)

        long_count = sum(1 for p in self.open_positions.values() if p.direction == 1)
        short_count = sum(1 for p in self.open_positions.values() if p.direction == -1)
        ranked = self.scanner.rank_candidates(
            candidates=candidates,
            currently_held_symbols=list(self.open_positions.keys()),
            current_long_count=long_count,
            current_short_count=short_count,
        )

        # 3. Size & Enter Top Candidate
        current_eq = self.get_equity(current_closes)
        for cand in ranked:
            if cand.symbol in self.open_positions:
                continue
            rules = self.instrument_rules.get(cand.symbol, {})
            qty_step = rules.get("qtyStep", Decimal("0.001"))
            min_order_qty = rules.get("minOrderQty", Decimal("0.001"))
            min_notional = rules.get("minNotional", Decimal("5.0"))

            next_open = next_open_prices.get(cand.symbol, cand.close_price)
            exec_entry_price = (
                next_open * (Decimal("1") + self.slippage)
                if cand.direction == 1
                else next_open * (Decimal("1") - self.slippage)
            )

            stop_dist = cand.atr * self.atr_stop_mult
            initial_stop = (
                exec_entry_price - stop_dist
                if cand.direction == 1
                else exec_entry_price + stop_dist
            )

            sizing = self.risk_manager.calculate_position_size(
                equity=current_eq,
                entry_price=exec_entry_price,
                stop_price=initial_stop,
                min_order_qty=min_order_qty,
                qty_step=qty_step,
                min_notional=min_notional,
            )

            if sizing.allowed:
                fee = sizing.qty * exec_entry_price * self.taker_fee
                self.realized_equity -= fee

                self.open_positions[cand.symbol] = ActivePosition(
                    symbol=cand.symbol,
                    direction=cand.direction,
                    initial_qty=sizing.qty,
                    current_qty=sizing.qty,
                    entry_price=exec_entry_price,
                    current_stop=initial_stop,
                    initial_atr=cand.atr,
                    highest_price=exec_entry_price,
                    lowest_price=exec_entry_price,
                    entry_time=timestamp,
                    is_pyramided=False,
                    is_breakeven_set=False,
                )
                break  # Enter at most one new position per hourly bar

        # 4. Record Equity & Drawdown
        total_eq = self.get_equity(current_closes)
        if total_eq > self.peak_equity:
            self.peak_equity = total_eq
        if self.peak_equity > Decimal("0"):
            dd = (self.peak_equity - total_eq) / self.peak_equity * Decimal("100")
            if dd > self.max_drawdown_pct:
                self.max_drawdown_pct = dd
        self.equity_history.append((timestamp, total_eq))

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
