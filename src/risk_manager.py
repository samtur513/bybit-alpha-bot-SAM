"""
Risk Manager v1.0.
Profile-based risk management with:
- Three profiles: CONSERVATIVE, MODERATE, AGGRESSIVE
- Streak-adaptive risk sizing
- Loss cooldown timer
- Partial TP support
- All Decimal arithmetic
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import datetime
from typing import Dict, Optional, Tuple

from src.config import (
    DEFAULT_RISK_PROFILE,
    INSTRUMENT_RULES,
    LOSS_STREAK_THRESHOLD,
    WIN_STREAK_RECOVERY,
    RISK_PROFILES,
    RiskProfile,
    # Backward compat
    BASE_RISK_PCT,
    BASE_SLIPPAGE,
    BASE_TAKER_FEE,
)

@dataclass
class SizingResult:
    allowed: bool
    qty: Decimal
    entry_price: Decimal
    stop_price: Decimal
    risk_amount: Decimal
    risk_pct: Decimal
    notional: Decimal
    skip_reason: Optional[str] = None

class RiskManager:
    """Profile-based risk management with streak-adaptive sizing."""

    def __init__(
        self,
        initial_capital: Decimal,
        profile_name: str = DEFAULT_RISK_PROFILE,
    ):
        self.initial_capital = Decimal(str(initial_capital))
        self.equity = self.initial_capital
        self.peak_equity = self.initial_capital

        # Load risk profile
        self.profile: RiskProfile = RISK_PROFILES[profile_name]

        # Risk percentage state
        self.current_risk_pct = self.profile.base_risk_pct
        self.in_reduced_risk_mode = False

        # Streak tracking
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.in_streak_reduced_mode = False

        # Loss cooldown
        self.cooldown_until_ts: int = 0

        # Circuit breaker tracking
        self.day_start_equity = self.initial_capital
        self.current_day_str = ""
        self.daily_lockout_until_ts: int = 0

        self.week_start_equity = self.initial_capital
        self.current_week_str = ""
        self.weekly_lockout_until_ts: int = 0

        self.is_permanently_halted = False
        self.halt_reason: Optional[str] = None
        self.halt_timestamp: Optional[int] = None

        # Statistics
        self.trades_skipped_min_size = 0
        self.trades_skipped_risk_budget = 0
        self.trades_skipped_daily_lock = 0
        self.trades_skipped_weekly_lock = 0
        self.trades_skipped_cooldown = 0
        self.trades_skipped_filters = 0

    def record_trade_result(self, net_pnl: Decimal, exit_time_ts: int) -> None:
        """Update streak counters and cooldown after each trade."""
        if net_pnl > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            # Set cooldown
            cooldown_ms = self.profile.cooldown_hours * 3600 * 1000
            self.cooldown_until_ts = exit_time_ts + cooldown_ms

        # Streak-adaptive risk
        if self.consecutive_losses >= LOSS_STREAK_THRESHOLD:
            self.in_streak_reduced_mode = True
        elif self.consecutive_wins >= WIN_STREAK_RECOVERY:
            self.in_streak_reduced_mode = False

    def update_calendar_and_equity(self, current_ts: int, current_equity: Decimal) -> None:
        """
        Updates day/week baselines, peak equity, and checks circuit breakers.
        """
        self.equity = current_equity
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        dt_utc = datetime.datetime.fromtimestamp(current_ts / 1000.0, tz=datetime.timezone.utc)
        day_str = dt_utc.strftime("%Y-%m-%d")
        week_str = f"{dt_utc.year}-W{dt_utc.isocalendar()[1]:02d}"

        # New UTC Day
        if day_str != self.current_day_str:
            self.current_day_str = day_str
            self.day_start_equity = self.equity

        # New UTC Week
        if week_str != self.current_week_str:
            self.current_week_str = week_str
            self.week_start_equity = self.equity

        # Current drawdown from peak
        if self.peak_equity > 0:
            current_dd = (self.peak_equity - self.equity) / self.peak_equity
        else:
            current_dd = Decimal("0.0")

        # 30%/25%/20% peak drawdown → permanent halt
        if current_dd >= self.profile.max_peak_drawdown_pct:
            self.is_permanently_halted = True
            self.halt_reason = (
                f"Max peak drawdown {float(self.profile.max_peak_drawdown_pct*100):.0f}% "
                f"exceeded ({float(current_dd*100):.2f}%)"
            )
            self.halt_timestamp = current_ts

        # Daily circuit breaker
        if self.day_start_equity > 0:
            daily_loss_pct = (self.day_start_equity - self.equity) / self.day_start_equity
            if daily_loss_pct >= self.profile.daily_loss_limit_pct:
                next_day_dt = (dt_utc + datetime.timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                self.daily_lockout_until_ts = int(next_day_dt.timestamp() * 1000)

        # Weekly circuit breaker
        if self.week_start_equity > 0:
            weekly_loss_pct = (self.week_start_equity - self.equity) / self.week_start_equity
            if weekly_loss_pct >= self.profile.weekly_loss_limit_pct:
                days_ahead = 7 - dt_utc.weekday()
                next_mon_dt = (dt_utc + datetime.timedelta(days=days_ahead)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                self.weekly_lockout_until_ts = int(next_mon_dt.timestamp() * 1000)

        # Dynamic risk sizing mode
        if current_dd >= self.profile.dd_risk_reduction_threshold:
            self.in_reduced_risk_mode = True
        elif current_dd < self.profile.dd_risk_recovery_threshold:
            self.in_reduced_risk_mode = False

        # Set effective risk pct
        if self.in_reduced_risk_mode or self.in_streak_reduced_mode:
            self.current_risk_pct = self.profile.reduced_risk_pct
        else:
            self.current_risk_pct = self.profile.base_risk_pct

    def is_cooldown_active(self, current_ts: int) -> bool:
        """Check if loss cooldown is active."""
        return current_ts < self.cooldown_until_ts

    def calculate_order_size(
        self,
        symbol: str,
        direction: int,
        entry_price: Decimal,
        atr: Decimal,
        k_mult: float,
        current_funding_rate: Decimal,
        current_ts: int,
        fee_rate: Decimal = BASE_TAKER_FEE,
        slippage_rate: Decimal = BASE_SLIPPAGE,
    ) -> SizingResult:
        """
        Calculates position size per risk profile.
        """
        if self.is_permanently_halted:
            return SizingResult(
                False, Decimal("0"), entry_price, Decimal("0"),
                Decimal("0"), Decimal("0"), Decimal("0"),
                "Strategy permanently halted"
            )

        if current_ts < self.daily_lockout_until_ts:
            self.trades_skipped_daily_lock += 1
            return SizingResult(
                False, Decimal("0"), entry_price, Decimal("0"),
                Decimal("0"), Decimal("0"), Decimal("0"),
                "Daily loss lockout active"
            )

        if current_ts < self.weekly_lockout_until_ts:
            self.trades_skipped_weekly_lock += 1
            return SizingResult(
                False, Decimal("0"), entry_price, Decimal("0"),
                Decimal("0"), Decimal("0"), Decimal("0"),
                "Weekly loss lockout active"
            )

        if self.is_cooldown_active(current_ts):
            self.trades_skipped_cooldown += 1
            return SizingResult(
                False, Decimal("0"), entry_price, Decimal("0"),
                Decimal("0"), Decimal("0"), Decimal("0"),
                "Loss cooldown active"
            )

        rules = INSTRUMENT_RULES.get(symbol)
        if not rules:
            return SizingResult(
                False, Decimal("0"), entry_price, Decimal("0"),
                Decimal("0"), Decimal("0"), Decimal("0"),
                f"No rules for {symbol}"
            )

        min_order_qty = rules["minOrderQty"]
        qty_step = rules["qtyStep"]
        min_notional = rules["minNotionalValue"]
        tick_size = rules["tickSize"]

        # Stop distance = K * ATR
        stop_dist = Decimal(str(k_mult)) * atr
        if stop_dist <= 0:
            return SizingResult(
                False, Decimal("0"), entry_price, Decimal("0"),
                Decimal("0"), Decimal("0"), Decimal("0"),
                "Zero stop distance"
            )

        # Stop price with tickSize rounding
        if direction == 1:
            raw_stop = entry_price - stop_dist
            stop_price = (raw_stop / tick_size).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick_size
        else:
            raw_stop = entry_price + stop_dist
            # For short, round up (ceil) = floor + 1 tick if there's remainder
            stop_price = (raw_stop / tick_size).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick_size
            if stop_price < raw_stop:
                stop_price += tick_size

        actual_stop_dist = abs(entry_price - stop_price)

        # Expected funding for max hold (15 periods of 8h for 120h, 12 for 96h)
        funding_periods = Decimal("12")  # 96h / 8h
        expected_funding = abs(current_funding_rate) * funding_periods * entry_price

        # Risk per unit
        risk_per_unit = (
            actual_stop_dist
            + Decimal("2") * fee_rate * entry_price
            + Decimal("2") * slippage_rate * entry_price
            + expected_funding
        )

        if risk_per_unit <= 0:
            return SizingResult(
                False, Decimal("0"), entry_price, stop_price,
                Decimal("0"), Decimal("0"), Decimal("0"),
                "Invalid risk per unit"
            )

        # Risk budget
        risk_budget = self.equity * self.current_risk_pct

        # Size by risk budget
        q_by_risk = risk_budget / risk_per_unit

        # Max notional
        max_notional = self.equity * self.profile.max_notional_leverage
        q_by_notional = max_notional / entry_price

        # Unconstrained qty
        q_raw = min(q_by_risk, q_by_notional)

        # Round down to qtyStep
        steps = (q_raw / qty_step).quantize(Decimal("1"), rounding=ROUND_FLOOR)
        q = steps * qty_step

        # Exchange filters
        if q < min_order_qty:
            self.trades_skipped_min_size += 1
            return SizingResult(
                False, Decimal("0"), entry_price, stop_price,
                Decimal("0"), self.current_risk_pct, Decimal("0"),
                f"Qty {q} below minOrderQty {min_order_qty}"
            )

        notional = q * entry_price
        if notional < min_notional:
            self.trades_skipped_min_size += 1
            return SizingResult(
                False, Decimal("0"), entry_price, stop_price,
                Decimal("0"), self.current_risk_pct, Decimal("0"),
                f"Notional {notional} below minNotionalValue {min_notional}"
            )

        # Re-check risk after rounding
        actual_risk = q * risk_per_unit
        if actual_risk > risk_budget * Decimal("1.0001"):
            self.trades_skipped_risk_budget += 1
            return SizingResult(
                False, Decimal("0"), entry_price, stop_price,
                actual_risk, self.current_risk_pct, notional,
                f"Actual risk {actual_risk} exceeds budget {risk_budget}"
            )

        if notional > max_notional * Decimal("1.0001"):
            return SizingResult(
                False, Decimal("0"), entry_price, stop_price,
                actual_risk, self.current_risk_pct, notional,
                f"Notional {notional} exceeds max {max_notional}"
            )

        return SizingResult(
            allowed=True,
            qty=q,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_amount=actual_risk,
            risk_pct=self.current_risk_pct,
            notional=notional,
        )
