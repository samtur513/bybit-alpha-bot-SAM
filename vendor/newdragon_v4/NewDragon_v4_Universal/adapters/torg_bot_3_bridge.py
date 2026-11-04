"""
Bridge Adapter for integrating NewDragon v4.0 Universal Phase 1 into Torg_Bot_3.
Works seamlessly with both Torg_Bot_3 v3 (Donchian Breakout) and v3.7 (Scalp OMS).

Provides:
1. UniversalRiskBridge: Wraps UniversalPositionSizer to calculate exact contract quantity
   for Bybit V5 Linear Perpetuals with strict True 1R accounting and minNotional protection.
2. AdaptiveExitBridge: Wraps AdaptiveExitFSM to provide 4-stage position management
   (Fast Scratch, Ratchet Lock, Partial Scale-Out Maker, Chandelier Trail with Shock compression).
3. CausalRegimeBridge: Wraps CausalRegimeDetector to stream closed bars and classify
   market regime (bull, bear, sideways, shock) with ZERO lookahead bias.
"""
from typing import Dict, Any, Optional, Tuple
from decimal import Decimal

from core.models import (
    InstrumentSpec,
    PortfolioState,
    SignalOutput,
    PositionTracker,
    ExitAction,
)
from layers.l5_risk import UniversalPositionSizer
from layers.l6_execution.exit_fsm import AdaptiveExitFSM
from layers.l2_regime import CausalRegimeDetector


class UniversalRiskBridge:
    """
    Adapter for Torg_Bot_3 risk management.
    Drop-in enhancement for Kelly sizing with micro-account protection and True 1R accounting.
    """

    def __init__(
        self,
        taker_fee: float = 0.00055,
        maker_fee: float = 0.00020,
        slippage_base: float = 0.0005,
        max_leverage: float = 10.0,
    ) -> None:
        self.sizer = UniversalPositionSizer(
            taker_fee=taker_fee,
            maker_fee=maker_fee,
            slippage_base=slippage_base,
            max_leverage=max_leverage,
        )

    def size_position_for_torg_bot(
        self,
        symbol: str,
        side: str,  # "Buy" / "Sell" or "LONG" / "SHORT"
        entry_price: float,
        stop_price: float,
        capital_usdt: float,
        available_margin_usdt: float,
        current_drawdown_pct: float,  # e.g. 0.04 for 4%
        market_regime: str = "bull",
        qty_step: float = 0.001,
        min_order_qty: float = 0.001,
        min_notional: float = 5.0,
        max_leverage: float = 10.0,
        atr_1h: float = 0.0,
        composite_score: float = 1.0,
    ) -> Tuple[float, Optional[str]]:
        """
        Calculates contract order quantity for Torg_Bot_3.
        Returns (qty, rejection_reason). If rejected by minNotional or drawdown, qty is 0.0.
        """
        norm_side = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        spec = InstrumentSpec(
            symbol=symbol,
            qty_step=qty_step,
            tick_size=0.01,
            min_order_qty=min_order_qty,
            min_notional=min_notional,
            max_leverage=max_leverage,
        )
        portfolio = PortfolioState(
            total_capital=capital_usdt,
            available_margin=available_margin_usdt,
            current_drawdown=current_drawdown_pct,
            regime=market_regime,
            asset_volatility=0.025,
            inv_vol_sum=40.0,
        )
        sig = SignalOutput(
            symbol=symbol,
            side=norm_side,
            entry_price=entry_price,
            stop_price=stop_price,
            atr_1h=atr_1h or abs(entry_price - stop_price),
            composite_score=composite_score,
        )

        qty = self.sizer.compute_quantity(sig, spec, portfolio)
        if qty <= 0.0:
            if current_drawdown_pct >= 0.20:
                reason = "Drawdown >= 20% (Emergency Halt)"
            elif market_regime.lower() == "shock":
                reason = "Market Regime is Shock (Entries Prohibited)"
            else:
                reason = f"Order notional below minNotional (${min_notional}) or risk budget exhausted"
            return 0.0, reason

        return qty, None


class AdaptiveExitBridge:
    """
    Adapter for Torg_Bot_3 active position exit evaluation.
    Evaluates every bar and returns recommended action.
    """

    def __init__(self) -> None:
        self.fsm = AdaptiveExitFSM()
        self._active_trackers: Dict[str, PositionTracker] = {}

    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_price: float,
        qty: float,
    ) -> PositionTracker:
        """Registers a newly opened position in the FSM tracker."""
        norm_side = "LONG" if side.upper() in ("BUY", "LONG") else "SHORT"
        tracker = PositionTracker(
            symbol=symbol,
            side=norm_side,
            entry_price=entry_price,
            current_stop=stop_price,
            initial_1r_dist=abs(entry_price - stop_price),
            remaining_qty=qty,
            stage=0,
        )
        self._active_trackers[symbol] = tracker
        return tracker

    def on_bar_update(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
        prev_high: float,
        prev_low: float,
        highest_20: float,
        lowest_20: float,
        atr_1h: float,
        z_ofi: float = 0.0,
        shock_prob: float = 0.0,
    ) -> ExitAction:
        """Evaluates bar against registered position."""
        tracker = self._active_trackers.get(symbol)
        if tracker is None:
            return ExitAction(action_type="HOLD")

        action = self.fsm.evaluate_bar(
            pos=tracker,
            high=high,
            low=low,
            close=close,
            prev_high=prev_high,
            prev_low=prev_low,
            highest_20=highest_20,
            lowest_20=lowest_20,
            atr_1h=atr_1h,
            z_ofi=z_ofi,
            shock_prob=shock_prob,
        )

        if action.action_type == "FULL_CLOSE_SCRATCH":
            self._active_trackers.pop(symbol, None)
        elif action.action_type == "PARTIAL_CLOSE_AND_STOP":
            tracker.remaining_qty *= (1.0 - action.close_qty_ratio)

        return action

    def deregister_position(self, symbol: str) -> None:
        """Removes position when fully closed."""
        self._active_trackers.pop(symbol, None)
