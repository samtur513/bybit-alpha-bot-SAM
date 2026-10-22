"""
Layer 3: Open Position Management Matrix (v1.1 with False-Positive Defense).
Evaluates news shock impact against active positions with 4-tier protection:
1. Price Drop Gate: Emergency Eject requires ≥1.0% drop on volume.
2. Source Tiering: Tier 3 (Media/Twitter) uses soft defense, not market close.
3. Runner Protection: If R ≥ 1.5R, never market-eject; trail stop to entry + 0.5R.
4. Soft Defense: Tightens stop loss without dumping market position.
"""

from __future__ import annotations
from typing import List, Dict, Union, Optional
from datetime import datetime, timezone

try:
    from .models import PositionView, PositionAction, ActionType, EventType, Side
    from .news_config import NewsConfig
except (ImportError, ValueError):
    from models import PositionView, PositionAction, ActionType, EventType, Side
    from news_config import NewsConfig


class PositionMatrix:
    """Evaluates open positions against news events and generates defensive actions."""

    def __init__(self, config: Optional[NewsConfig] = None):
        self.config = config or NewsConfig()

    def evaluate(
        self,
        symbol: str,
        score: int,
        event: Union[EventType, str],
        open_positions: Union[List[PositionView], Dict[str, PositionView]],
        current_price: float,
        timestamp: Optional[datetime] = None,
        ret_1m: float = 0.0,
        vol_1m: float = 0.0,
        vol_median_60m: float = 1.0,
        source_tier: int = 3,  # 1=official, 2=onchain, 3=media
    ) -> List[PositionAction]:
        """
        Evaluate news event against open positions for the given symbol.
        Returns a list of PositionAction recommendations.
        """
        actions: List[PositionAction] = []
        now = timestamp or datetime.now(timezone.utc)

        # Standardize open_positions input (dict or list)
        pos: Optional[PositionView] = None
        if isinstance(open_positions, dict):
            pos = open_positions.get(symbol)
        elif isinstance(open_positions, list):
            for p in open_positions:
                if p.symbol == symbol:
                    pos = p
                    break

        if not pos or pos.size <= 0:
            return actions

        pos_side = Side.from_str(pos.side)
        r_multiple = pos.unrealized_r if pos.unrealized_r is not None else (pos.unrealized_pnl_r or pos.calculate_r_multiple())
        event_val = event.value if hasattr(event, "value") else str(event)

        # False-Positive Verification Checks
        price_confirms_panic = (
            ret_1m <= -self.config.eject_min_drop_1m
            and (vol_median_60m <= 0 or vol_1m >= self.config.eject_vol_multiplier * vol_median_60m)
        )
        is_media = source_tier >= 3
        is_strong_runner = r_multiple >= self.config.protect_runners_min_r

        # ------------------------------------------------------------------
        # 1. Emergency Eject (Long + CRASH_SHOCK) with v1.1 Guards
        # ------------------------------------------------------------------
        if (
            self.config.enable_emergency_eject
            and pos_side == Side.LONG
            and (event_val == "CRASH_SHOCK" or score <= -3)
        ):
            # Guard A: Runner Protection (R >= 1.5R) -> trail stop, do not dump market
            if is_strong_runner:
                risk_dist = abs(pos.entry_price - pos.stop_loss) if pos.stop_loss > 0 else (pos.entry_price * 0.015)
                trail_price = pos.entry_price + (self.config.runner_trail_r * risk_dist)
                new_stop = max(pos.stop_loss, trail_price)
                if current_price > 0:
                    new_stop = min(new_stop, current_price * 0.998)

                actions.append(
                    PositionAction(
                        action_type=ActionType.MODIFY_STOP_TP,
                        symbol=symbol,
                        side=Side.LONG,
                        size=pos.size,
                        stop_price=round(new_stop, 4),
                        reason=f"RUNNER_PROTECTION_TRAIL: R={r_multiple:.2f} >= {self.config.protect_runners_min_r}R. Stop trailed to {new_stop:.4f}",
                        urgency="HIGH",
                        created_at=now,
                    )
                )
                return actions

            # Guard B: Soft Defense for Media (Tier 3) without confirmed market dump
            elif self.config.soft_defense_for_media and is_media and not price_confirms_panic:
                soft_stop = max(pos.stop_loss, current_price * (1.0 - self.config.soft_stop_offset_pct))
                if current_price > 0:
                    soft_stop = min(soft_stop, current_price * 0.998)

                actions.append(
                    PositionAction(
                        action_type=ActionType.MODIFY_STOP_TP,
                        symbol=symbol,
                        side=Side.LONG,
                        size=pos.size,
                        stop_price=round(soft_stop, 4),
                        reason=f"SOFT_DEFENSE_MEDIA: Media headline without panic dump. Stop tightened to {soft_stop:.4f}",
                        urgency="MEDIUM",
                        created_at=now,
                    )
                )
                return actions

            # Guard C: Hard Emergency Eject (Official Tier 1 OR Confirmed Price Panic Drop)
            elif (
                not self.config.require_price_drop_for_eject
                or price_confirms_panic
                or source_tier == 1
            ):
                actions.append(
                    PositionAction(
                        action_type=ActionType.CLOSE_MARKET,
                        symbol=symbol,
                        side=Side.SHORT,  # sell to close
                        size=pos.size,
                        reason=f"EMERGENCY_EJECT: CRASH_SHOCK confirmed (tier={source_tier}, ret={ret_1m*100:.1f}%)",
                        urgency="IMMEDIATE",
                        created_at=now,
                    )
                )
                return actions

        # ------------------------------------------------------------------
        # 2. Squeeze Escape: Short + MAJOR_CATALYST (+3)
        # ------------------------------------------------------------------
        elif (
            self.config.enable_squeeze_escape
            and pos_side == Side.SHORT
            and (event_val == "MAJOR_CATALYST" or score >= 2)
        ):
            actions.append(
                PositionAction(
                    action_type=ActionType.CLOSE_MARKET,
                    symbol=symbol,
                    side=Side.LONG,  # buy to close
                    size=pos.size,
                    reason="SHORT_SQUEEZE_ESCAPE",
                    urgency="IMMEDIATE",
                    created_at=now,
                )
            )
            return actions

        # ------------------------------------------------------------------
        # 3. Super-Runner: Long + Positive Catalyst + profit >= 0.8R
        # ------------------------------------------------------------------
        elif (
            self.config.enable_super_runner
            and pos_side == Side.LONG
            and score >= 2
            and r_multiple >= self.config.super_runner_r_threshold
        ):
            new_stop = max(pos.stop_loss, pos.entry_price * (1.0 + self.config.super_runner_breakeven_offset_pct))
            if new_stop > pos.stop_loss and new_stop < current_price:
                actions.append(
                    PositionAction(
                        action_type=ActionType.MODIFY_STOP_TP,
                        symbol=symbol,
                        side=pos_side,
                        size=pos.size,
                        stop_price=round(new_stop, 4),
                        reason=f"SUPER_RUNNER_BE: Profit at {r_multiple:.2f}R. Stop moved to BE +{self.config.super_runner_breakeven_offset_pct*100:.1f}%",
                        urgency="HIGH",
                        created_at=now,
                    )
                )

        return actions
