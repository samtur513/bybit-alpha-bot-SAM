"""
Layer 4: Offensive Order Generation Engine.
Generates opportunistic, risk-controlled directional orders upon confirmed news:
- N1 Pullback: Limit post-only order on ~45% retracement of the impulse bar (no FOMO chase).
- N2 Sympathy: Eco-system follower trade (disabled by default).
- N3 Crash Short: Momentum short on confirmed crash shocks.
- Sizing strictly bounded by max_news_risk_pct.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, List

try:
    from .models import OrderIntent, Side
    from .news_config import NewsConfig
except (ImportError, ValueError):
    from models import OrderIntent, Side
    from news_config import NewsConfig


class OffensiveEngine:
    """Generates directional entry orders when market confirmation triggers."""

    def __init__(self, config: Optional[NewsConfig] = None):
        self.config = config or NewsConfig()

    def generate_n1_pullback(
        self,
        symbol: str,
        high: float,
        low: float,
        side: Side,
        deposit: float = 1000.0,
        timestamp: Optional[datetime] = None
    ) -> Optional[OrderIntent]:
        """
        Generate N1 Pullback order.
        Places a post-only limit order waiting for ~45% pullback of the impulse candle.
        """
        if not self.config.enable_n1_pullback:
            return None

        if high <= low or low <= 0:
            return None

        now = timestamp or datetime.now(timezone.utc)
        side_val = Side.from_str(side)
        impulse_range = high - low

        if side_val == Side.LONG:
            # For LONG: limit buy at High - 45% of range
            limit_price = high - (impulse_range * self.config.pullback_ratio)
            stop_loss = low * (1.0 - self.config.n1_stop_loss_pct)
            
            # Risk per unit
            risk_per_unit = limit_price - stop_loss
            if risk_per_unit <= 0:
                return None
            
            # Size calculation based on max_news_risk_pct
            max_capital_risk = deposit * self.config.max_news_risk_pct
            size = max_capital_risk / risk_per_unit

            return OrderIntent(
                symbol=symbol,
                side=Side.LONG,
                order_type="LIMIT",
                limit_price=round(limit_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit=round(high + impulse_range * 0.5, 4),
                size=round(size, 4),
                post_only=True,
                strategy_tag="N1_PULLBACK",
                reason=f"N1 Pullback LONG at 45% retrace ({limit_price:.4f}) of impulse [{low:.4f}..{high:.4f}]",
                created_at=now,
            )
        else:
            # For SHORT: limit sell at Low + 45% of range
            limit_price = low + (impulse_range * self.config.pullback_ratio)
            stop_loss = high * (1.0 + self.config.n1_stop_loss_pct)

            risk_per_unit = stop_loss - limit_price
            if risk_per_unit <= 0:
                return None

            max_capital_risk = deposit * self.config.max_news_risk_pct
            size = max_capital_risk / risk_per_unit

            return OrderIntent(
                symbol=symbol,
                side=Side.SHORT,
                order_type="LIMIT",
                limit_price=round(limit_price, 4),
                stop_loss=round(stop_loss, 4),
                take_profit=round(low - impulse_range * 0.5, 4),
                size=round(size, 4),
                post_only=True,
                strategy_tag="N1_PULLBACK",
                reason=f"N1 Pullback SHORT at 45% retrace ({limit_price:.4f}) of impulse [{low:.4f}..{high:.4f}]",
                created_at=now,
            )

    def generate_n3_crash_short(
        self,
        symbol: str,
        current_price: float,
        impulse_high: float,
        deposit: float = 1000.0,
        timestamp: Optional[datetime] = None
    ) -> Optional[OrderIntent]:
        """
        Generate N3 Crash Short order.
        Aggressive short on confirmed exploit/delist/crash shock.
        """
        if not self.config.enable_n3_crash_short:
            return None

        if current_price <= 0:
            return None

        now = timestamp or datetime.now(timezone.utc)
        stop_loss = max(impulse_high, current_price * 1.01) * (1.0 + self.config.n3_stop_loss_pct)
        risk_per_unit = stop_loss - current_price

        if risk_per_unit <= 0:
            return None

        max_capital_risk = deposit * self.config.max_news_risk_pct
        size = max_capital_risk / risk_per_unit

        return OrderIntent(
            symbol=symbol,
            side=Side.SHORT,
            order_type="MARKET",
            limit_price=current_price,
            stop_loss=round(stop_loss, 4),
            take_profit=round(current_price * 0.90, 4),  # Target ~10% drop
            size=round(size, 4),
            post_only=False,
            strategy_tag="N3_CRASH_SHORT",
            reason=f"N3 Crash Short at {current_price:.4f} with SL at {stop_loss:.4f}",
            created_at=now,
        )

    def get_sympathy_symbols(self, primary_symbol: str) -> List[str]:
        """Return ecosystem sympathy tokens if enabled."""
        if not self.config.enable_n2_sympathy:
            return []
        
        base_coin = primary_symbol.replace("USDT", "").replace("PERP", "")
        return [f"{s}USDT" for s in self.config.sympathy_map.get(base_coin, [])]
