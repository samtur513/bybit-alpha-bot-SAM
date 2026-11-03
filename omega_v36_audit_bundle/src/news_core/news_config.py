"""
Configuration dataclass for News Core.
Fully configurable parameters for all 4 layers, Regime Guard, and backtest execution.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class NewsConfig:
    # News Core Version
    core_version: str = "1.2"

    # --- Layer 1: Classification & Deduplication ---
    min_score_to_act: int = 2
    cooldown_minutes: int = 45

    # --- Layer 2: Market Confirmation ---
    vol_multiplier: float = 3.0          # 1m volume >= 3.0x median 60m volume
    min_abs_return_1m: float = 0.006     # 0.6% price move in news direction
    watch_window_minutes: int = 15       # Window to wait for confirmation before expiring
    require_market_confirmation: bool = True  # If False, acts immediately (unsafe, test only)

    # --- Layer 3: Position Management Matrix & v1.1/v1.2 Anti-False-Positive Guards ---
    enable_emergency_eject: bool = True       # Long + CRASH_SHOCK (-3) -> close market
    emergency_eject_ratio: float = 1.0        # 1.0 = 100% close (v1.1 default) or 0.50 (skill std)
    enable_squeeze_escape: bool = True        # Short + MAJOR_CATALYST (+3) -> close 100% market
    enable_super_runner: bool = True          # Long + catalyst + profit >= 0.8R -> stop to breakeven +0.3%
    super_runner_r_threshold: float = 0.8     # Profit threshold in R multiples
    super_runner_breakeven_offset_pct: float = 0.003  # Breakeven +0.3% (0.003)
    enable_pyramid: bool = False              # Pyramid доливка (disabled by default)

    # v1.1 False-Positive Protections for Exits
    require_price_drop_for_eject: bool = True # Never eject on text alone without market drop
    eject_min_drop_1m: float = 0.010          # Require >= 1.0% drop for emergency eject
    eject_vol_multiplier: float = 3.0         # Volume confirmation for drop
    soft_defense_for_media: bool = True       # Media (Tier 3) -> only move stop, no market close
    soft_stop_offset_pct: float = 0.005       # 0.5% below current price for soft defense
    protect_runners_min_r: float = 1.5        # If position R >= 1.5, never market-eject; trail stop
    runner_trail_r: float = 0.5               # Trail to entry + 0.5R when protecting runner

    # --- Layer 4: Offensive Entries (Optional) ---
    enable_n1_pullback: bool = True           # Pullback limit order at ~45% of impulse candle
    pullback_ratio: float = 0.45              # 45% retracement
    n1_stop_loss_pct: float = 0.015           # 1.5% SL below impulse low/above impulse high
    enable_n2_sympathy: bool = False          # Correlated ecosystem coin entry (disabled by default)
    sympathy_map: Dict[str, List[str]] = field(default_factory=lambda: {
        "SOL": ["RAY", "JTO", "JUP"],
        "ETH": ["ARB", "OP", "LDO"],
        "BTC": ["STX", "ORDI"],
        "AVAX": ["JOE"],
    })
    enable_n3_crash_short: bool = True        # Short upon confirmed negative shock
    n3_stop_loss_pct: float = 0.02            # 2.0% SL
    enable_slot_preemption: bool = False      # Force-closing other positions for news trade (disabled)
    max_news_risk_pct: float = 0.10           # Max 10% risk/size allocation of balance

    # --- Regime Guard & Macro Lockout v1.2 ---
    macro_blackout_minutes: Optional[int] = None # Legacy parameter: if explicitly passed, sets symmetric window
    macro_lockout_before_minutes: int = 45    # [-45m] pre-event lockout (liquidity pull-out)
    macro_lockout_after_minutes: int = 30     # [+30m] post-event lockout (volatility settle)
    enable_macro_profit_trimming: bool = True # Bank 30% profit on runners >= 1.0R at T-45m
    macro_trim_ratio: float = 0.30            # 30% partial close
    macro_trim_min_r: float = 1.0             # Minimum floating profit in R multiples to qualify
    macro_trim_min_atr: float = 1.0           # Minimum floating profit in ATR multiples to qualify
    enable_stablecoin_guard: bool = True      # Guard against USDC/USDT depeg
    stablecoin_depeg_threshold: float = 0.008 # 0.8% deviation from $1.00
    enable_paxg_guard: bool = True            # Guard against gold risk-off flight
    paxg_spike_threshold_1m: float = 0.007    # 0.7% 1m spike in PAXG

    def __post_init__(self):
        if self.macro_blackout_minutes is not None:
            self.macro_lockout_before_minutes = self.macro_blackout_minutes
            self.macro_lockout_after_minutes = self.macro_blackout_minutes

    # --- Execution & Cost Models ---
    cost_model: str = "BASE"                  # "BASE" (5.3 bp), "STRESS" (8.5 bp), "NEWS_STRESS" (x3 in ±5m)
    latency_market_ms: int = 150              # Market data delivery latency
    latency_compute_ms: int = 10              # News Core compute latency
    latency_order_ms: int = 150               # Order execution round-trip latency

    @classmethod
    def defensive_only(cls) -> "NewsConfig":
        """Pre-configured profile for purely defensive operations (no offensive orders)."""
        return cls(
            enable_n1_pullback=False,
            enable_n2_sympathy=False,
            enable_n3_crash_short=False,
            enable_emergency_eject=True,
            enable_squeeze_escape=True,
            enable_super_runner=True,
        )

    @classmethod
    def defense_plus_n1(cls) -> "NewsConfig":
        """Pre-configured profile for defense + N1 pullback orders only."""
        return cls(
            enable_n1_pullback=True,
            enable_n2_sympathy=False,
            enable_n3_crash_short=False,
            enable_emergency_eject=True,
            enable_squeeze_escape=True,
            enable_super_runner=True,
        )
