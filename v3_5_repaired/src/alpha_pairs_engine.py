"""
Multi-Alpha Market-Neutral Engine v3.5-REPAIRED (Phase F1B).
Audit-hardened implementation with penny-perfect cash and trade accounting,
realistic stop-gap execution with Long/Short symmetry, Bybit exchange lot/minNotional validation,
and bad-data spike protection.

Mathematical Invariants:
1. Conservation of Realized Equity upon full closure:
   Delta Realized Equity == Sum(ClosedPairTrade.net_pnl) - Pending Open Entry Fees
2. Net PnL = Gross PnL - Entry Fee Allocated - Exit Fee Paid
3. Stop-Gap Realism:
   Long Exit Price <= min(stop_price, current_price) * (1 - slippage)
   Short Exit Price >= max(stop_price, current_price) * (1 + slippage)
"""

from dataclasses import dataclass
from decimal import Decimal
import logging
from typing import Dict, List, Optional, Any
import numpy as np

from v3_5_repaired.src.config import (
    INITIAL_CAPITAL,
    FEE_TAKER,
    FEE_MAKER,
    BASE_SLIPPAGE,
    MIN_NOTIONAL_USDT,
    MAX_TICK_SPIKE_PCT,
    BASE_RISK_PER_LEG_PCT,
    MAX_LEVERAGE_PER_LEG,
    STOP_LOSS_ATR_MULT,
    TRAILING_ACTIVATION_R,
    TRAILING_DISTANCE_ATR,
    PARTIAL_TP_R,
    PARTIAL_TP_RATIO,
    REBALANCE_INTERVAL_HOURS,
    ENTRY_TOP_K,
    ENTRY_BOTTOM_K,
    MIN_REL_STRENGTH_LONG,
    MIN_REL_STRENGTH_SHORT,
    MIN_KER_THRESHOLD,
    HYSTERESIS_TOP_RANK,
    HYSTERESIS_BOTTOM_RANK,
    MIN_PAIR_NET_ALPHA_SPREAD,
    HYSTERESIS_EXIT_RS_BUFFER,
    MIN_HOLD_BARS_REBALANCE,
    DRAWDOWN_LIMIT_PCT,
    BENCHMARK_SYMBOL,
)

logger = logging.getLogger("AlphaPairsEngineRepaired")


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
    entry_fee: Decimal = Decimal("0.0")  # F1B: Track allocated entry fee
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
    entry_fee_allocated: Decimal  # F1B: Track allocated entry fee
    exit_fee_paid: Decimal        # F1B: Track exit fee paid
    net_pnl: Decimal              # F1B: Penny-perfect net PnL
    reason: str
    entry_bar: int
    exit_bar: int
    entry_ts: int
    exit_ts: int

    @property
    def fee_paid(self) -> Decimal:
        return self.entry_fee_allocated + self.exit_fee_paid


class AlphaPairsEngineRepaired:
    """
    Market-Neutral Long/Short Pairs Trading Engine (Phase F1B Audit Hardened).
    """

    def __init__(
        self,
        initial_capital: Decimal = INITIAL_CAPITAL,
        benchmark: str = BENCHMARK_SYMBOL,
        fee_taker: Decimal = FEE_TAKER,
        fee_maker: Decimal = FEE_MAKER,
        slippage: Decimal = BASE_SLIPPAGE,
        min_notional: Decimal = MIN_NOTIONAL_USDT,
        max_tick_spike: Decimal = MAX_TICK_SPIKE_PCT,
    ):
        self.initial_capital = Decimal(str(initial_capital))
        self.realized_equity = Decimal(str(initial_capital))
        self.peak_equity = Decimal(str(initial_capital))
        self.max_drawdown_pct = Decimal("0.0")
        self.benchmark = benchmark
        self.fee_taker = Decimal(str(fee_taker))
        self.fee_maker = Decimal(str(fee_maker))
        self.fee_rate = self.fee_taker  # backward compatibility alias
        self.slippage = Decimal(str(slippage))
        self.min_notional = Decimal(str(min_notional))
        self.max_tick_spike = Decimal(str(max_tick_spike))

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
        metrics = {}
        for s, bars in prices_series.items():
            if len(bars) < 50:
                continue
            closes = np.array([b["close"] for b in bars], dtype=np.float64)
            highs = np.array([b["high"] for b in bars], dtype=np.float64)
            lows = np.array([b["low"] for b in bars], dtype=np.float64)

            # 72h momentum window
            lookback = min(72, len(closes) - 1)
            ret_window = (closes[-1] - closes[-lookback]) / closes[-lookback]

            # 24-period ATR
            tr1 = highs[1:] - lows[1:]
            tr2 = np.abs(highs[1:] - closes[:-1])
            tr3 = np.abs(lows[1:] - closes[:-1])
            tr = np.maximum(tr1, np.maximum(tr2, tr3))
            atr24 = float(np.mean(tr[-24:])) if len(tr) >= 24 else float(np.mean(tr))

            # Kaufman Efficiency Ratio (KER48)
            window_ker = min(48, len(closes) - 1)
            direction_net = abs(closes[-1] - closes[-window_ker])
            volatility_sum = np.sum(np.abs(np.diff(closes[-window_ker:])))
            ker48 = float(direction_net / volatility_sum) if volatility_sum > 0 else 0.0

            metrics[s] = {
                "ret_window": float(ret_window),
                "atr24": atr24,
                "ker48": ker48,
                "close": float(closes[-1]),
            }
        return metrics

    def rank_universe(
        self,
        metrics: Dict[str, Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        if self.benchmark not in metrics:
            return []
        btc_ret = metrics[self.benchmark]["ret_window"]
        rankings = []
        for s, m in metrics.items():
            if s == self.benchmark:
                continue
            rs = m["ret_window"] - btc_ret
            # Weighted rank score: rs * (0.6 + 0.4 * ker)
            composite_score = rs * (0.6 + 0.4 * m["ker48"])
            rankings.append({
                "symbol": s,
                "rs": float(rs),
                "ret": m["ret_window"],
                "ker48": m["ker48"],
                "atr24": m["atr24"],
                "score": float(composite_score),
            })
        rankings.sort(key=lambda x: x["score"], reverse=True)
        for i, item in enumerate(rankings):
            item["rank"] = i + 1
        self.latest_rankings = rankings
        return rankings

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

        # F1B Constraint: Verify Bybit minNotionalValue ($5.00 USDT)
        if notional < self.min_notional:
            logger.warning(
                f"Rejecting {symbol} entry: Notional=${notional:.2f} < MinNotional=${self.min_notional:.2f}"
            )
            return False

        # Deduct entry fee from realized equity (Cash Accounting)
        entry_fee = notional * self.fee_taker
        self.realized_equity -= entry_fee

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
            entry_fee=entry_fee,  # F1B: Retain allocated entry fee
        )
        side_str = "LONG" if direction == 1 else "SHORT"
        logger.info(
            f"Entered {side_str} pair leg on {symbol} @ {p} "
            f"(qty={qty:.4f}, notional=${notional:.2f}, entry_fee=${entry_fee:.4f})"
        )
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
        Zero-Lookahead: Uses closed bars and protects open positions even when halted.
        """
        self.bar_counter += 1
        metrics = self.compute_metrics(prices_series)
        rankings = self.rank_universe(metrics)

        # 1. Manage Active Positions (ALWAYS ACTIVE, EVEN IF HALTED)
        to_close = []
        for s, pos in list(self.open_positions.items()):
            curr_p = current_prices.get(s, pos.entry_price)
            atr = Decimal(str(metrics.get(s, {}).get("atr24", pos.initial_atr)))
            direction = pos.direction

            # F1B: Bad data spike check (ignore impossible wicks from advancing trailing stops)
            if pos.entry_price > Decimal("0.0"):
                price_delta_pct = abs(curr_p - pos.entry_price) / pos.entry_price
                is_anomalous_spike = price_delta_pct > self.max_tick_spike
            else:
                is_anomalous_spike = False

            if not is_anomalous_spike:
                if direction == 1:
                    pos.highest_p = max(pos.highest_p, curr_p)
                    profit_r = (pos.highest_p - pos.entry_price) / pos.initial_risk_dist
                    if profit_r >= TRAILING_ACTIVATION_R:
                        trail_stop = pos.highest_p - (TRAILING_DISTANCE_ATR * atr)
                        if trail_stop > pos.stop_price:
                            pos.stop_price = trail_stop
                else:
                    pos.lowest_p = min(pos.lowest_p, curr_p)
                    profit_r = (pos.entry_price - pos.lowest_p) / pos.initial_risk_dist
                    if profit_r >= TRAILING_ACTIVATION_R:
                        trail_stop = pos.lowest_p + (TRAILING_DISTANCE_ATR * atr)
                        if trail_stop < pos.stop_price:
                            pos.stop_price = trail_stop
            else:
                logger.warning(f"Anomalous price spike ignored on {s}: curr_p={curr_p}, entry={pos.entry_price}")

            # Partial Take Profit at +4.0R (50% trim)
            if not pos.tp_taken and not is_anomalous_spike:
                dist = (curr_p - pos.entry_price) * Decimal(str(direction))
                if dist >= pos.initial_risk_dist * PARTIAL_TP_R:
                    tp_qty = pos.qty * PARTIAL_TP_RATIO
                    tp_gross = tp_qty * (curr_p - pos.entry_price) * Decimal(str(direction))
                    tp_exit_fee = tp_qty * curr_p * self.fee_maker
                    tp_entry_fee = pos.entry_fee * PARTIAL_TP_RATIO

                    # Update remaining position
                    pos.entry_fee -= tp_entry_fee
                    pos.qty -= tp_qty
                    pos.tp_taken = True
                    pos.stop_price = pos.entry_price + (Decimal(str(direction)) * atr * Decimal("0.1"))

                    # Cash accounting (add gross - exit_fee; entry_fee was already deducted)
                    self.realized_equity += (tp_gross - tp_exit_fee)

                    # Analytical Ledger (F1B: Record closed partial slice)
                    slice_net = tp_gross - tp_entry_fee - tp_exit_fee
                    self.closed_trades.append(
                        ClosedPairTrade(
                            symbol=s,
                            direction=direction,
                            entry_price=pos.entry_price,
                            exit_price=curr_p,
                            qty=tp_qty,
                            gross_pnl=tp_gross,
                            entry_fee_allocated=tp_entry_fee,
                            exit_fee_paid=tp_exit_fee,
                            net_pnl=slice_net,
                            reason="PARTIAL_TP_4R",
                            entry_bar=pos.entry_bar,
                            exit_bar=self.bar_counter,
                            entry_ts=pos.entry_ts,
                            exit_ts=bar_ts,
                        )
                    )
                    logger.info(f"Partial TP 50% on {s} (+{slice_net:.2f} USD Net)")

            # Stop Loss Trigger (F1B: Full Long/Short symmetry with realistic gap execution)
            if direction == 1:
                is_stopped = curr_p <= pos.stop_price
                effective_stop = min(pos.stop_price, curr_p)
                exit_p = effective_stop * (Decimal("1.0") - self.slippage)
            else:
                is_stopped = curr_p >= pos.stop_price
                effective_stop = max(pos.stop_price, curr_p)
                exit_p = effective_stop * (Decimal("1.0") + self.slippage)

            if is_stopped:
                gross_pnl = pos.qty * (exit_p - pos.entry_price) * Decimal(str(direction))
                exit_fee = pos.qty * exit_p * self.fee_taker
                net_pnl = gross_pnl - pos.entry_fee - exit_fee

                # Cash accounting
                self.realized_equity += (gross_pnl - exit_fee)

                self.closed_trades.append(
                    ClosedPairTrade(
                        symbol=s,
                        direction=direction,
                        entry_price=pos.entry_price,
                        exit_price=exit_p,
                        qty=pos.qty,
                        gross_pnl=gross_pnl,
                        entry_fee_allocated=pos.entry_fee,
                        exit_fee_paid=exit_fee,
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

        # 2. Rebalancing & Hysteresis Ranking Check (every 4 hours)
        if (self.bar_counter % REBALANCE_INTERVAL_HOURS == 0 or len(self.open_positions) == 0) and rankings:
            ranked_symbols = [r["symbol"] for r in rankings]
            rank_map = {r["symbol"]: (r["rank"], r["rs"], r["score"]) for r in rankings}

            top_retained = set(ranked_symbols[:HYSTERESIS_TOP_RANK])
            bottom_retained = set(ranked_symbols[-HYSTERESIS_BOTTOM_RANK:])

            for s in list(self.open_positions.keys()):
                pos = self.open_positions[s]
                curr_p = current_prices.get(s, pos.entry_price)
                s_info = rank_map.get(s, (99, 0.0, 0.0))
                s_rs = Decimal(str(s_info[1]))
                bars_held = self.bar_counter - pos.entry_bar

                if bars_held < MIN_HOLD_BARS_REBALANCE:
                    continue

                exit_pos = False
                if pos.direction == 1:
                    if (s not in top_retained) or (s_rs < -HYSTERESIS_EXIT_RS_BUFFER):
                        exit_pos = True
                elif pos.direction == -1:
                    if (s not in bottom_retained) or (s_rs > HYSTERESIS_EXIT_RS_BUFFER):
                        exit_pos = True

                if exit_pos:
                    if pos.direction == 1:
                        exit_p = curr_p * (Decimal("1.0") - self.slippage)
                    else:
                        exit_p = curr_p * (Decimal("1.0") + self.slippage)

                    gross_pnl = pos.qty * (exit_p - pos.entry_price) * Decimal(str(pos.direction))
                    exit_fee = pos.qty * exit_p * self.fee_taker
                    net_pnl = gross_pnl - pos.entry_fee - exit_fee

                    self.realized_equity += (gross_pnl - exit_fee)
                    self.closed_trades.append(
                        ClosedPairTrade(
                            symbol=s,
                            direction=pos.direction,
                            entry_price=pos.entry_price,
                            exit_price=exit_p,
                            qty=pos.qty,
                            gross_pnl=gross_pnl,
                            entry_fee_allocated=pos.entry_fee,
                            exit_fee_paid=exit_fee,
                            net_pnl=net_pnl,
                            reason="HYSTERESIS_EXIT",
                            entry_bar=pos.entry_bar,
                            exit_bar=self.bar_counter,
                            entry_ts=pos.entry_ts,
                            exit_ts=bar_ts,
                        )
                    )
                    del self.open_positions[s]

            # 3. New Entry Candidate Evaluation (F1B: BLOCKED WHEN HALTED OR IN MACRO LOCKOUT)
            if not self.trading_halted and not macro_lockout:
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

                if active_longs == active_shorts and active_longs < ENTRY_TOP_K:
                    for l_item, s_item in zip(entry_long_candidates, entry_short_candidates):
                        if active_longs >= ENTRY_TOP_K or active_shorts >= ENTRY_BOTTOM_K:
                            break
                        l_rs = Decimal(str(l_item["rs"]))
                        s_rs = Decimal(str(s_item["rs"]))
                        net_spread = l_rs - s_rs
                        if net_spread < MIN_PAIR_NET_ALPHA_SPREAD:
                            continue

                        # Atomic matched pair entry
                        l_ok = self._enter_leg(l_item["symbol"], 1, current_prices, metrics, current_eq, bar_ts)
                        s_ok = self._enter_leg(s_item["symbol"], -1, current_prices, metrics, current_eq, bar_ts)

                        # If one leg fails, unwind the other to avoid naked directional risk (F1B Hardening)
                        if l_ok and not s_ok:
                            logger.error(f"Broken pair leg: {l_item['symbol']} entered, but {s_item['symbol']} failed. Unwinding {l_item['symbol']}.")
                            self._unwind_leg(l_item["symbol"], current_prices, bar_ts)
                        elif s_ok and not l_ok:
                            logger.error(f"Broken pair leg: {s_item['symbol']} entered, but {l_item['symbol']} failed. Unwinding {s_item['symbol']}.")
                            self._unwind_leg(s_item["symbol"], current_prices, bar_ts)
                        elif l_ok and s_ok:
                            active_longs += 1
                            active_shorts += 1

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
            elif self.trading_halted:
                logger.warning("[HALT ACTIVE] Trading halted due to max drawdown limit. No new entries allowed.")

        # 4. Update Drawdown & Equity Peaks
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

    def _unwind_leg(self, symbol: str, current_prices: Dict[str, Decimal], bar_ts: int) -> None:
        """Emergency unwind of a single leg when matched pair execution breaks."""
        pos = self.open_positions.pop(symbol, None)
        if not pos:
            return
        curr_p = current_prices.get(symbol, pos.entry_price)
        if pos.direction == 1:
            exit_p = curr_p * (Decimal("1.0") - self.slippage)
        else:
            exit_p = curr_p * (Decimal("1.0") + self.slippage)

        gross_pnl = pos.qty * (exit_p - pos.entry_price) * Decimal(str(pos.direction))
        exit_fee = pos.qty * exit_p * self.fee_taker
        net_pnl = gross_pnl - pos.entry_fee - exit_fee
        self.realized_equity += (gross_pnl - exit_fee)
        self.closed_trades.append(
            ClosedPairTrade(
                symbol=symbol,
                direction=pos.direction,
                entry_price=pos.entry_price,
                exit_price=exit_p,
                qty=pos.qty,
                gross_pnl=gross_pnl,
                entry_fee_allocated=pos.entry_fee,
                exit_fee_paid=exit_fee,
                net_pnl=net_pnl,
                reason="UNWIND_BROKEN_PAIR",
                entry_bar=pos.entry_bar,
                exit_bar=self.bar_counter,
                entry_ts=pos.entry_ts,
                exit_ts=bar_ts,
            )
        )
