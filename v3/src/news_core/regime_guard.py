"""
Regime Guard: Global Trading Embargo and Safety Filter.
Protects the bot against macro shock events and systemic market crises:
1. Macro Blackout: Blocks new entries ±15m around FOMC, CPI, NFP releases.
2. Stablecoin Depeg: Blocks new entries if USDC/USDT deviates by > 0.8% from 1.0000.
3. Safe Haven Gold Surge: Blocks entries if PAXG spikes > 0.7% in 1 minute.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

try:
    from .models import RegimeStatus
    from .news_config import NewsConfig
except (ImportError, ValueError):
    from models import RegimeStatus
    from news_config import NewsConfig


class RegimeGuard:
    """Monitors systemic risks and issues global or symbol-specific trading embargoes."""

    def __init__(self, config: Optional[NewsConfig] = None):
        self.config = config or NewsConfig()
        
        # Scheduled high-impact macro events: list of (datetime_utc, name)
        self._macro_events: List[tuple[datetime, str]] = []
        
        # State tracking
        self._last_usdc_price: float = 1.0000
        self._last_paxg_spike_time: Optional[datetime] = None
        self._manual_block: bool = False
        self._manual_reason: str = ""

        # Pre-populate known high-impact macro anchor dates for 2024-2026
        self._load_baseline_macro_schedule()

    def _load_baseline_macro_schedule(self) -> None:
        """Baseline schedule for major 2024-2026 releases (FOMC, CPI, NFP)."""
        # Formatted sample dates (in production/backtest, loaded via ForexFactory/BLS connector)
        sample_schedule = [
            # 2024 Examples
            ("2024-01-05 13:30:00", "US NFP (Employment Situation)"),
            ("2024-01-11 13:30:00", "US CPI Release"),
            ("2024-01-31 19:00:00", "Fed FOMC Rate Decision"),
            ("2024-02-02 13:30:00", "US NFP"),
            ("2024-02-13 13:30:00", "US CPI Release"),
            ("2024-03-20 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-05-01 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-06-12 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-09-18 18:00:00", "Fed FOMC Rate Decision"),
            ("2024-11-07 19:00:00", "Fed FOMC Rate Decision"),
            ("2024-12-18 19:00:00", "Fed FOMC Rate Decision"),
            # 2025 Examples
            ("2025-01-29 19:00:00", "Fed FOMC Rate Decision"),
            ("2025-03-19 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-05-07 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-06-18 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-09-17 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-10-29 18:00:00", "Fed FOMC Rate Decision"),
            ("2025-12-10 19:00:00", "Fed FOMC Rate Decision"),
            # 2026 Examples
            ("2026-01-28 19:00:00", "Fed FOMC Rate Decision"),
            ("2026-03-18 18:00:00", "Fed FOMC Rate Decision"),
            ("2026-05-06 18:00:00", "Fed FOMC Rate Decision"),
            ("2026-06-17 18:00:00", "Fed FOMC Rate Decision"),
            ("2026-09-16 18:00:00", "Fed FOMC Rate Decision"),
        ]
        for dt_str, name in sample_schedule:
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

        # 1. Macro Blackout Window (±15 minutes)
        window = timedelta(minutes=self.config.macro_blackout_minutes)
        for event_time, name in self._macro_events:
            diff = abs(now - event_time)
            if diff <= window:
                minutes_delta = diff.total_seconds() / 60.0
                return RegimeStatus(
                    block_new_entries=True,
                    reason=f"Macro blackout: {name} in ±{self.config.macro_blackout_minutes}m window (delta: {minutes_delta:.1f}m)",
                    details={"event": name, "event_time": event_time.isoformat()}
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
