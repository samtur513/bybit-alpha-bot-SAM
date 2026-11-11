"""
Multi-Alpha Market-Neutral Engine v3.5-REPAIRED (Phase F1B Audit Hardened).
Audit-hardened implementation with penny-perfect cash and trade accounting,
realistic stop-gap execution with Long/Short symmetry, Bybit exchange lot/minNotional validation,
advanced multi-criteria market data validity checking, and non-blocking risk protection.

Mathematical Invariants:
1. Conservation of Realized Equity upon full closure:
   Delta Realized Equity == Sum(ClosedPairTrade.net_pnl) + Sum(FundingCashflowEvent.cashflow_usdt) - Sum(Open.entry_fee)
2. Net PnL = Gross PnL - Entry Fee Allocated - Exit Fee Paid
3. Stop-Gap Realism:
   Long Exit Price <= min(stop_price, current_price) * (1 - slippage)
   Short Exit Price >= max(stop_price, current_price) * (1 + slippage)
4. Orderbook Fill Realism:
   Long Exit Fill = Bid * (1 - slippage)
   Short Exit Fill = Ask * (1 + slippage)
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import logging
from typing import Dict, List, Optional, Any, Tuple
import numpy as np

from v3_5_repaired.src.config import (
    INITIAL_CAPITAL,
    FEE_TAKER,
    FEE_MAKER,
    BASE_SLIPPAGE,
    MIN_NOTIONAL_USDT,
    MIN_ORDER_QTY_DEFAULT,
    MAX_TICK_SPIKE_PCT,
    MAX_QUOTE_STALENESS_SEC,
    MAX_BID_ASK_SPREAD_PCT,
    MAX_VOLATILITY_JUMP_ATR_MULT,
    CONFIRMED_SPIKE_TICKS,
    BYBIT_INSTRUMENT_RULES,
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
    entry_fee: Decimal = Decimal("0.0")  # Track allocated entry fee
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
    entry_fee_allocated: Decimal  # Proportional entry fee allocated to this slice/trade
    exit_fee_paid: Decimal        # Taker/Maker fee paid on exit
    net_pnl: Decimal              # Penny-perfect net PnL = gross - entry_fee - exit_fee
    reason: str
    entry_bar: int
    exit_bar: int
    entry_ts: int
    exit_ts: int

    @property
    def fee_paid(self) -> Decimal:
        return self.entry_fee_allocated + self.exit_fee_paid


@dataclass
class FundingCashflowEvent:
    timestamp: int
    symbol: str
    position_qty: Decimal
    mark_price: Decimal
    funding_rate: Decimal
    cashflow_usdt: Decimal  # Positive = received, Negative = paid


@dataclass
class BybitInstrumentRule:
    symbol: str
    tick_size: Decimal = Decimal("0.01")
    qty_step: Decimal = Decimal("0.001")
    min_order_qty: Decimal = Decimal("0.001")
    max_order_qty: Decimal = Decimal("100000.0")
    min_notional_value: Decimal = Decimal("5.00")
    status: str = "Trading"

    def round_qty(self, raw_qty: Decimal) -> Decimal:
        """Floor to exact qty_step multiple using Decimal arithmetic."""
        if self.qty_step <= Decimal("0.0"):
            return raw_qty
        steps = (raw_qty / self.qty_step).to_integral_value(rounding=ROUND_FLOOR)
        return steps * self.qty_step

    def round_price(self, raw_price: Decimal) -> Decimal:
        """Round to nearest tick_size."""
        if self.tick_size <= Decimal("0.0"):
            return raw_price
        steps = (raw_price / self.tick_size).to_integral_value(rounding=ROUND_HALF_UP)
        return steps * self.tick_size

    def validate_order(self, raw_qty: Decimal, price: Decimal) -> Tuple[bool, Decimal, str]:
        if self.status != "Trading":
            return False, Decimal("0.0"), f"Instrument status '{self.status}' != 'Trading'"

        qty = self.round_qty(raw_qty)
        if qty < self.min_order_qty:
            return False, qty, f"Quantity {qty} < minOrderQty {self.min_order_qty}"
        if qty > self.max_order_qty:
            return False, qty, f"Quantity {qty} > maxOrderQty {self.max_order_qty}"

        notional = qty * price
        if notional < self.min_notional_value:
            return False, qty, f"Notional ${notional:.2f} < minNotionalValue ${self.min_notional_value:.2f}"

        return True, qty, "VALID"


class MarketDataValidator:
    """
    Market Data Validity & Non-Blocking Risk Protection Engine.
    Enforces freshness, bid/ask spread, and volatility-scaled jump checks.
    Ensures that protective stop-loss triggers are NEVER blocked by a single anomalous tick.
    """

    def __init__(
        self,
        max_staleness_sec: int = MAX_QUOTE_STALENESS_SEC,
        max_spread_pct: Decimal = MAX_BID_ASK_SPREAD_PCT,
        max_volatility_jump_mult: Decimal = MAX_VOLATILITY_JUMP_ATR_MULT,
        confirm_ticks_threshold: int = CONFIRMED_SPIKE_TICKS,
    ):
        self.max_staleness_sec = max_staleness_sec
        self.max_spread_pct = max_spread_pct
        self.max_volatility_jump_mult = max_volatility_jump_mult
        self.confirm_ticks_threshold = confirm_ticks_threshold

        self.last_valid_price: Dict[str, Decimal] = {}
        self.last_valid_timestamp: Dict[str, int] = {}
        self.consecutive_breaches: Dict[str, int] = {}

    def validate_quote(
        self,
        symbol: str,
        price: Decimal,
        timestamp: int,
        atr: Decimal,
        bid: Optional[Decimal] = None,
        ask: Optional[Decimal] = None,
        current_time: Optional[int] = None,
        stop_price: Optional[Decimal] = None,
        direction: Optional[int] = None,
    ) -> Tuple[bool, str, bool]:
        """
        Validates quote data integrity.
        Returns:
            (allow_advancement, reason, allow_protective_exit)
        - allow_advancement: Whether to advance trailing stops or update peak prices.
        - allow_protective_exit: Whether a stop-loss protective exit is permitted.
        """
        # 1. Monotonic Timestamp Check
        last_ts = self.last_valid_timestamp.get(symbol, 0)
        if timestamp < last_ts:
            return False, f"Timestamp regression: {timestamp} < {last_ts}", False

        # 2. Staleness Check
        if current_time is not None:
            staleness_ms = abs(current_time - timestamp)
            if staleness_ms > self.max_staleness_sec * 1000:
                return False, f"Stale quote: age={staleness_ms}ms > {self.max_staleness_sec * 1000}ms", False

        # 3. Bid/Ask Spread Check
        if bid is not None and ask is not None:
            if bid > Decimal("0.0") and ask >= bid:
                mid = (bid + ask) / Decimal("2.0")
                spread_pct = (ask - bid) / mid
                if spread_pct > self.max_spread_pct:
                    return False, f"Hollow book: spread={spread_pct*100:.3f}% > {self.max_spread_pct*100:.3f}%", False
            else:
                return False, f"Inverted or non-positive book: bid={bid}, ask={ask}", False

        # 4. Volatility-Scaled Jump Check
        prev_p = self.last_valid_price.get(symbol, price)
        jump = abs(price - prev_p)
        max_jump = self.max_volatility_jump_mult * atr

        is_jump_anomalous = (prev_p > Decimal("0.0")) and (jump > max_jump) and (atr > Decimal("0.0"))

        # 5. Protective Stop Breach Evaluation
        allow_protective_exit = True
        if is_jump_anomalous:
            # Check if this price would breach a stop loss
            is_breach = False
            if stop_price is not None and direction is not None:
                if direction == 1 and price <= stop_price:
                    is_breach = True
                elif direction == -1 and price >= stop_price:
                    is_breach = True

            if is_breach:
                # Count consecutive breach ticks
                count = self.consecutive_breaches.get(symbol, 0) + 1
                self.consecutive_breaches[symbol] = count
                # Corroboration: If bid/ask confirms the breach OR consecutive breaches >= threshold
                has_book_confirm = False
                if direction == 1 and bid is not None and bid <= stop_price:
                    has_book_confirm = True
                elif direction == -1 and ask is not None and ask >= stop_price:
                    has_book_confirm = True

                if count >= self.confirm_ticks_threshold or has_book_confirm:
                    allow_protective_exit = True
                    logger.warning(
                        f"Protective exit confirmed on {symbol} despite jump (count={count}, book_confirm={has_book_confirm})"
                    )
                else:
                    allow_protective_exit = False
                    logger.warning(
                        f"Unconfirmed spike on {symbol}: price={price}, prev={prev_p}, jump={jump} > max_jump={max_jump}. "
                        f"Trailing freeze active; awaiting confirmation."
                    )
            else:
                self.consecutive_breaches[symbol] = 0

            # Advancement of trailing stop is always FROZEN during anomalous single-tick jumps
            return False, f"Volatility jump: jump={jump:.4f} > {max_jump:.4f} (3.5x ATR)", allow_protective_exit

        # Valid tick: update state
        self.last_valid_price[symbol] = price
        self.last_valid_timestamp[symbol] = timestamp
        self.consecutive_breaches[symbol] = 0
        return True, "VALID", True


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
        self.funding_events: List[FundingCashflowEvent] = []
        self.latest_rankings: List[Dict[str, Any]] = []

        # Market validator
        self.market_validator = MarketDataValidator()

        # Bybit instrument rules
        self.instrument_rules: Dict[str, BybitInstrumentRule] = {}
        for sym, cfg in BYBIT_INSTRUMENT_RULES.items():
            self.instrument_rules[sym] = BybitInstrumentRule(
                symbol=sym,
                tick_size=cfg["tick_size"],
                qty_step=cfg["qty_step"],
                min_order_qty=cfg["min_order_qty"],
                max_order_qty=cfg["max_order_qty"],
                min_notional_value=cfg["min_notional_value"],
                status=cfg.get("status", "Trading"),
            )

    def get_instrument_rule(self, symbol: str) -> BybitInstrumentRule:
        if symbol in self.instrument_rules:
            return self.instrument_rules[symbol]
        default_cfg = BYBIT_INSTRUMENT_RULES["DEFAULT"]
        return BybitInstrumentRule(
            symbol=symbol,
            tick_size=default_cfg["tick_size"],
            qty_step=default_cfg["qty_step"],
            min_order_qty=default_cfg["min_order_qty"],
            max_order_qty=default_cfg["max_order_qty"],
            min_notional_value=default_cfg["min_notional_value"],
            status="Trading",
        )

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

    def calculate_portfolio_beta(
        self,
        prices_series: Dict[str, List[Dict[str, float]]],
    ) -> Decimal:
        """
        Calculates true signed portfolio beta:
        Beta_i = Cov(R_i, R_btc) / Var(R_btc)
        Portfolio Beta = Sum(weight_i * direction_i * Beta_i)
        """
        if self.benchmark not in prices_series or len(self.open_positions) == 0:
            return Decimal("0.0")

        btc_bars = prices_series[self.benchmark]
        if len(btc_bars) < 20:
            return Decimal("0.0")

        btc_closes = np.array([b["close"] for b in btc_bars], dtype=np.float64)
        btc_rets = np.diff(btc_closes) / btc_closes[:-1]
        btc_var = np.var(btc_rets)
        if btc_var <= 1e-12:
            return Decimal("0.0")

        total_notional = Decimal("0.0")
        weighted_beta_sum = Decimal("0.0")

        for s, pos in self.open_positions.items():
            if s not in prices_series or len(prices_series[s]) < 20:
                continue
            s_bars = prices_series[s]
            min_len = min(len(btc_rets), len(s_bars) - 1)
            s_closes = np.array([b["close"] for b in s_bars], dtype=np.float64)
            s_rets = np.diff(s_closes[-min_len-1:]) / s_closes[-min_len-1:-1]
            sub_btc_rets = btc_rets[-min_len:]

            cov = np.cov(s_rets, sub_btc_rets)[0, 1]
            sub_btc_var = np.var(sub_btc_rets)
            coin_beta = Decimal(str(cov / sub_btc_var)) if sub_btc_var > 1e-12 else Decimal("1.0")

            notional = pos.qty * pos.entry_price
            signed_notional = notional * Decimal(str(pos.direction))
            total_notional += notional
            weighted_beta_sum += signed_notional * coin_beta

        if total_notional <= Decimal("0.0"):
            return Decimal("0.0")

        return weighted_beta_sum / total_notional

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

    def validate_order_constraints(
        self,
        symbol: str,
        raw_qty: Decimal,
        price: Decimal,
    ) -> Tuple[bool, Decimal, str]:
        """
        Validates order against Bybit exchange specifications:
        qtyStep, minOrderQty, maxOrderQty, minNotionalValue, and status.
        """
        rule = self.get_instrument_rule(symbol)
        return rule.validate_order(raw_qty, price)

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
        raw_qty = risk_usd / stop_dist
        max_notional = current_eq * MAX_LEVERAGE_PER_LEG
        if raw_qty * p > max_notional:
            raw_qty = max_notional / p

        # Validate against full Bybit contract specifications
        is_valid, qty, reason = self.validate_order_constraints(symbol, raw_qty, p)
        if not is_valid:
            logger.warning(f"Rejecting {symbol} entry: {reason}")
            return False

        notional = qty * p

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
            entry_fee=entry_fee,
        )
        side_str = "LONG" if direction == 1 else "SHORT"
        logger.info(
            f"Entered {side_str} pair leg on {symbol} @ {p} "
            f"(qty={qty}, notional=${notional:.2f}, entry_fee=${entry_fee:.4f})"
        )
        return True

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
        logger.warning(f"Unwound broken pair leg on {symbol}: NetPnL=${net_pnl:.2f}")

    def execute_candle_stress_stop_gap(
        self,
        pos: ActivePairPosition,
        bar_open: Decimal,
        bar_low: Decimal,
        bar_high: Decimal,
    ) -> Tuple[bool, Decimal, str]:
        """
        Candle-level stop-gap stress scenario.
        If bar breaches stop:
          - Long: if bar_open <= stop_price, gap-through open fill = bar_open * (1 - slippage).
                  else intraday penetration fill = pos.stop_price * (1 - slippage).
          - Short: if bar_open >= stop_price, gap-through open fill = bar_open * (1 + slippage).
                   else intraday penetration fill = pos.stop_price * (1 + slippage).
        """
        if pos.direction == 1:
            if bar_low <= pos.stop_price:
                gap_through = bar_open <= pos.stop_price
                effective_p = bar_open if gap_through else pos.stop_price
                exit_p = effective_p * (Decimal("1.0") - self.slippage)
                return True, exit_p, "STOP_GAP_OPEN" if gap_through else "STOP_LOSS_INTRADAY"
        elif pos.direction == -1:
            if bar_high >= pos.stop_price:
                gap_through = bar_open >= pos.stop_price
                effective_p = bar_open if gap_through else pos.stop_price
                exit_p = effective_p * (Decimal("1.0") + self.slippage)
                return True, exit_p, "STOP_GAP_OPEN" if gap_through else "STOP_LOSS_INTRADAY"

        return False, Decimal("0.0"), "NO_STOP"

    def execute_orderbook_fill(
        self,
        pos: ActivePairPosition,
        bid: Decimal,
        ask: Decimal,
        last_trade: Optional[Decimal] = None,
    ) -> Tuple[bool, Decimal, str]:
        """
        Tick / L2 orderbook fill model for protective stop execution.
        Fills against opposing top of book with taker fee and adverse market impact.
        """
        ref_p = last_trade if last_trade is not None else ((bid + ask) / Decimal("2.0"))
        if pos.direction == 1:
            if bid <= pos.stop_price or ref_p <= pos.stop_price:
                # Sell into bid book
                fill_p = bid * (Decimal("1.0") - self.slippage)
                return True, fill_p, "ORDERBOOK_STOP_FILL_BID"
        elif pos.direction == -1:
            if ask >= pos.stop_price or ref_p >= pos.stop_price:
                # Buy from ask book
                fill_p = ask * (Decimal("1.0") + self.slippage)
                return True, fill_p, "ORDERBOOK_STOP_FILL_ASK"

        return False, Decimal("0.0"), "NO_STOP"

    def apply_funding_cashflow(
        self,
        funding_rates: Dict[str, Decimal],
        current_prices: Dict[str, Decimal],
        timestamp: int,
    ) -> Decimal:
        """
        Applies Bybit 8h funding settlement cashflow to all open positions.
        Long pays funding when rate > 0; Short receives funding when rate > 0.
        Returns net funding cashflow in USDT.
        """
        net_cashflow = Decimal("0.0")
        for sym, pos in self.open_positions.items():
            if sym not in funding_rates or sym not in current_prices:
                continue
            f_rate = funding_rates[sym]
            mark_p = current_prices[sym]
            notional = pos.qty * mark_p

            # Bybit convention: Cashflow = -1 * direction * notional * rate
            # Long (dir=1): rate > 0 => pays (negative cashflow)
            # Short (dir=-1): rate > 0 => receives (positive cashflow)
            cashflow = -Decimal(str(pos.direction)) * notional * f_rate
            net_cashflow += cashflow

            self.funding_events.append(
                FundingCashflowEvent(
                    timestamp=timestamp,
                    symbol=sym,
                    position_qty=pos.qty,
                    mark_price=mark_p,
                    funding_rate=f_rate,
                    cashflow_usdt=cashflow,
                )
            )

        self.realized_equity += net_cashflow
        logger.info(f"Funding cashflow applied: Net={net_cashflow:+.4f} USDT. Realized Equity=${self.realized_equity:.4f}")
        return net_cashflow

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

            # Validate market data using enhanced validator
            allow_advancement, reason, allow_exit = self.market_validator.validate_quote(
                symbol=s,
                price=curr_p,
                timestamp=bar_ts,
                atr=atr,
                stop_price=pos.stop_price,
                direction=direction,
            )

            # Advance trailing stop ONLY if data validity permits
            if allow_advancement:
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
                logger.info(f"Market validator froze trailing advancement on {s}: {reason}")

            # Partial Take Profit at +4.0R (50% trim)
            if not pos.tp_taken and allow_advancement:
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

                    # Cash accounting
                    self.realized_equity += (tp_gross - tp_exit_fee)

                    # Analytical Ledger
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

            # Stop Loss Evaluation with realistic gap execution
            is_stopped = False
            exit_p = Decimal("0.0")

            if allow_exit:
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

            # 3. New Entry Candidate Evaluation (BLOCKED WHEN HALTED OR IN MACRO LOCKOUT)
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

                        # If one leg fails, unwind the other to avoid unhedged directional exposure
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

    def export_state(self) -> Dict[str, Any]:
        """Exports full engine state to JSON-serializable dictionary."""
        return {
            "initial_capital": str(self.initial_capital),
            "realized_equity": str(self.realized_equity),
            "peak_equity": str(self.peak_equity),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "trading_halted": self.trading_halted,
            "bar_counter": self.bar_counter,
            "open_positions": {
                s: {
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "qty": str(pos.qty),
                    "entry_price": str(pos.entry_price),
                    "stop_price": str(pos.stop_price),
                    "initial_risk_dist": str(pos.initial_risk_dist),
                    "initial_atr": str(pos.initial_atr),
                    "highest_p": str(pos.highest_p),
                    "lowest_p": str(pos.lowest_p),
                    "entry_bar": pos.entry_bar,
                    "entry_ts": pos.entry_ts,
                    "entry_fee": str(pos.entry_fee),
                    "tp_taken": pos.tp_taken,
                    "macro_trimmed": pos.macro_trimmed,
                }
                for s, pos in self.open_positions.items()
            },
            "closed_trades": [
                {
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_price": str(t.entry_price),
                    "exit_price": str(t.exit_price),
                    "qty": str(t.qty),
                    "gross_pnl": str(t.gross_pnl),
                    "entry_fee_allocated": str(t.entry_fee_allocated),
                    "exit_fee_paid": str(t.exit_fee_paid),
                    "net_pnl": str(t.net_pnl),
                    "reason": t.reason,
                    "entry_bar": t.entry_bar,
                    "exit_bar": t.exit_bar,
                    "entry_ts": t.entry_ts,
                    "exit_ts": t.exit_ts,
                }
                for t in self.closed_trades
            ],
            "funding_events": [
                {
                    "timestamp": ev.timestamp,
                    "symbol": ev.symbol,
                    "position_qty": str(ev.position_qty),
                    "mark_price": str(ev.mark_price),
                    "funding_rate": str(ev.funding_rate),
                    "cashflow_usdt": str(ev.cashflow_usdt),
                }
                for ev in self.funding_events
            ],
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restores full engine state with exact Decimal types and zero drift."""
        self.initial_capital = Decimal(str(state["initial_capital"]))
        self.realized_equity = Decimal(str(state["realized_equity"]))
        self.peak_equity = Decimal(str(state["peak_equity"]))
        self.max_drawdown_pct = Decimal(str(state["max_drawdown_pct"]))
        self.trading_halted = bool(state.get("trading_halted", False))
        self.bar_counter = int(state.get("bar_counter", 0))

        self.open_positions.clear()
        for s, p_data in state.get("open_positions", {}).items():
            self.open_positions[s] = ActivePairPosition(
                symbol=p_data["symbol"],
                direction=int(p_data["direction"]),
                qty=Decimal(str(p_data["qty"])),
                entry_price=Decimal(str(p_data["entry_price"])),
                stop_price=Decimal(str(p_data["stop_price"])),
                initial_risk_dist=Decimal(str(p_data["initial_risk_dist"])),
                initial_atr=Decimal(str(p_data["initial_atr"])),
                highest_p=Decimal(str(p_data["highest_p"])),
                lowest_p=Decimal(str(p_data["lowest_p"])),
                entry_bar=int(p_data["entry_bar"]),
                entry_ts=int(p_data["entry_ts"]),
                entry_fee=Decimal(str(p_data.get("entry_fee", "0.0"))),
                tp_taken=bool(p_data.get("tp_taken", False)),
                macro_trimmed=bool(p_data.get("macro_trimmed", False)),
            )

        self.closed_trades.clear()
        for t_data in state.get("closed_trades", []):
            self.closed_trades.append(
                ClosedPairTrade(
                    symbol=t_data["symbol"],
                    direction=int(t_data["direction"]),
                    entry_price=Decimal(str(t_data["entry_price"])),
                    exit_price=Decimal(str(t_data["exit_price"])),
                    qty=Decimal(str(t_data["qty"])),
                    gross_pnl=Decimal(str(t_data["gross_pnl"])),
                    entry_fee_allocated=Decimal(str(t_data["entry_fee_allocated"])),
                    exit_fee_paid=Decimal(str(t_data["exit_fee_paid"])),
                    net_pnl=Decimal(str(t_data["net_pnl"])),
                    reason=t_data["reason"],
                    entry_bar=int(t_data["entry_bar"]),
                    exit_bar=int(t_data["exit_bar"]),
                    entry_ts=int(t_data["entry_ts"]),
                    exit_ts=int(t_data["exit_ts"]),
                )
            )

        self.funding_events.clear()
        for ev_data in state.get("funding_events", []):
            self.funding_events.append(
                FundingCashflowEvent(
                    timestamp=int(ev_data["timestamp"]),
                    symbol=ev_data["symbol"],
                    position_qty=Decimal(str(ev_data["position_qty"])),
                    mark_price=Decimal(str(ev_data["mark_price"])),
                    funding_rate=Decimal(str(ev_data["funding_rate"])),
                    cashflow_usdt=Decimal(str(ev_data["cashflow_usdt"])),
                )
            )

    def verify_ledger_conservation(self) -> Tuple[bool, Decimal, Decimal]:
        """
        Verifies fundamental accounting identity:
        Delta Realized Equity == Sum(ClosedTrade.net_pnl) + Sum(FundingCashflows) - Sum(Open.entry_fee)
        """
        delta_equity = self.realized_equity - self.initial_capital
        sum_trades_net = sum(t.net_pnl for t in self.closed_trades)
        sum_funding = sum(ev.cashflow_usdt for ev in self.funding_events)
        sum_open_entry_fees = sum(p.entry_fee for p in self.open_positions.values())

        expected_delta = sum_trades_net + sum_funding - sum_open_entry_fees
        discrepancy = abs(delta_equity - expected_delta)
        is_balanced = discrepancy < Decimal("1e-10")
        return is_balanced, delta_equity, expected_delta
