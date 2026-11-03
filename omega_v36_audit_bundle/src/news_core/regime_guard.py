"""
Regime Guard: Global Trading Embargo and Safety Filter (News Core v1.2).
Protects the bot against macro shock events and systemic market crises:
1. Macro Lockout: Blocks new entries in [-45m : +30m] window around FOMC, CPI, NFP, Core PCE releases.
2. Pre-Macro Profit Trimming: Banks 30% profit at T-45m if floating profit >= 1.0R, keeping remaining 70% with stop intact.
3. Stablecoin Depeg: Blocks new entries if USDC/USDT deviates by > 0.8% from 1.0000.
4. Safe Haven Gold Surge: Blocks entries if PAXG spikes > 0.7% in 1 minute.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Union, Set, Tuple

try:
    from .models import RegimeStatus, PositionView, PositionAction, ActionType, Side
    from .news_config import NewsConfig
except (ImportError, ValueError):
    from models import RegimeStatus, PositionView, PositionAction, ActionType, Side
    from news_config import NewsConfig


class RegimeGuard:
    """Monitors systemic risks and issues global or symbol-specific trading embargoes."""

    def __init__(self, config: Optional[NewsConfig] = None):
        self.config = config or NewsConfig()
        
        # Scheduled high-impact macro events: list of (datetime_utc, name)
        self._macro_events: List[tuple[datetime, str]] = []
        
        # Deduplication for pre-macro profit trimming: set of (event_key, symbol)
        self._trimmed_macro_events: Set[Tuple[str, str]] = set()

        # State tracking
        self._last_usdc_price: float = 1.0000
        self._last_paxg_spike_time: Optional[datetime] = None
        self._manual_block: bool = False
        self._manual_reason: str = ""

        # Pre-populate known high-impact macro anchor dates for 2024-2026
        self._load_baseline_macro_schedule()

    def _load_baseline_macro_schedule(self) -> None:
        """Baseline schedule for major 2024-2026 releases (FOMC, CPI, NFP, Core PCE)."""
        schedule = [
            # ---------------- 2024 High-Impact Macro Schedule ----------------
            # Fed FOMC Rate Decisions (18:00 / 19:00 UTC)
            ("2024-01-31 19:00:00", "Fed FOMC Rate Decision"),
            ("2024-03-20 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-05-01 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-06-12 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-07-31 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-09-18 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-11-07 19:00:00", "Fed FOMC Rate Decision"),
            ("2024-12-18 19:00:00", "Fed FOMC Rate Decision"),
            # US CPI Releases (12:30 / 13:30 UTC)
            ("2024-01-11 13:30:00", "US CPI Release"),
            ("2024-02-13 13:30:00", "US CPI Release"),
            ("2024-03-12 12:30:00", "US CPI Release"),
            ("2024-04-10 12:30:00", "US CPI Release"),
            ("2024-05-15 12:30:00", "US CPI Release"),
            ("2024-06-12 12:30:00", "US CPI Release"),
            ("2024-07-11 12:30:00", "US CPI Release"),
            ("2024-08-14 12:30:00", "US CPI Release"),
            ("2024-09-11 12:30:00", "US CPI Release"),
            ("2024-10-10 12:30:00", "US CPI Release"),
            ("2024-11-13 13:30:00", "US CPI Release"),
            ("2024-12-11 13:30:00", "US CPI Release"),
            # US NFP Releases (12:30 / 13:30 UTC)
            ("2024-01-05 13:30:00", "US NFP (Employment Situation)"),
            ("2024-02-02 13:30:00", "US NFP (Employment Situation)"),
            ("2024-03-08 13:30:00", "US NFP (Employment Situation)"),
            ("2024-04-05 12:30:00", "US NFP (Employment Situation)"),
            ("2024-05-03 12:30:00", "US NFP (Employment Situation)"),
            ("2024-06-07 12:30:00", "US NFP (Employment Situation)"),
            ("2024-07-05 12:30:00", "US NFP (Employment Situation)"),
            ("2024-08-02 12:30:00", "US NFP (Employment Situation)"),
            ("2024-09-06 12:30:00", "US NFP (Employment Situation)"),
            ("2024-10-04 12:30:00", "US NFP (Employment Situation)"),
            ("2024-11-01 12:30:00", "US NFP (Employment Situation)"),
            ("2024-12-06 13:30:00", "US NFP (Employment Situation)"),
            # US Core PCE Releases (12:30 / 13:30 UTC)
            ("2024-01-26 13:30:00", "US Core PCE Price Index"),
            ("2024-02-29 13:30:00", "US Core PCE Price Index"),
            ("2024-03-29 12:30:00", "US Core PCE Price Index"),
            ("2024-04-26 12:30:00", "US Core PCE Price Index"),
            ("2024-05-31 12:30:00", "US Core PCE Price Index"),
            ("2024-06-28 12:30:00", "US Core PCE Price Index"),
            ("2024-07-26 12:30:00", "US Core PCE Price Index"),
            ("2024-08-30 12:30:00", "US Core PCE Price Index"),
            ("2024-09-27 12:30:00", "US Core PCE Price Index"),
            ("2024-10-31 12:30:00", "US Core PCE Price Index"),
            ("2024-11-27 13:30:00", "US Core PCE Price Index"),
            ("2024-12-20 13:30:00", "US Core PCE Price Index"),

            # ---------------- 2025 High-Impact Macro Schedule ----------------
            # Fed FOMC Rate Decisions
            ("2025-01-29 19:00:00", "Fed FOMC Rate Decision"),
            ("2025-03-19 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-05-07 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-06-18 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-07-30 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-09-17 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-10-29 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-12-10 19:00:00", "Fed FOMC Rate Decision"),
            # US CPI Releases
            ("2025-01-15 13:30:00", "US CPI Release"),
            ("2025-02-12 13:30:00", "US CPI Release"),
            ("2025-03-12 12:30:00", "US CPI Release"),
            ("2025-04-10 12:30:00", "US CPI Release"),
            ("2025-05-14 12:30:00", "US CPI Release"),
            ("2025-06-11 12:30:00", "US CPI Release"),
            ("2025-07-11 12:30:00", "US CPI Release"),
            ("2025-08-13 12:30:00", "US CPI Release"),
            ("2025-09-10 12:30:00", "US CPI Release"),
            ("2025-10-15 12:30:00", "US CPI Release"),
            ("2025-11-12 13:30:00", "US CPI Release"),
            ("2025-12-10 13:30:00", "US CPI Release"),
            # US NFP Releases
            ("2025-01-10 13:30:00", "US NFP (Employment Situation)"),
            ("2025-02-07 13:30:00", "US NFP (Employment Situation)"),
            ("2025-03-07 13:30:00", "US NFP (Employment Situation)"),
            ("2025-04-04 12:30:00", "US NFP (Employment Situation)"),
            ("2025-05-02 12:30:00", "US NFP (Employment Situation)"),
            ("2025-06-06 12:30:00", "US NFP (Employment Situation)"),
            ("2025-07-04 12:30:00", "US NFP (Employment Situation)"),
            ("2025-08-01 12:30:00", "US NFP (Employment Situation)"),
            ("2025-09-05 12:30:00", "US NFP (Employment Situation)"),
            ("2025-10-03 12:30:00", "US NFP (Employment Situation)"),
            ("2025-11-07 13:30:00", "US NFP (Employment Situation)"),
            ("2025-12-05 13:30:00", "US NFP (Employment Situation)"),
            # US Core PCE Releases
            ("2025-01-31 13:30:00", "US Core PCE Price Index"),
            ("2025-02-28 13:30:00", "US Core PCE Price Index"),
            ("2025-03-28 12:30:00", "US Core PCE Price Index"),
            ("2025-04-25 12:30:00", "US Core PCE Price Index"),
            ("2025-05-30 12:30:00", "US Core PCE Price Index"),
            ("2025-06-27 12:30:00", "US Core PCE Price Index"),
            ("2025-07-25 12:30:00", "US Core PCE Price Index"),
            ("2025-08-29 12:30:00", "US Core PCE Price Index"),
            ("2025-09-26 12:30:00", "US Core PCE Price Index"),
            ("2025-10-31 12:30:00", "US Core PCE Price Index"),
            ("2025-11-26 13:30:00", "US Core PCE Price Index"),
            ("2025-12-23 13:30:00", "US Core PCE Price Index"),

            # ---------------- 2026 High-Impact Macro Schedule ----------------
            # Fed FOMC Rate Decisions
            ("2026-01-28 19:00:00", "Fed FOMC Rate Decision"),
            ("2026-03-18 18:00:00", "Fed FOMC Rate Decision"),
            ("2026-05-06 18:00:00", "Fed FOMC Rate Decision"),
            ("2026-06-17 18:00:00", "Fed FOMC Rate Decision"),
            ("2026-07-29 18:00:00", "Fed FOMC Rate Decision"),
            ("2026-09-16 18:00:00", "Fed FOMC Rate Decision"),
            ("2026-11-04 19:00:00", "Fed FOMC Rate Decision"),
            ("2026-12-16 19:00:00", "Fed FOMC Rate Decision"),
            # US CPI Releases
            ("2026-01-14 13:30:00", "US CPI Release"),
            ("2026-02-11 13:30:00", "US CPI Release"),
            ("2026-03-11 12:30:00", "US CPI Release"),
            ("2026-04-14 12:30:00", "US CPI Release"),
            ("2026-05-13 12:30:00", "US CPI Release"),
            ("2026-06-10 12:30:00", "US CPI Release"),
            ("2026-07-15 12:30:00", "US CPI Release"),
            ("2026-08-12 12:30:00", "US CPI Release"),
            ("2026-09-16 12:30:00", "US CPI Release"),
            ("2026-10-14 12:30:00", "US CPI Release"),
            ("2026-11-12 13:30:00", "US CPI Release"),
            ("2026-12-10 13:30:00", "US CPI Release"),
            # US NFP Releases
            ("2026-01-09 13:30:00", "US NFP (Employment Situation)"),
            ("2026-02-06 13:30:00", "US NFP (Employment Situation)"),
            ("2026-03-06 13:30:00", "US NFP (Employment Situation)"),
            ("2026-04-03 12:30:00", "US NFP (Employment Situation)"),
            ("2026-05-08 12:30:00", "US NFP (Employment Situation)"),
            ("2026-06-05 12:30:00", "US NFP (Employment Situation)"),
            ("2026-07-02 12:30:00", "US NFP (Employment Situation)"),
            ("2026-08-07 12:30:00", "US NFP (Employment Situation)"),
            ("2026-09-04 12:30:00", "US NFP (Employment Situation)"),
            ("2026-10-02 12:30:00", "US NFP (Employment Situation)"),
            ("2026-11-06 13:30:00", "US NFP (Employment Situation)"),
            ("2026-12-04 13:30:00", "US NFP (Employment Situation)"),
            # US Core PCE Releases
            ("2026-01-30 13:30:00", "US Core PCE Price Index"),
            ("2026-02-27 13:30:00", "US Core PCE Price Index"),
            ("2026-03-27 12:30:00", "US Core PCE Price Index"),
            ("2026-04-24 12:30:00", "US Core PCE Price Index"),
            ("2026-05-29 12:30:00", "US Core PCE Price Index"),
            ("2026-06-26 12:30:00", "US Core PCE Price Index"),
            ("2026-07-31 12:30:00", "US Core PCE Price Index"),
            ("2026-08-28 12:30:00", "US Core PCE Price Index"),
            ("2026-09-25 12:30:00", "US Core PCE Price Index"),
            ("2026-10-30 12:30:00", "US Core PCE Price Index"),
            ("2026-11-25 13:30:00", "US Core PCE Price Index"),
            ("2026-12-23 13:30:00", "US Core PCE Price Index"),
        ]
        for dt_str, name in schedule:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            self._macro_events.append((dt, name))

    def add_macro_event(self, event_time_utc: datetime, event_name: str) -> None:
        """Register a macro event in the calendar."""
        if event_time_utc.tzinfo is None:
            event_time_utc = event_time_utc.replace(tzinfo=timezone.utc)
        self._macro_events.append((event_time_utc, event_name))

    def update_stablecoin_price(self, price: float) -> None:
        """Update USDC/USDT reference price for depeg detection."""
        self._last_usdc_price = price

    def record_paxg_spike(self, timestamp: datetime, return_1m: float) -> None:
        """Record a sharp gold / safe-haven spike."""
        if return_1m >= self.config.paxg_spike_threshold_1m:
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            self._last_paxg_spike_time = timestamp

    def set_manual_block(self, blocked: bool, reason: str = "") -> None:
        """Manually toggle regime block."""
        self._manual_block = blocked
        self._manual_reason = reason

    def check_status(self, symbol: str, current_time: Optional[datetime] = None) -> RegimeStatus:
        """
        Check if new trade entries should be blocked.
        Returns RegimeStatus(block_new_entries=bool, reason=str).
        """
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # 0. Manual block override
        if self._manual_block:
            return RegimeStatus(
                block_new_entries=True,
                reason=f"Manual trading embargo: {self._manual_reason}",
                details={"source": "manual"}
            )

        # 1. Macro Blackout Window (asymmetric [-before_min : +after_min] in v1.2)
        before_min = getattr(self.config, "macro_lockout_before_minutes", self.config.macro_blackout_minutes)
        after_min = getattr(self.config, "macro_lockout_after_minutes", self.config.macro_blackout_minutes)
        window_before = timedelta(minutes=before_min)
        window_after = timedelta(minutes=after_min)

        for event_time, name in self._macro_events:
            # FOMC press conference & Q&A lasts ~60 min after rate release; expand post-window to +90 min
            is_fomc = "FOMC" in name.upper() or "RATE" in name.upper()
            after_m = 90 if is_fomc else after_min
            start_window = event_time - window_before
            end_window = event_time + timedelta(minutes=after_m)
            if start_window <= now <= end_window:
                delta_sec = (now - event_time).total_seconds()
                delta_min = delta_sec / 60.0
                return RegimeStatus(
                    block_new_entries=True,
                    reason=f"Macro blackout: {name} in [-{before_min}m : +{after_m}m] window (delta: {delta_min:+.1f}m)",
                    details={
                        "event": name,
                        "event_time": event_time.isoformat(),
                        "window_start": start_window.isoformat(),
                        "window_end": end_window.isoformat(),
                        "delta_minutes": round(delta_min, 1),
                    }
                )

        # 2. Stablecoin Depeg Check
        if self.config.enable_stablecoin_guard:
            depeg_deviation = abs(self._last_usdc_price - 1.0)
            if depeg_deviation >= self.config.stablecoin_depeg_threshold:
                return RegimeStatus(
                    block_new_entries=True,
                    reason=f"Systemic stablecoin depeg: USDC/USDT at {self._last_usdc_price:.4f} (dev: {depeg_deviation*100:.2f}%)",
                    details={"usdc_price": self._last_usdc_price, "deviation": depeg_deviation}
                )

        # 3. Gold (PAXG) Flight to Safety Check
        if self.config.enable_paxg_guard and self._last_paxg_spike_time:
            time_since_spike = (now - self._last_paxg_spike_time).total_seconds() / 60.0
            if time_since_spike <= 30.0:  # Block for 30 minutes following extreme gold spike
                return RegimeStatus(
                    block_new_entries=True,
                    reason=f"Risk-off crisis: PAXG safe-haven spike occurred {time_since_spike:.1f}m ago",
                    details={"spike_time": self._last_paxg_spike_time.isoformat()}
                )

        # All clear
        return RegimeStatus(block_new_entries=False, reason=None)

    def evaluate_macro_trimming(
        self,
        open_positions: Union[List[PositionView], Dict[str, PositionView]],
        current_prices: Optional[Dict[str, float]] = None,
        current_time: Optional[datetime] = None,
    ) -> List[PositionAction]:
        """
        Evaluate Pre-Macro Profit Trimming at T-45m for active positions (News Core v1.2).
        If a position has floating profit >= 1.0R (or >= 1.0 ATR), banks 30% profit via PARTIAL_TRIM.
        Keeps the remaining 70% of the position with original stop loss intact.
        Guarantees deduplication: exactly one trim per symbol per scheduled macro event.
        """
        actions: List[PositionAction] = []
        if not getattr(self.config, "enable_macro_profit_trimming", True):
            return actions

        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        prices = current_prices or {}
        before_min = getattr(self.config, "macro_lockout_before_minutes", 45)
        trim_ratio = getattr(self.config, "macro_trim_ratio", 0.30)
        min_r = getattr(self.config, "macro_trim_min_r", 1.0)

        # Standardize open_positions
        pos_list: List[PositionView] = (
            list(open_positions.values()) if isinstance(open_positions, dict) else list(open_positions)
        )

        for event_time, name in self._macro_events:
            # Check if now is in the pre-macro window [event_time - before_min, event_time]
            start_trim_window = event_time - timedelta(minutes=before_min)
            if start_trim_window <= now < event_time:
                event_key = f"{event_time.isoformat()}_{name}"
                mins_to_event = (event_time - now).total_seconds() / 60.0

                for pos in pos_list:
                    if pos.size <= 0:
                        continue
                    sym = pos.symbol
                    dedup_key = (event_key, sym)
                    if dedup_key in self._trimmed_macro_events:
                        continue

                    curr_price = pos.current_price or prices.get(sym, pos.entry_price)
                    if curr_price <= 0:
                        continue

                    pos_side = Side.from_str(pos.side)
                    risk_dist = abs(pos.entry_price - pos.stop_loss) if pos.stop_loss > 0 else (pos.entry_price * 0.015)

                    if pos.unrealized_r is not None:
                        r_mult = pos.unrealized_r
                    elif pos.unrealized_pnl_r is not None:
                        r_mult = pos.unrealized_pnl_r
                    else:
                        if pos_side == Side.LONG:
                            r_mult = (curr_price - pos.entry_price) / risk_dist if risk_dist > 0 else 0.0
                        else:
                            r_mult = (pos.entry_price - curr_price) / risk_dist if risk_dist > 0 else 0.0

                    # Check floating profit qualification (>= 1.0R)
                    if r_mult >= min_r:
                        is_major_event = any(k in name.upper() for k in ["FOMC", "RATE", "CPI"])
                        active_trim_ratio = 0.40 if is_major_event else trim_ratio
                        trim_size = pos.size * active_trim_ratio
                        self._trimmed_macro_events.add(dedup_key)
                        be_stop = pos.entry_price * (1.002 if pos_side == Side.LONG else 0.998)

                        actions.append(
                            PositionAction(
                                action_type=ActionType.PARTIAL_TRIM,
                                symbol=sym,
                                side=Side.SHORT if pos_side == Side.LONG else Side.LONG,  # reduceOnly direction
                                size=trim_size,
                                stop_price=be_stop,  # Ratchet stop to Breakeven (+0.2%)
                                reason=(
                                    f"MACRO_PRE_TRIM: {name} in {mins_to_event:.1f}m. "
                                    f"Banked {active_trim_ratio*100:.0f}% profit at {r_mult:.2f}R. "
                                    f"Preserving {(1.0-active_trim_ratio)*100:.0f}% with SL moved to Breakeven at {be_stop:.4f}."
                                ),
                                urgency="HIGH",
                                created_at=now,
                            )
                        )

        return actions

