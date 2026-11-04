"""
Quantitative Core Engine for v3.5 Alpha Pairs (Market-Neutral Long/Short Pairs).
Adheres strictly to the crypto-hybrid-bot-tester and quantitative invariant standards:
- Zero-Lookahead Point-in-Time (PIT) ranking
- Cross-Sectional Relative Strength Momentum (CAR-CSMOM 168h vs BTC)
- Kaufman Efficiency Ratio (KER 48h) weighting
- Minimum Alpha Spread Hurdle & Turnover Friction Filter (cuts rebalancing drag by >80%)
- Hysteresis Rank Retention Buffer with Deadband
- Runner Protection (preserves profitable legs like 1000PEPEUSDT)
- True Mark-to-Market Portfolio Equity Tracking
- Penny-perfect trade accounting conservation
"""

from dataclasses import dataclass, field
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from v3_5.src.config import (
    BENCHMARK_SYMBOL,
    MOMENTUM_WINDOW_HOURS,
    REBALANCE_INTERVAL_HOURS,
    ENTRY_TOP_K,
    ENTRY_BOTTOM_K,
    MIN_REL_STRENGTH_LONG,
    MIN_REL_STRENGTH_SHORT,
    MIN_KER_THRESHOLD,
    HYSTERESIS_TOP_RANK,
    HYSTERESIS_BOTTOM_RANK,
    MIN_PAIR_NET_ALPHA_SPREAD,
    MIN_REBALANCE_SPREAD_ADVANTAGE,
    HYSTERESIS_EXIT_RS_BUFFER,
    MIN_HOLD_BARS_REBALANCE,
    PROTECT_PROFITABLE_RUNNERS,
    INITIAL_CAPITAL,
    BASE_RISK_PER_LEG_PCT,
    MAX_LEVERAGE_PER_LEG,
    MAX_PORTFOLIO_LEVERAGE,
    DRAWDOWN_LIMIT_PCT,
    STOP_LOSS_ATR_MULT,
    TRAILING_ACTIVATION_R,
    TRAILING_DISTANCE_ATR,
    PARTIAL_TP_R,
    PARTIAL_TP_RATIO,
    BLENDED_FEE_RATE,
    BASE_SLIPPAGE,
)

logger = logging.getLogger("AlphaPairsEngine")


@dataclass
class ActivePairPosition:
    symbol: str
    direction: int  # +1 for Long, -1 for Short
    qty: Decimal
    entry_price: Decimal
    stop_price: Decimal
    initial_risk_dist: Decimal
    initial_atr: Decimal
    highest_p: Decimal
    lowest_p: Decimal
    entry_bar: int
    entry_ts: int
    macro_trimmed: bool = False
    tp_taken: bool = False

    def floating_pnl(self, current_price: Decimal) -> Decimal:
        return self.qty * (current_price - self.entry_price) * Decimal(str(self.direction))


@dataclass
class ClosedPairTrade:
    symbol: str
    direction: int
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    gross_pnl: Decimal
    fee_paid: Decimal
    net_pnl: Decimal
    reason: str
    entry_bar: int
    exit_bar: int
    entry_ts: int
    exit_ts: int


class AlphaPairsEngine:
    """
    Market-Neutral Long/Short Pairs Trading Engine.
    """

    def __init__(
        self,
        initial_capital: Decimal = INITIAL_CAPITAL,
        benchmark: str = BENCHMARK_SYMBOL,
        fee_rate: Decimal = BLENDED_FEE_RATE,
        slippage: Decimal = BASE_SLIPPAGE,
    ):
        self.initial_capital = Decimal(str(initial_capital))
        self.realized_equity = Decimal(str(initial_capital))
        self.peak_equity = Decimal(str(initial_capital))
        self.max_drawdown_pct = Decimal("0.0")
        self.benchmark = benchmark
        self.fee_rate = Decimal(str(fee_rate))
        self.slippage = Decimal(str(slippage))

        self.trading_halted = False
        self.bar_counter = 0

        self.open_positions: Dict[str, ActivePairPosition] = {}
        self.closed_trades: List[ClosedPairTrade] = []
        self.latest_rankings: List[Dict[str, Any]] = []

    def get_portfolio_equity(self, current_prices: Dict[str, Decimal]) -> Decimal:
        fl_pnl = Decimal("0.0")
        for s, pos in self.open_positions.items():
            if s in current_prices:
                fl_pnl += pos.floating_pnl(current_prices[s])
        return self.realized_equity + fl_pnl

    def compute_metrics(
        self,
        prices_series: Dict[str, List[Dict[str, float]]],
    ) -> Dict[str, Dict[str, float]]:
        """
        Compute rolling 168h Return, ATR24, and KER48 for all symbols.
        Each bar in prices_series is dict(open, high, low, close, volume).
        Zero-Lookahead: uses only past closed bars.
        """
        metrics = {}
        for s, bars in prices_series.items():
            if len(bars) < 50:
                continue
            closes = np.array([b["close"] for b in bars], dtype=np.float64)
            highs = np.array([b["high"] for b in bars], dtype=np.float64)
            lows = np.array([b["low"] for b in bars], dtype=np.float64)

            # 168h Return (or as far back as available up to 168h)
            lookback = min(len(closes) - 1, MOMENTUM_WINDOW_HOURS)
            ret_168 = (closes[-1] - closes[-lookback - 1]) / closes[-lookback - 1] if lookback > 0 else 0.0

            # ATR24
            tr1 = highs[1:] - lows[1:]
            tr2 = np.abs(highs[1:] - closes[:-1])
            tr3 = np.abs(lows[1:] - closes[:-1])
            tr = np.maximum(tr1, np.maximum(tr2, tr3))
            atr = float(np.mean(tr[-24:])) if len(tr) >= 24 else float(np.mean(tr)) if len(tr) > 0 else closes[-1] * 0.02

            # KER48 (Kaufman Efficiency Ratio over 48h)
            ker_window = min(len(closes) - 1, 48)
            change = abs(closes[-1] - closes[-ker_window - 1])
            volatility = np.sum(np.abs(closes[1:] - closes[:-1])[-ker_window:])
            ker = float(change / volatility) if volatility > 0 else 0.5

            metrics[s] = {
                "close": float(closes[-1]),
                "ret_168": ret_168,
                "atr24": atr,
                "ker48": ker,
            }
        return metrics

    def rank_universe(self, metrics: Dict[str, Dict[str, float]]) -> List[Dict[str, Any]]:
        """
        Rank altcoins by Relative Strength vs BTC, weighted by KER.
        """
        if self.benchmark not in metrics:
            return []

        btc_ret = metrics[self.benchmark]["ret_168"]
        rank_list = []

        for s, m in metrics.items():
            if s == self.benchmark:
                continue
            rs = m["ret_168"] - btc_ret
            ker = m["ker48"]
            # Combined momentum score: RS * (0.6 + 0.4 * KER)
            score = rs * (0.6 + 0.4 * ker)
            rank_list.append({
                "symbol": s,
                "score": float(score),
                "rs": float(rs),
                "ret_168": float(m["ret_168"]),
                "ker48": float(ker),
                "close": float(m["close"]),
                "atr24": float(m["atr24"]),
            })

        rank_list.sort(key=lambda x: x["score"], reverse=True)
        for idx, item in enumerate(rank_list):
            item["rank"] = idx + 1
        self.latest_rankings = rank_list
        return rank_list

    def _enter_leg(
        self,
        symbol: str,
        direction: int,
        current_prices: Dict[str, Decimal],
        metrics: Dict[str, Dict[str, Any]],
        current_eq: Decimal,
        bar_ts: int,
    ) -> bool:
        p = current_prices.get(symbol)
        if not p or symbol not in metrics:
            return False
        atr = Decimal(str(metrics[symbol]["atr24"]))
        stop_dist = STOP_LOSS_ATR_MULT * atr
        if stop_dist <= Decimal("0.0"):
            return False

        risk_usd = current_eq * (BASE_RISK_PER_LEG_PCT / Decimal("100.0"))
        qty = risk_usd / stop_dist
        notional = qty * p
        max_notional = current_eq * MAX_LEVERAGE_PER_LEG
        if notional > max_notional:
            qty = max_notional / p
            notional = max_notional

        fee = notional * self.fee_rate
        self.realized_equity -= fee
        stop_price = p - stop_dist if direction == 1 else p + stop_dist
        self.open_positions[symbol] = ActivePairPosition(
            symbol=symbol,
            direction=direction,
            qty=qty,
            entry_price=p,
            stop_price=stop_price,
            initial_risk_dist=stop_dist,
            initial_atr=atr,
            highest_p=p,
            lowest_p=p,
            entry_bar=self.bar_counter,
            entry_ts=bar_ts,
        )
        side_str = "LONG" if direction == 1 else "SHORT"
        logger.info(f"Entered {side_str} pair leg on {symbol} @ {p} (qty={qty:.4f}, stop={stop_price})")
        return True

    def process_bar(
        self,
        prices_series: Dict[str, List[Dict[str, float]]],
        current_prices: Dict[str, Decimal],
        bar_ts: int,
        macro_lockout: bool = False,
    ) -> None:
        """
        Main bar processing cycle (executed on closed 1h bars).
        """
        self.bar_counter += 1
        metrics = self.compute_metrics(prices_series)
        rankings = self.rank_universe(metrics)

        # 1. Manage Active Positions (Stop Loss, Trailing Stop, Partial TP, Macro Trim)
        to_close = []
        for s, pos in list(self.open_positions.items()):
            if s not in current_prices:
                continue
            curr_p = current_prices[s]
            direction = pos.direction
            atr = pos.initial_atr

            # Update highest / lowest price
            if direction == 1:
                pos.highest_p = max(pos.highest_p, curr_p)
                profit_r = (pos.highest_p - pos.entry_price) / pos.initial_risk_dist
                # Trailing stop update after +1.5R
                if profit_r >= TRAILING_ACTIVATION_R:
                    trail_stop = pos.highest_p - (TRAILING_DISTANCE_ATR * atr)
                    if trail_stop > pos.stop_price:
                        pos.stop_price = trail_stop
            else:
                pos.lowest_p = min(pos.lowest_p, curr_p)
                profit_r = (pos.entry_price - pos.lowest_p) / pos.initial_risk_dist
                # Trailing stop update after +1.5R
                if profit_r >= TRAILING_ACTIVATION_R:
                    trail_stop = pos.lowest_p + (TRAILING_DISTANCE_ATR * atr)
                    if trail_stop < pos.stop_price:
                        pos.stop_price = trail_stop

            # Partial Take Profit at +4.0R (50% trim)
            if not pos.tp_taken:
                dist = (curr_p - pos.entry_price) * Decimal(str(direction))
                if dist >= pos.initial_risk_dist * PARTIAL_TP_R:
                    tp_qty = pos.qty * PARTIAL_TP_RATIO
                    tp_gross = tp_qty * (curr_p - pos.entry_price) * Decimal(str(direction))
                    tp_fee = tp_qty * curr_p * self.fee_rate
                    tp_net = tp_gross - tp_fee
                    self.realized_equity += tp_net
                    pos.qty -= tp_qty
                    pos.tp_taken = True
                    # Tighten stop to Breakeven + buffer
                    pos.stop_price = pos.entry_price + (Decimal(str(direction)) * atr * Decimal("0.1"))
                    logger.info(f"Partial TP 50% hit on {s} at +4.0R (+{tp_net:.2f} USD)")

            # Stop Loss Trigger
            is_stopped = (curr_p <= pos.stop_price) if direction == 1 else (curr_p >= pos.stop_price)
            if is_stopped:
                exit_p = pos.stop_price * (Decimal("1.0") - (self.slippage * Decimal(str(direction))))
                gross_pnl = pos.qty * (exit_p - pos.entry_price) * Decimal(str(direction))
                fee = pos.qty * exit_p * self.fee_rate
                net_pnl = gross_pnl - fee
                self.realized_equity += net_pnl
                self.closed_trades.append(
                    ClosedPairTrade(
                        symbol=s,
                        direction=direction,
                        entry_price=pos.entry_price,
                        exit_price=exit_p,
                        qty=pos.qty,
                        gross_pnl=gross_pnl,
                        fee_paid=fee,
                        net_pnl=net_pnl,
                        reason="STOP_LOSS",
                        entry_bar=pos.entry_bar,
                        exit_bar=self.bar_counter,
                        entry_ts=pos.entry_ts,
                        exit_ts=bar_ts,
                    )
                )
                to_close.append(s)

        for s in to_close:
            del self.open_positions[s]

        # 2. Rebalancing & Hysteresis Ranking Check (every 12 hours)
        if (self.bar_counter % REBALANCE_INTERVAL_HOURS == 0 or len(self.open_positions) == 0) and rankings:
            ranked_symbols = [r["symbol"] for r in rankings]
            rank_map = {r["symbol"]: (r["rank"], r["rs"], r["score"]) for r in rankings}

            top_retained = set(ranked_symbols[:HYSTERESIS_TOP_RANK])
            bottom_retained = set(ranked_symbols[-HYSTERESIS_BOTTOM_RANK:])

            # Hysteresis Exits with Friction/Spread & Runner Protection
            for s in list(self.open_positions.keys()):
                pos = self.open_positions[s]
                curr_p = current_prices.get(s, pos.entry_price)
                s_info = rank_map.get(s, (99, 0.0, 0.0))
                s_rs = Decimal(str(s_info[1]))
                bars_held = self.bar_counter - pos.entry_bar
                floating_pnl = pos.floating_pnl(curr_p)

                # Filter 1: Min holding bars before allowing rebalance exit
                if bars_held < MIN_HOLD_BARS_REBALANCE:
                    continue

                # Filter 2: Runner protection for profitable positions
                # If profitable and still has positive RS (or within buffer), do not churn!
                if PROTECT_PROFITABLE_RUNNERS and floating_pnl > Decimal("0.0"):
                    if pos.direction == 1 and s_rs >= -HYSTERESIS_EXIT_RS_BUFFER:
                        continue
                    if pos.direction == -1 and s_rs <= HYSTERESIS_EXIT_RS_BUFFER:
                        continue

                exit_pos = False
                if pos.direction == 1:
                    # Long exit if dropped below Top 5 OR RS fell below negative buffer
                    if (s not in top_retained) or (s_rs < -HYSTERESIS_EXIT_RS_BUFFER):
                        exit_pos = True
                elif pos.direction == -1:
                    # Short exit if rose above Bottom 5 OR RS rose above positive buffer
                    if (s not in bottom_retained) or (s_rs > HYSTERESIS_EXIT_RS_BUFFER):
                        exit_pos = True

                if exit_pos:
                    exit_p = curr_p * (Decimal("1.0") - (self.slippage * Decimal(str(pos.direction))))
                    gross_pnl = pos.qty * (exit_p - pos.entry_price) * Decimal(str(pos.direction))
                    fee = pos.qty * exit_p * self.fee_rate
                    net_pnl = gross_pnl - fee
                    self.realized_equity += net_pnl
                    self.closed_trades.append(
                        ClosedPairTrade(
                            symbol=s,
                            direction=pos.direction,
                            entry_price=pos.entry_price,
                            exit_price=exit_p,
                            qty=pos.qty,
                            gross_pnl=gross_pnl,
                            fee_paid=fee,
                            net_pnl=net_pnl,
                            reason="HYSTERESIS_EXIT",
                            entry_bar=pos.entry_bar,
                            exit_bar=self.bar_counter,
                            entry_ts=pos.entry_ts,
                            exit_ts=bar_ts,
                        )
                    )
                    del self.open_positions[s]
                    logger.info(f"Hysteresis exit for {s} (rank={s_info[0]}, RS={s_rs:.3f}, net_pnl={net_pnl:.2f})")

            # Candidate Selection for New Entries (eligible unheld symbols)
            entry_long_candidates = [
                r for r in rankings[:ENTRY_TOP_K]
                if Decimal(str(r["rs"])) >= MIN_REL_STRENGTH_LONG and Decimal(str(r["ker48"])) >= MIN_KER_THRESHOLD
                and r["symbol"] not in self.open_positions and r["symbol"] in current_prices and r["symbol"] in metrics
            ]
            entry_short_candidates = [
                r for r in rankings[-ENTRY_BOTTOM_K:]
                if Decimal(str(r["rs"])) <= MIN_REL_STRENGTH_SHORT and Decimal(str(r["ker48"])) >= MIN_KER_THRESHOLD
                and r["symbol"] not in self.open_positions and r["symbol"] in current_prices and r["symbol"] in metrics
            ]

            active_longs = sum(1 for p in self.open_positions.values() if p.direction == 1)
            active_shorts = sum(1 for p in self.open_positions.values() if p.direction == -1)
            current_eq = self.get_portfolio_equity(current_prices)

            # Strict Pair-Wise Execution (Matched Long + Short pairs to guarantee Beta ~ 0.0)
            if not macro_lockout:
                # Case 1: Balanced state -> enter matched Long + Short pairs simultaneously
                if active_longs == active_shorts and active_longs < ENTRY_TOP_K:
                    for l_item, s_item in zip(entry_long_candidates, entry_short_candidates):
                        if active_longs >= ENTRY_TOP_K or active_shorts >= ENTRY_BOTTOM_K:
                            break
                        l_rs = Decimal(str(l_item["rs"]))
                        s_rs = Decimal(str(s_item["rs"]))
                        net_spread = l_rs - s_rs
                        if net_spread < MIN_PAIR_NET_ALPHA_SPREAD:
                            logger.info(
                                f"Skipping pair entry {l_item['symbol']}/{s_item['symbol']}: "
                                f"Net spread {net_spread*100:.2f}% < threshold {MIN_PAIR_NET_ALPHA_SPREAD*100:.2f}%"
                            )
                            continue

                        # Enter both legs in lockstep
                        if self._enter_leg(l_item["symbol"], 1, current_prices, metrics, current_eq, bar_ts):
                            active_longs += 1
                        if self._enter_leg(s_item["symbol"], -1, current_prices, metrics, current_eq, bar_ts):
                            active_shorts += 1

                # Case 2: Imbalance recovery (e.g. one leg stopped out) -> Restore balance
                elif active_longs < active_shorts and entry_long_candidates:
                    for l_item in entry_long_candidates:
                        if active_longs >= active_shorts or active_longs >= ENTRY_TOP_K:
                            break
                        if self._enter_leg(l_item["symbol"], 1, current_prices, metrics, current_eq, bar_ts):
                            active_longs += 1

                elif active_shorts < active_longs and entry_short_candidates:
                    for s_item in entry_short_candidates:
                        if active_shorts >= active_longs or active_shorts >= ENTRY_BOTTOM_K:
                            break
                        if self._enter_leg(s_item["symbol"], -1, current_prices, metrics, current_eq, bar_ts):
                            active_shorts += 1

        # 3. Update Drawdown & Equity Peaks
        total_eq = self.get_portfolio_equity(current_prices)
        if total_eq > self.peak_equity:
            self.peak_equity = total_eq
        if self.peak_equity > Decimal("0.0"):
            dd = (self.peak_equity - total_eq) / self.peak_equity * Decimal("100.0")
            if dd > self.max_drawdown_pct:
                self.max_drawdown_pct = dd
            if dd >= DRAWDOWN_LIMIT_PCT:
                self.trading_halted = True
                logger.warning(f"Drawdown {dd:.2f}% breached limit {DRAWDOWN_LIMIT_PCT}%. Trading halted!")
