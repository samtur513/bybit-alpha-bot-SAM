"""
Layer 5: Universal Position Sizer & Dynamic Risk Engine.
Strict mathematical formulation from NewDragon v4.0 Universal Specification.

Fixes all dimensionality mismatch bugs:
- All 4 sizing limiters (True 1R, Fractional Kelly, Risk Parity, CVaR 95%)
  are strictly converted to base currency units (coins) before min().
- Strict minNotional compliance for Bybit V5: NO risk expansion on micro budgets.
"""
import math
from typing import Optional, Tuple
import numpy as np

from core.models import InstrumentSpec, PortfolioState, SignalOutput


class UniversalPositionSizer:
    """
    Universal Position Sizer (Layer 5) with True 1R accounting,
    Fractional Kelly, Risk Parity, CVaR 95% bounds, and Micro-Account Contract Router.
    """

    def __init__(
        self,
        taker_fee: float = 0.00055,       # Bybit VIP0 Taker: 5.5 bps
        maker_fee: float = 0.00020,       # Bybit VIP0 Maker: 2.0 bps
        slippage_base: float = 0.0005,    # 5 bps base adverse slippage
        max_leverage: float = 10.0,
        kelly_fraction: float = 0.25,     # Quarter Kelly
        lambda_corr: float = 0.35,        # Correlation penalty coefficient
    ) -> None:
        self.taker_fee = taker_fee
        self.maker_fee = maker_fee
        self.slippage_base = slippage_base
        self.max_leverage = max_leverage
        self.kelly_fraction = kelly_fraction
        self.lambda_corr = lambda_corr

    @staticmethod
    def get_drawdown_multiplier(current_drawdown: float) -> float:
        """
        Calculates stepped drawdown brake multiplier M_DD:
        - DD < 0.05 -> 1.0 (Normal)
        - 0.05 <= DD < 0.10 -> 0.75 (Caution)
        - 0.10 <= DD < 0.20 -> 0.35 (Severe defence)
        - DD >= 0.20 -> 0.00 (Hard halt / circuit breaker)
        """
        if current_drawdown < 0.05:
            return 1.0
        elif current_drawdown < 0.10:
            return 0.75
        elif current_drawdown < 0.20:
            return 0.35
        else:
            return 0.0

    @staticmethod
    def get_regime_multiplier(regime: str) -> float:
        """Regime risk budget multiplier."""
        regime_lower = regime.lower()
        if regime_lower == "bull":
            return 1.0
        elif regime_lower == "bear":
            return 0.5
        elif regime_lower == "sideways":
            return 0.6
        elif regime_lower == "shock":
            return 0.0
        return 0.3  # Transitional/cautious

    @staticmethod
    def calculate_adaptive_risk_pct(capital: float) -> float:
        """
        Adaptive True 1R risk fraction R_pct(Cap) scaling from 0.5% ($100) to 1.0% ($100k+).
        Formula: 0.005 + 0.005 * clip(log10(Cap / 100) / 3, 0.0, 1.0)
        """
        if capital <= 100.0:
            return 0.005
        log_scale = math.log10(capital / 100.0) / 3.0
        scaled = 0.005 + 0.005 * float(np.clip(log_scale, 0.0, 1.0))
        return scaled

    def compute_unit_loss(
        self,
        entry_price: float,
        stop_price: float,
        is_maker_entry: bool = False,
    ) -> float:
        """
        Exact dollar loss on 1 unit (coin) of base asset:
        L_unit = |P_entry - P_stop| + P_entry * (f_entry + s_expected)
                 + P_stop * (f_exit + s_stress) + P_entry * FundingBuffer
        """
        price_dist = abs(entry_price - stop_price)
        f_entry = self.maker_fee if is_maker_entry else self.taker_fee
        s_expected = self.slippage_base
        f_exit = self.taker_fee  # Stop-loss is market exit
        s_stress = self.slippage_base * 2.0  # Adverse gap slippage
        funding_buffer = 0.00005  # 0.5 bps funding reserve

        l_unit = (
            price_dist
            + entry_price * (f_entry + s_expected)
            + stop_price * (f_exit + s_stress)
            + entry_price * funding_buffer
        )
        return max(l_unit, 1e-8)

    def compute_quantity(
        self,
        signal: SignalOutput,
        spec: InstrumentSpec,
        portfolio: PortfolioState,
        is_maker_entry: bool = False,
    ) -> float:
        """
        Computes discrete, risk-bounded contract quantity in units of base asset (coins).
        Returns 0.0 if rejected by risk boundaries or exchange minNotional.
        """
        capital = portfolio.total_capital
        if capital <= 0.0 or portfolio.available_margin <= 0.0:
            return 0.0

        # 1. Evaluate Circuit Breakers & Risk Budget
        m_dd = self.get_drawdown_multiplier(portfolio.current_drawdown)
        if m_dd <= 0.0:
            return 0.0

        m_regime = self.get_regime_multiplier(portfolio.regime)
        if m_regime <= 0.0:
            return 0.0

        # Correlation Cluster Guard: limit high-correlation positions
        if portfolio.active_correlated_positions >= portfolio.max_correlated_positions:
            return 0.0

        # Portfolio Heat Guard: cap total open risk across all positions
        if portfolio.current_portfolio_heat >= portfolio.max_portfolio_heat:
            return 0.0

        signal_scale = abs(signal.composite_score)
        if signal_scale <= 0.0:
            return 0.0

        r_pct = self.calculate_adaptive_risk_pct(capital)
        r_budget = capital * r_pct * m_regime * m_dd * signal_scale
        if r_budget <= 0.0:
            return 0.0

        # Bound r_budget by remaining portfolio heat capacity
        heat_capacity_usd = max(0.0, (portfolio.max_portfolio_heat - portfolio.current_portfolio_heat) * capital)
        r_budget = min(r_budget, heat_capacity_usd)
        if r_budget <= 0.0:
            return 0.0

        # 2. Bound 1: True 1R Quantity (in coins)
        entry_px = signal.entry_price
        if entry_px <= 0.0:
            return 0.0

        l_unit = self.compute_unit_loss(entry_px, signal.stop_price, is_maker_entry)
        q_true_1r = r_budget / l_unit

        # 3. Bound 2: Fractional Kelly (in coins)
        # f_kelly = clip(K_frac * (mu / var), 0.0, L_max)
        # Q_kelly = (Capital * f_kelly) / P_entry
        eff_leverage = min(self.max_leverage, spec.max_leverage)
        var = max(portfolio.expected_var, 1e-6)
        raw_kelly = self.kelly_fraction * (portfolio.expected_mu / var)
        f_kelly = float(np.clip(raw_kelly, 0.0, eff_leverage))
        q_kelly = (capital * f_kelly) / entry_px

        # 4. Bound 3: Risk Parity with Correlation Penalty (in coins)
        # w_i_RP = (1 / sigma_i) / sum(1 / sigma_j)
        # w_i_adj = w_i_RP * (1 - lambda_corr * avg_rho)
        # Q_RP = (Capital * L_max * w_i_adj) / P_entry
        asset_vol = max(portfolio.asset_volatility, 1e-6)
        inv_vol = 1.0 / asset_vol
        inv_sum = max(portfolio.inv_vol_sum, inv_vol)
        w_rp = inv_vol / inv_sum
        w_adj = max(0.0, w_rp * (1.0 - self.lambda_corr * portfolio.avg_correlation))
        q_rp = (capital * eff_leverage * w_adj) / entry_px

        # 5. Bound 4: Tail Risk CVaR 95% (in coins)
        # Limits potential 95% CVaR loss to at most 2.0 * R_budget
        # Q_CVaR = (2.0 * R_budget) / (P_entry * CVaR_95)
        cvar_95 = max(portfolio.cvar_95_pct, 1e-4)
        q_cvar = (2.0 * r_budget) / (entry_px * cvar_95)

        # 6. Unified Coin-Space Minimum
        q_raw = min(q_true_1r, q_kelly, q_rp, q_cvar)
        if q_raw <= 0.0:
            return 0.0

        # 7. Discrete Quantization by qty_step
        q_steps = math.floor(q_raw / spec.qty_step)
        q_final = round(q_steps * spec.qty_step, 8)

        # 8. Micro-Account Contract Router (Bybit V5 Strict Rule)
        # Invariant: NEVER expand risk to meet exchange minimum notional!
        if q_final < spec.min_order_qty:
            return 0.0

        notional = q_final * entry_px
        if notional < spec.min_notional:
            return 0.0

        # 9. Available Margin & Bankruptcy Buffer Validation
        # OrderCost = Notional * (1 / Leverage + f_open + f_close * (1 + 1 / Leverage))
        f_open = self.maker_fee if is_maker_entry else self.taker_fee
        f_close = self.taker_fee
        order_cost = notional * ((1.0 / eff_leverage) + f_open + f_close * (1.0 + (1.0 / eff_leverage)))
        if order_cost > portfolio.available_margin:
            # Scale down to available margin if viable
            max_viable_notional = portfolio.available_margin / ((1.0 / eff_leverage) + f_open + f_close * (1.0 + (1.0 / eff_leverage)))
            if max_viable_notional < spec.min_notional:
                return 0.0
            reduced_q_steps = math.floor((max_viable_notional / entry_px) / spec.qty_step)
            q_final = round(reduced_q_steps * spec.qty_step, 8)
            if q_final < spec.min_order_qty or (q_final * entry_px) < spec.min_notional:
                return 0.0

        return q_final
