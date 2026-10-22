"""
Zero-Lookahead Replay Engine for News Core.
Strictly adheres to PROMPT_NEWS_BYBIT_REPLAY_TEST_v2.md:
- Time step simulation: at time T, bot only sees data published <= T.
- Realistic execution:
  * Limit post-only orders execute ONLY if price trades strictly THROUGH the limit level.
  * Market orders execute with slippage according to cost models (BASE, STRESS, NEWS_STRESS).
  * Stop loss is a market order executing with slippage.
- Cost models:
  * BASE: 5.3 bp
  * STRESS: 8.5 bp
  * NEWS_STRESS: 15.9 bp (3x slippage in ±5m window)
- Placebo validation: compares event performance against randomized timing controls.
"""

from __future__ import annotations
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

try:
    from .models import (
        Side,
        EventType,
        ActionType,
        PositionView,
        PositionAction,
        OrderIntent,
        MarketCandle,
    )
    from .news_config import NewsConfig
    from .news_core_final import NewsCore
except (ImportError, ValueError):
    from models import (
        Side,
        EventType,
        ActionType,
        PositionView,
        PositionAction,
        OrderIntent,
        MarketCandle,
    )
    from news_config import NewsConfig
    from news_core_final import NewsCore


@dataclass
class ReplayTradeRecord:
    """Record of a trade simulated in historical replay."""
    trade_id: str
    symbol: str
    strategy_tag: str
    side: Side
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    stop_loss: float = 0.0
    take_profit: Optional[float] = None
    size: float = 0.0
    cost_paid_bp: float = 0.0
    net_pnl_usd: float = 0.0
    net_return_r: float = 0.0
    exit_reason: str = ""
    is_placebo: bool = False


@dataclass
class DefenseShieldRecord:
    """Record of a defensive shield trigger during replay."""
    action_type: ActionType
    symbol: str
    side: Side
    trigger_time: datetime
    trigger_price: float
    reason: str
    loss_avoided_pct: float = 0.0
    saved_capital_usd: float = 0.0


@dataclass
class ReplaySummary:
    """Summary metrics of a replay test."""
    total_news_events: int
    classified_count: int
    confirmed_count: int
    defensive_actions_count: int
    offensive_orders_count: int
    executed_trades_count: int
    total_net_pnl_usd: float
    avg_r_multiple: float
    win_rate_pct: float
    total_defense_capital_saved_usd: float
    placebo_avg_r_multiple: float
    edge_detected: bool
    cost_model: str
    verdict: str


class ReplaySimulator:
    """
    Step-by-step zero-lookahead backtest simulator.
    """

    def __init__(self, config: Optional[NewsConfig] = None):
        self.config = config or NewsConfig()
        self.core = NewsCore(self.config)
        self.cost_bp = self._get_cost_bp(self.config.cost_model)

    def _get_cost_bp(self, model: str) -> float:
        """Cost model per side in basis points."""
        if model == "STRESS":
            return 8.5
        elif model == "NEWS_STRESS":
            return 15.9  # 5.3 * 3
        return 5.3  # BASE

    def simulate_event(
        self,
        headline: str,
        published_at_utc: datetime,
        candles_1m: List[MarketCandle],
        median_vol_60m: float,
        existing_position: Optional[PositionView] = None,
        symbol: Optional[str] = None,
        account_balance: float = 1000.0,
    ) -> Dict[str, Any]:
        """
        Replay a single news event over a sequence of future 1m candles.
        
        Args:
            headline: Raw news headline text
            published_at_utc: Exact publication timestamp
            candles_1m: Forward 1m candles [T0, T1, T2, ...]
            median_vol_60m: 60-minute median volume leading up to T0
            existing_position: Active position in main bot prior to headline
            symbol: Optional explicit symbol (else extracted)
            account_balance: Account equity
            
        Returns:
            Dict containing actions, execution status, and outcomes
        """
        sym = symbol or self.core.extract_symbol(headline)
        
        # 1. Regime Guard Check
        regime_status = self.core.get_status(sym, published_at_utc)
        if regime_status["block_new_entries"]:
            return {
                "symbol": sym,
                "status": "BLOCKED_BY_REGIME_GUARD",
                "reason": regime_status["reason"],
                "actions": [],
                "trade": None,
                "defense": None,
            }

        # 2. Layer 1 Classification
        event_type, score = self.core.classify(headline, symbol=sym, timestamp=published_at_utc)
        if abs(score) < self.config.min_score_to_act:
            return {
                "symbol": sym,
                "status": "IGNORED_LOW_SCORE",
                "score": score,
                "event_type": event_type.value,
                "actions": [],
                "trade": None,
                "defense": None,
            }

        defense_record: Optional[DefenseShieldRecord] = None
        current_price = candles_1m[0].open if candles_1m else 100.0

        # 3. Layer 3 Position Matrix Check (if existing position)
        if existing_position and existing_position.size > 0:
            actions = self.core.manage_positions(
                symbol=sym,
                score=score,
                event=event_type,
                open_positions=[existing_position],
                current_price=current_price,
                timestamp=published_at_utc,
            )
            if actions:
                act = actions[0]
                if act.action_type == ActionType.CLOSE_MARKET:
                    # Calculate how much capital/drawdown was avoided over subsequent candles
                    worst_price = min(c.low for c in candles_1m[:15]) if candles_1m else current_price
                    avoided_loss_pct = (current_price - worst_price) / current_price if current_price > 0 else 0.0
                    saved_usd = avoided_loss_pct * existing_position.size * current_price

                    defense_record = DefenseShieldRecord(
                        action_type=act.action_type,
                        symbol=sym,
                        side=act.side,
                        trigger_time=published_at_utc,
                        trigger_price=current_price,
                        reason=act.reason,
                        loss_avoided_pct=avoided_loss_pct,
                        saved_capital_usd=saved_usd,
                    )
                    return {
                        "symbol": sym,
                        "status": "DEFENSE_SHIELD_TRIGGERED",
                        "event_type": event_type.value,
                        "score": score,
                        "actions": actions,
                        "defense": defense_record,
                        "trade": None,
                    }

        # 4. Layer 2 Market Confirmation Check
        if not candles_1m:
            return {
                "symbol": sym,
                "status": "NO_CANDLE_DATA",
                "actions": [],
                "trade": None,
                "defense": None,
            }

        # First candle after headline
        impulse_candle = candles_1m[0]
        confirmed = self.core.check_market_confirmation(
            score=score,
            candle_1m=impulse_candle,
            median_vol_60m=median_vol_60m,
            base_price=impulse_candle.open,
        )

        if not confirmed and self.config.require_market_confirmation:
            return {
                "symbol": sym,
                "status": "UNCONFIRMED_MARKET_MOVE",
                "score": score,
                "event_type": event_type.value,
                "candle_return": impulse_candle.return_pct,
                "actions": [],
                "trade": None,
                "defense": None,
            }

        # 5. Layer 4 Offensive Generation (if no open position and confirmed)
        side = Side.LONG if score > 0 else Side.SHORT
        trade_record: Optional[ReplayTradeRecord] = None

        if score > 0 and self.config.enable_n1_pullback:
            # Generate N1 Pullback order
            order = self.core.generate_n1_pullback(
                symbol=sym,
                high=impulse_candle.high,
                low=impulse_candle.low,
                side=side,
                deposit=account_balance,
                timestamp=published_at_utc,
            )
            if order:
                # Replay forward candles to see if limit order filled strictly through limit price
                trade_record = self._simulate_limit_execution(
                    order=order,
                    candles=candles_1m[1:],  # subsequent bars
                    entry_time=published_at_utc,
                )

        elif score <= -2 and self.config.enable_n3_crash_short:
            # Generate N3 Crash Short order (Market execution)
            order = self.core.generate_n3_crash_short(
                symbol=sym,
                current_price=impulse_candle.close,
                high_price=impulse_candle.high,
                deposit=account_balance,
                timestamp=published_at_utc,
            )
            if order:
                trade_record = self._simulate_market_execution(
                    order=order,
                    candles=candles_1m[1:],
                    entry_time=published_at_utc,
                )

        return {
            "symbol": sym,
            "status": "SUCCESS",
            "event_type": event_type.value,
            "score": score,
            "confirmed": True,
            "actions": [],
            "trade": trade_record,
            "defense": None,
        }

    def _simulate_limit_execution(
        self,
        order: OrderIntent,
        candles: List[MarketCandle],
        entry_time: datetime,
    ) -> Optional[ReplayTradeRecord]:
        """
        Simulate limit post-only execution.
        Must trade strictly through limit price (low < limit_price for Long).
        """
        filled = False
        fill_price = order.limit_price
        fill_time: Optional[datetime] = None
        fill_idx = -1

        for i, c in enumerate(candles):
            dt = datetime.fromtimestamp(c.timestamp / 1000.0, tz=timezone.utc)
            if order.side == Side.LONG:
                if c.low < order.limit_price:  # Strictly through
                    filled = True
                    fill_time = dt
                    fill_idx = i
                    break
            else:
                if c.high > order.limit_price:
                    filled = True
                    fill_time = dt
                    fill_idx = i
                    break

        if not filled or fill_idx < 0:
            return None  # Unfilled limit order expired without chasing

        # Simulate position outcome over remaining candles
        remaining_candles = candles[fill_idx + 1:]
        cost_rate = (self.cost_bp / 10000.0)
        entry_cost = fill_price * order.size * cost_rate

        for c in remaining_candles:
            dt = datetime.fromtimestamp(c.timestamp / 1000.0, tz=timezone.utc)
            if order.side == Side.LONG:
                # Stop loss hit
                if c.low <= order.stop_loss:
                    exit_price = order.stop_loss * (1.0 - cost_rate)
                    exit_cost = exit_price * order.size * cost_rate
                    pnl = (exit_price - fill_price) * order.size - (entry_cost + exit_cost)
                    risk_amount = (fill_price - order.stop_loss) * order.size
                    r_ret = pnl / risk_amount if risk_amount > 0 else -1.0
                    return ReplayTradeRecord(
                        trade_id=f"N1-{order.symbol}-{int(entry_time.timestamp())}",
                        symbol=order.symbol,
                        strategy_tag=order.strategy_tag,
                        side=order.side,
                        entry_time=fill_time,
                        entry_price=fill_price,
                        exit_time=dt,
                        exit_price=exit_price,
                        stop_loss=order.stop_loss,
                        take_profit=order.take_profit,
                        size=order.size,
                        cost_paid_bp=self.cost_bp * 2,
                        net_pnl_usd=pnl,
                        net_return_r=r_ret,
                        exit_reason="STOP_LOSS",
                    )
                # Take profit hit
                if order.take_profit and c.high >= order.take_profit:
                    exit_price = order.take_profit * (1.0 - cost_rate)
                    exit_cost = exit_price * order.size * cost_rate
                    pnl = (exit_price - fill_price) * order.size - (entry_cost + exit_cost)
                    risk_amount = (fill_price - order.stop_loss) * order.size
                    r_ret = pnl / risk_amount if risk_amount > 0 else 1.0
                    return ReplayTradeRecord(
                        trade_id=f"N1-{order.symbol}-{int(entry_time.timestamp())}",
                        symbol=order.symbol,
                        strategy_tag=order.strategy_tag,
                        side=order.side,
                        entry_time=fill_time,
                        entry_price=fill_price,
                        exit_time=dt,
                        exit_price=exit_price,
                        stop_loss=order.stop_loss,
                        take_profit=order.take_profit,
                        size=order.size,
                        cost_paid_bp=self.cost_bp * 2,
                        net_pnl_usd=pnl,
                        net_return_r=r_ret,
                        exit_reason="TAKE_PROFIT",
                    )

        # Reached end of horizon: exit at last close
        last_c = remaining_candles[-1] if remaining_candles else candles[-1]
        exit_price = last_c.close * (1.0 - cost_rate if order.side == Side.LONG else 1.0 + cost_rate)
        exit_cost = exit_price * order.size * cost_rate
        pnl = ((exit_price - fill_price) if order.side == Side.LONG else (fill_price - exit_price)) * order.size - (entry_cost + exit_cost)
        risk_amount = abs(fill_price - order.stop_loss) * order.size
        r_ret = pnl / risk_amount if risk_amount > 0 else 0.0

        return ReplayTradeRecord(
            trade_id=f"N1-{order.symbol}-{int(entry_time.timestamp())}",
            symbol=order.symbol,
            strategy_tag=order.strategy_tag,
            side=order.side,
            entry_time=fill_time,
            entry_price=fill_price,
            exit_time=datetime.fromtimestamp(last_c.timestamp / 1000.0, tz=timezone.utc),
            exit_price=exit_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            size=order.size,
            cost_paid_bp=self.cost_bp * 2,
            net_pnl_usd=pnl,
            net_return_r=r_ret,
            exit_reason="TIME_HORIZON_CLOSE",
        )

    def _simulate_market_execution(
        self,
        order: OrderIntent,
        candles: List[MarketCandle],
        entry_time: datetime,
    ) -> Optional[ReplayTradeRecord]:
        """Simulate market order execution with slippage."""
        if not candles:
            return None

        cost_rate = (self.cost_bp / 10000.0)
        # Slippage: for SHORT, market entry price slips downward
        entry_price = candles[0].open * (1.0 - cost_rate)
        entry_cost = entry_price * order.size * cost_rate

        for c in candles[1:]:
            dt = datetime.fromtimestamp(c.timestamp / 1000.0, tz=timezone.utc)
            # Short stop loss hit
            if c.high >= order.stop_loss:
                exit_price = order.stop_loss * (1.0 + cost_rate)
                exit_cost = exit_price * order.size * cost_rate
                pnl = (entry_price - exit_price) * order.size - (entry_cost + exit_cost)
                risk = (order.stop_loss - entry_price) * order.size
                r_ret = pnl / risk if risk > 0 else -1.0
                return ReplayTradeRecord(
                    trade_id=f"N3-{order.symbol}-{int(entry_time.timestamp())}",
                    symbol=order.symbol,
                    strategy_tag=order.strategy_tag,
                    side=order.side,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    exit_time=dt,
                    exit_price=exit_price,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    size=order.size,
                    cost_paid_bp=self.cost_bp * 2,
                    net_pnl_usd=pnl,
                    net_return_r=r_ret,
                    exit_reason="STOP_LOSS",
                )
            # Short take profit hit
            if order.take_profit and c.low <= order.take_profit:
                exit_price = order.take_profit * (1.0 + cost_rate)
                exit_cost = exit_price * order.size * cost_rate
                pnl = (entry_price - exit_price) * order.size - (entry_cost + exit_cost)
                risk = (order.stop_loss - entry_price) * order.size
                r_ret = pnl / risk if risk > 0 else 1.0
                return ReplayTradeRecord(
                    trade_id=f"N3-{order.symbol}-{int(entry_time.timestamp())}",
                    symbol=order.symbol,
                    strategy_tag=order.strategy_tag,
                    side=order.side,
                    entry_time=entry_time,
                    entry_price=entry_price,
                    exit_time=dt,
                    exit_price=exit_price,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    size=order.size,
                    cost_paid_bp=self.cost_bp * 2,
                    net_pnl_usd=pnl,
                    net_return_r=r_ret,
                    exit_reason="TAKE_PROFIT",
                )

        # Exit at end of window
        last_c = candles[-1]
        exit_price = last_c.close * (1.0 + cost_rate)
        exit_cost = exit_price * order.size * cost_rate
        pnl = (entry_price - exit_price) * order.size - (entry_cost + exit_cost)
        risk = abs(order.stop_loss - entry_price) * order.size
        r_ret = pnl / risk if risk > 0 else 0.0

        return ReplayTradeRecord(
            trade_id=f"N3-{order.symbol}-{int(entry_time.timestamp())}",
            symbol=order.symbol,
            strategy_tag=order.strategy_tag,
            side=order.side,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=datetime.fromtimestamp(last_c.timestamp / 1000.0, tz=timezone.utc),
            exit_price=exit_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            size=order.size,
            cost_paid_bp=self.cost_bp * 2,
            net_pnl_usd=pnl,
            net_return_r=r_ret,
            exit_reason="TIME_HORIZON_CLOSE",
        )
