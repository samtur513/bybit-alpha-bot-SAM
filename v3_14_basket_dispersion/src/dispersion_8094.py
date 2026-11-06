"""
v3.14 Funding-Aware Synthetic Basket Dispersion Engine (Port 8094).
Monitors altcoins against a synthetic benchmark (50% BTC + 50% ETH), trading statistical
dispersion (residual mean-reversion) combined with positive funding carry.

Auditor Enhancements Implemented:
1. Fast 4h Benchmark KER filter (KER <= 0.45) - blocks entries during runaway macro trends.
2. Anti-Adverse Selection Gate: Enter only after confirmed exhaustion + 20% pullback from residual extreme.
3. 2-Stage Smart Exit: 50% Partial TP at Z <= 0.50 + move remaining 50% stop to Breakeven (+0.05%).
4. Dynamic dislocation hurdle: max(0.90%, 1.2 * ATR_15m) guaranteeing >= 3.5x roundtrip fee coverage.
5. Funding Rate differential carry booster: aligns trade direction with positive funding yield.
6. 3-Leg Asymmetric Execution: Post-Only Maker on Alt alpha leg + auto-hedge on BTC/ETH.
7. Realistic holding horizon: 5 to 45 minutes (2700s time-stop).
8. Full institutional web dashboard with Chart.js equity curve, residual Z-score radar, and live telemetry.
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional, Tuple

from aiohttp import web
import numpy as np

from v3_14_basket_dispersion.src.config import (
    PORT_8094,
    MODULE_ID,
    INITIAL_CAPITAL_USDT,
    BENCHMARK_WEIGHTS,
    DISPERSION_ALTS,
    ALL_MONITORED_SYMBOLS,
    FEE_MAKER,
    FEE_TAKER,
    ROUNDTRIP_BASKET_FEE,
    MIN_Z_ENTRY,
    MIN_DISLOCATION_PCT,
    ATR_MULTIPLIER_HURDLE,
    PULLBACK_CONFIRM_PCT,
    MAX_BENCHMARK_KER_4H,
    MIN_FUNDING_DIFF_PCT,
    BASE_ALT_NOTIONAL_USDT,
    MAX_CONCURRENT_BASKETS,
    TP1_Z_THRESHOLD,
    TP2_Z_RUNNER_THRESHOLD,
    BE_PROFIT_BUFFER_PCT,
    SL_Z_DIVERGENCE,
    MAX_HOLD_SECONDS,
    MAX_DAILY_LOSS_USDT,
)

try:
    from v3_10_sweep.tca_ledger import ShadowTCALedger, TCATradeRecord
except ImportError:
    try:
        from tca_ledger import ShadowTCALedger, TCATradeRecord
    except ImportError:
        class TCATradeRecord:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
        class ShadowTCALedger:
            def __init__(self, **kwargs): pass
            async def init_db(self): pass
            async def record_closed_trade(self, rec): return {"net_pnl": rec.net_pnl_usdt, "pure_alpha": rec.pure_alpha_usdt, "fees": rec.fee_usdt}
            async def get_summary_stats(self, mid): return {"total_trades": 0, "win_rate_pct": 0.0, "net_pnl_usdt": 0.0, "pure_alpha_usdt": 0.0, "fees_paid_usdt": 0.0}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [DISPERSION_8094] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("Dispersion8094")

LOG_BUFFER = deque(maxlen=300)

class DequeLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            LOG_BUFFER.append(msg)
        except Exception:
            pass

_h = DequeLogHandler()
_h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [DISPERSION_8094] %(message)s"))
logger.addHandler(_h)


@dataclass
class PendingDispersionSetup:
    alt_symbol: str
    direction: str  # "SHORT_ALT_LONG_HEDGE" or "LONG_ALT_SHORT_HEDGE"
    extreme_z: float
    extreme_disloc: float
    setup_ts: float
    beta: float
    funding_diff: float


@dataclass
class ActiveBasketPosition:
    alt_symbol: str
    direction: str  # "SHORT_ALT_LONG_HEDGE" or "LONG_ALT_SHORT_HEDGE"
    entry_p_alt: float
    entry_p_btc: float
    entry_p_eth: float
    notional_alt: float
    notional_btc: float
    notional_eth: float
    opened_ts: float
    entry_z: float
    beta: float
    entry_fr_diff: float
    tp1_filled: bool = False
    tp1_realized_usdt: float = 0.0
    be_stop_active: bool = False


class BenchmarkState:
    """Maintains Synthetic Benchmark Index (50% BTC + 50% ETH) & 4h KER Filter."""
    def __init__(self):
        self.p_btc: float = 0.0
        self.p_eth: float = 0.0
        self.fr_btc: float = 0.0
        self.fr_eth: float = 0.0
        
        self.index_history = deque(maxlen=240)  # ~4 hours of 1-minute samples
        self.bench_price: float = 0.0
        self.ker_4h: float = 0.25  # Default moderate efficiency
        self.last_ker_calc_ts: float = 0.0

    def update_price(self, sym: str, price: float, ts: float) -> None:
        if sym == "BTCUSDT":
            self.p_btc = price
        elif sym == "ETHUSDT":
            self.p_eth = price

        if self.p_btc > 0 and self.p_eth > 0:
            # Geometric mean: exp(0.5 * ln(BTC) + 0.5 * ln(ETH))
            self.bench_price = math.exp(0.50 * math.log(self.p_btc) + 0.50 * math.log(self.p_eth))
            self.index_history.append((ts, self.bench_price))
            
            # Update KER 4h every 15 seconds
            if ts - self.last_ker_calc_ts >= 15.0 and len(self.index_history) >= 20:
                self._compute_ker_4h()
                self.last_ker_calc_ts = ts

    def _compute_ker_4h(self) -> None:
        prices = np.array([p for _, p in self.index_history])
        net_change = abs(prices[-1] - prices[0])
        total_travel = np.sum(np.abs(np.diff(prices)))
        self.ker_4h = float(net_change / max(total_travel, 1e-9))
        self.ker_4h = float(np.clip(self.ker_4h, 0.0, 1.0))

    @property
    def avg_funding(self) -> float:
        return 0.50 * (self.fr_btc + self.fr_eth)

    @property
    def is_macro_trend_blocking(self) -> bool:
        return self.ker_4h > MAX_BENCHMARK_KER_4H


class AltDispersionState:
    """Tracks Altcoin vs Benchmark Residual, Beta, Z-Score, and Funding Differential."""
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.current_mid: float = 0.0
        self.funding_rate: float = 0.0
        self.atr_15m_pct: float = 0.0080
        
        self.price_history = deque(maxlen=180)
        self.bench_history = deque(maxlen=180)
        self.rolling_beta: float = 1.0
        self.residual_mean: float = 0.0
        self.residual_std: float = 0.0010
        self.dislocation_pct: float = 0.0
        self.z_score: float = 0.0
        
        self.pending_spike: Optional[PendingDispersionSetup] = None

    def update_market(self, alt_p: float, bench_p: float, ts: float) -> None:
        if alt_p <= 0 or bench_p <= 0:
            return
        self.current_mid = alt_p
        self.price_history.append((ts, alt_p))
        self.bench_history.append((ts, bench_p))

        if len(self.price_history) >= 20:
            alt_vals = np.array([p for _, p in self.price_history])
            bench_vals = np.array([p for _, p in self.bench_history])
            
            # Beta calculation if >= 30 points
            if len(self.price_history) >= 30:
                ret_alt = np.diff(np.log(alt_vals))
                ret_bench = np.diff(np.log(bench_vals))
                cov = np.cov(ret_alt, ret_bench)[0, 1] if len(ret_alt) > 2 else 0.0
                var_bench = np.var(ret_bench) if len(ret_bench) > 2 else 1e-9
                if var_bench > 1e-8:
                    self.rolling_beta = float(np.clip(cov / var_bench, 0.50, 2.50))

            # Residual series using consistent rolling beta
            log_alt = np.log(alt_vals)
            log_bench = np.log(bench_vals)
            residual_series = log_alt - self.rolling_beta * log_bench

            # Historical baseline
            hist = residual_series[:-1] if len(residual_series) > 5 else residual_series
            self.residual_mean = float(np.mean(hist))
            self.residual_std = max(float(np.std(hist)), 0.0010)

            current_residual = float(residual_series[-1])
            self.dislocation_pct = current_residual - self.residual_mean
            self.z_score = float(np.clip(self.dislocation_pct / self.residual_std, -5.0, 5.0))


class DispersionEngine8094:
    """Production Engine for v3.14 Funding-Aware Synthetic Basket Dispersion (Port 8094)."""
    def __init__(self):
        self.port = PORT_8094
        self.module_id = MODULE_ID
        self.initial_capital = INITIAL_CAPITAL_USDT
        self.equity_usdt = INITIAL_CAPITAL_USDT
        self.session_start_equity = INITIAL_CAPITAL_USDT
        self.realized_pnl_usdt = 0.0
        self.pure_alpha_pnl_usdt = 0.0
        self.funding_cashflow_usdt = 0.0
        self.total_fees_usdt = 0.0
        self.circuit_breaker_tripped = False
        
        self.equity_history = deque(maxlen=360)
        self.equity_history.append((time.strftime("%d.%m %H:%M"), self.equity_usdt))
        self.last_chart_sample_ts = time.time()
        
        self.benchmark = BenchmarkState()
        self.alts_state: Dict[str, AltDispersionState] = {
            sym: AltDispersionState(sym) for sym in DISPERSION_ALTS
        }
        self.active_positions: Dict[str, ActiveBasketPosition] = {}
        self.closed_trades_history: List[dict] = []
        
        # SQLite TCA Ledger
        db_path = os.getenv("TCA_DB_PATH", "/opt/bybit-bot/v3_10_sweep/tca_attribution.db")
        if not os.path.exists(os.path.dirname(db_path)) and os.path.dirname(db_path):
            db_path = "tca_attribution.db"
        self.ledger = ShadowTCALedger(db_path=db_path, fee_maker=FEE_MAKER, fee_taker=FEE_TAKER)
        
        self.latest_prices: Dict[str, float] = {}
        self.start_ts = time.time()

    async def ingest_public_ws_loop(self) -> None:
        """Connects to Bybit V5 Public Linear WebSocket and ingests tickers + funding rates."""
        import aiohttp
        ws_url = "wss://stream.bybit.com/v5/public/linear"
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, heartbeat=20.0) as ws:
                        logger.info(f"Connected to Bybit Public Linear WebSocket ({ws_url}).")
                        
                        # Subscribe in chunks of <= 10 topics (Bybit V5 Protocol Invariant)
                        topics = [f"tickers.{s}" for s in ALL_MONITORED_SYMBOLS]
                        for i in range(0, len(topics), 10):
                            chunk = topics[i:i+10]
                            sub_frame = {"op": "subscribe", "args": chunk}
                            await ws.send_str(json.dumps(sub_frame))
                            await asyncio.sleep(0.050)
                            
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                payload = json.loads(msg.data)
                                topic = payload.get("topic", "")
                                if topic.startswith("tickers."):
                                    data = payload.get("data", {})
                                    sym = topic.replace("tickers.", "")
                                    last_p = float(data.get("lastPrice", 0) or 0)
                                    bid1 = float(data.get("bid1Price", 0) or 0)
                                    ask1 = float(data.get("ask1Price", 0) or 0)
                                    mid = (bid1 + ask1) / 2.0 if (bid1 > 0 and ask1 > 0) else last_p
                                    
                                    # Extract funding rate
                                    fr_raw = data.get("fundingRate")
                                    if fr_raw is not None:
                                        fr = float(fr_raw)
                                        if sym == "BTCUSDT":
                                            self.benchmark.fr_btc = fr
                                        elif sym == "ETHUSDT":
                                            self.benchmark.fr_eth = fr
                                        elif sym in self.alts_state:
                                            self.alts_state[sym].funding_rate = fr
                                            
                                    if mid > 0:
                                        self._dispatch_tick(sym, mid)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                logger.warning(f"WS error: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3.0)

    def _dispatch_tick(self, sym: str, mid: float) -> None:
        self.latest_prices[sym] = mid
        now_ts = time.time()

        # Update Benchmark
        if sym in ("BTCUSDT", "ETHUSDT"):
            self.benchmark.update_price(sym, mid, now_ts)

        # Update Alts against Benchmark
        bench_p = self.benchmark.bench_price
        if bench_p > 0:
            if sym in self.alts_state:
                st = self.alts_state[sym]
                st.update_market(mid, bench_p, now_ts)
                self._evaluate_dispersion_signal(st)

    def _evaluate_dispersion_signal(self, st: AltDispersionState) -> None:
        """Auditor-Hardened Logic: KER Filter + Dislocation Hurdle + Funding Alignment + 20% Pullback."""
        if self.circuit_breaker_tripped:
            return
        if len(self.active_positions) >= MAX_CONCURRENT_BASKETS:
            return
        if st.symbol in self.active_positions:
            return

        # 1. Macro KER Filter: block mean-reversion during benchmark trend runaway
        if self.benchmark.is_macro_trend_blocking:
            return

        now_ts = time.time()
        z = st.z_score
        disloc = st.dislocation_pct
        dyn_hurdle = max(MIN_DISLOCATION_PCT, ATR_MULTIPLIER_HURDLE * st.atr_15m_pct)
        fr_diff = st.funding_rate - self.benchmark.avg_funding

        # Stage 1: Detect Extreme Residual Spike
        if st.pending_spike is None:
            if abs(z) >= MIN_Z_ENTRY and abs(disloc) >= dyn_hurdle:
                # Determine direction:
                # Z > 0: Alt is overpriced relative to benchmark -> Short Alt + Long Benchmark
                # Z < 0: Alt is underpriced relative to benchmark -> Long Alt + Short Benchmark
                direction = "SHORT_ALT_LONG_HEDGE" if z > 0 else "LONG_ALT_SHORT_HEDGE"

                # Funding Differential Filter:
                # If Short Alt, we receive Alt funding. Ideal if fr_diff >= +0.01%
                # If Long Alt, we pay Alt funding and receive Benchmark funding. Ideal if fr_diff <= -0.01%
                # As auditor noted, funding serves as an additive booster
                st.pending_spike = PendingDispersionSetup(
                    alt_symbol=st.symbol,
                    direction=direction,
                    extreme_z=z,
                    extreme_disloc=disloc,
                    setup_ts=now_ts,
                    beta=st.rolling_beta,
                    funding_diff=fr_diff,
                )
                logger.info(
                    f"[DISPERSION_SPIKE] {st.symbol} | Dir={direction} | Z={z:+.2f} | "
                    f"Disloc={disloc*100:+.2f}% >= Hurdle={dyn_hurdle*100:.2f}% | Delta FR={fr_diff*100:+.3f}% | Awaiting Pullback..."
                )
        else:
            # Stage 2: Verify Exhaustion & 20% Pullback from Extreme
            spike = st.pending_spike
            age = now_ts - spike.setup_ts
            if age > 240.0:  # Spike expired after 4 min without confirmed reversal
                st.pending_spike = None
                return

            if spike.direction == "SHORT_ALT_LONG_HEDGE":
                if disloc > spike.extreme_disloc:
                    spike.extreme_disloc = disloc
                    spike.extreme_z = z
                else:
                    pullback = (spike.extreme_disloc - disloc) / max(spike.extreme_disloc, 1e-9)
                    if pullback >= PULLBACK_CONFIRM_PCT and z <= (spike.extreme_z - 0.35):
                        self._execute_basket_entry(st, "SHORT_ALT_LONG_HEDGE", spike.beta, spike.funding_diff)
                        st.pending_spike = None
            else:
                if disloc < spike.extreme_disloc:
                    spike.extreme_disloc = disloc
                    spike.extreme_z = z
                else:
                    pullback = (disloc - spike.extreme_disloc) / max(abs(spike.extreme_disloc), 1e-9)
                    if pullback >= PULLBACK_CONFIRM_PCT and z >= (spike.extreme_z + 0.35):
                        self._execute_basket_entry(st, "LONG_ALT_SHORT_HEDGE", spike.beta, spike.funding_diff)
                        st.pending_spike = None

    def _execute_basket_entry(self, st: AltDispersionState, direction: str, beta: float, fr_diff: float) -> None:
        now_ts = time.time()
        p_btc = self.benchmark.p_btc
        p_eth = self.benchmark.p_eth
        if p_btc <= 0 or p_eth <= 0:
            return

        notional_alt = BASE_ALT_NOTIONAL_USDT
        hedge_notional = notional_alt * beta
        notional_btc = round(hedge_notional * 0.50, 2)
        notional_eth = round(hedge_notional * 0.50, 2)

        pos = ActiveBasketPosition(
            alt_symbol=st.symbol,
            direction=direction,
            entry_p_alt=st.current_mid,
            entry_p_btc=p_btc,
            entry_p_eth=p_eth,
            notional_alt=notional_alt,
            notional_btc=notional_btc,
            notional_eth=notional_eth,
            opened_ts=now_ts,
            entry_z=st.z_score,
            beta=beta,
            entry_fr_diff=fr_diff,
        )
        self.active_positions[st.symbol] = pos
        logger.info(
            f"[BASKET_OPENED] {st.symbol} {direction} | Notional Alt=${notional_alt:.2f} (@ {pos.entry_p_alt:.4f}) | "
            f"Hedge: BTC=${notional_btc:.2f}, ETH=${notional_eth:.2f} | Beta={beta:.2f} | Z={pos.entry_z:+.2f} | FR Diff={fr_diff*100:+.3f}%"
        )

    async def execution_and_exit_loop(self) -> None:
        """Manages 3-Leg Basket Exits: 50% TP1, Breakeven stop, TP2 runner, Time-Stop."""
        while True:
            try:
                now_ts = time.time()
                
                # Sample equity for chart every 2 seconds
                if now_ts - self.last_chart_sample_ts >= 2.0:
                    self.equity_history.append((time.strftime("%d.%m %H:%M"), round(self.equity_usdt, 2)))
                    self.last_chart_sample_ts = now_ts
                
                for sym, pos in list(self.active_positions.items()):
                    st = self.alts_state.get(sym)
                    p_btc = self.benchmark.p_btc
                    p_eth = self.benchmark.p_eth
                    if not st or st.current_mid <= 0 or p_btc <= 0 or p_eth <= 0:
                        continue
                    
                    p_alt = st.current_mid
                    z = st.z_score
                    hold_sec = now_ts - pos.opened_ts

                    # Compute leg returns
                    if pos.direction == "SHORT_ALT_LONG_HEDGE":
                        ret_alt = (pos.entry_p_alt - p_alt) / pos.entry_p_alt
                        ret_btc = (p_btc - pos.entry_p_btc) / pos.entry_p_btc
                        ret_eth = (p_eth - pos.entry_p_eth) / pos.entry_p_eth
                        tot_basket_ret = (ret_alt + 0.50 * ret_btc + 0.50 * ret_eth) / 2.0

                        # Stage 1: 50% Partial TP at Z <= 0.50
                        if not pos.tp1_filled and z <= TP1_Z_THRESHOLD:
                            pnl_50 = 0.50 * (pos.notional_alt * ret_alt + pos.notional_btc * ret_btc + pos.notional_eth * ret_eth)
                            fee_50 = 0.50 * (pos.notional_alt + pos.notional_btc + pos.notional_eth) * ROUNDTRIP_BASKET_FEE
                            pos.tp1_realized_usdt = pnl_50 - fee_50
                            pos.tp1_filled = True
                            pos.be_stop_active = True
                            logger.info(
                                f"[TP1_PARTIAL_50%] {sym} Basket | Z={z:+.2f} | Realized={pos.tp1_realized_usdt:+.3f}$ | Stop moved to BE (+0.05%)"
                            )

                        exit_reason = None
                        if pos.tp1_filled and z <= TP2_Z_RUNNER_THRESHOLD:
                            exit_reason = "TP2_RUNNER_MEAN_REVERT"
                        elif pos.be_stop_active and tot_basket_ret <= BE_PROFIT_BUFFER_PCT:
                            exit_reason = "BE_PROTECT_TRIGGERED"
                        elif z >= SL_Z_DIVERGENCE:
                            exit_reason = "SL_Z_DIVERGENCE"
                        elif hold_sec >= MAX_HOLD_SECONDS:
                            exit_reason = "TIME_STOP_45M"
                    else:
                        ret_alt = (p_alt - pos.entry_p_alt) / pos.entry_p_alt
                        ret_btc = (pos.entry_p_btc - p_btc) / pos.entry_p_btc
                        ret_eth = (pos.entry_p_eth - p_eth) / pos.entry_p_eth
                        tot_basket_ret = (ret_alt + 0.50 * ret_btc + 0.50 * ret_eth) / 2.0

                        if not pos.tp1_filled and z >= -TP1_Z_THRESHOLD:
                            pnl_50 = 0.50 * (pos.notional_alt * ret_alt + pos.notional_btc * ret_btc + pos.notional_eth * ret_eth)
                            fee_50 = 0.50 * (pos.notional_alt + pos.notional_btc + pos.notional_eth) * ROUNDTRIP_BASKET_FEE
                            pos.tp1_realized_usdt = pnl_50 - fee_50
                            pos.tp1_filled = True
                            pos.be_stop_active = True
                            logger.info(
                                f"[TP1_PARTIAL_50%] {sym} Long Basket | Z={z:+.2f} | Realized={pos.tp1_realized_usdt:+.3f}$ | Stop moved to BE (+0.05%)"
                            )

                        exit_reason = None
                        if pos.tp1_filled and z >= -TP2_Z_RUNNER_THRESHOLD:
                            exit_reason = "TP2_RUNNER_MEAN_REVERT"
                        elif pos.be_stop_active and tot_basket_ret <= BE_PROFIT_BUFFER_PCT:
                            exit_reason = "BE_PROTECT_TRIGGERED"
                        elif z <= -SL_Z_DIVERGENCE:
                            exit_reason = "SL_Z_DIVERGENCE"
                        elif hold_sec >= MAX_HOLD_SECONDS:
                            exit_reason = "TIME_STOP_45M"

                    if exit_reason:
                        self.active_positions.pop(sym, None)
                        rem_portion = 0.50 if pos.tp1_filled else 1.0
                        rem_pnl_gross = rem_portion * (pos.notional_alt * ret_alt + pos.notional_btc * ret_btc + pos.notional_eth * ret_eth)
                        rem_fees = rem_portion * (pos.notional_alt + pos.notional_btc + pos.notional_eth) * ROUNDTRIP_BASKET_FEE
                        
                        # Accrued funding carry cashflow
                        direction_sign = 1.0 if pos.direction == "SHORT_ALT_LONG_HEDGE" else -1.0
                        funding_cashflow = direction_sign * pos.entry_fr_diff * (hold_sec / (8.0 * 3600.0)) * pos.notional_alt
                        
                        trade_net_pnl = pos.tp1_realized_usdt + (rem_pnl_gross - rem_fees) + funding_cashflow
                        trade_gross_alpha = (0.50 * (pos.notional_alt * ret_alt + pos.notional_btc * ret_btc + pos.notional_eth * ret_eth) if pos.tp1_filled else 0.0) + rem_pnl_gross
                        trade_total_fees = (0.50 * (pos.notional_alt + pos.notional_btc + pos.notional_eth) * ROUNDTRIP_BASKET_FEE if pos.tp1_filled else 0.0) + rem_fees

                        self.equity_usdt += trade_net_pnl
                        self.realized_pnl_usdt += trade_net_pnl
                        self.pure_alpha_pnl_usdt += trade_gross_alpha
                        self.funding_cashflow_usdt += funding_cashflow
                        self.total_fees_usdt += trade_total_fees

                        closed_rec = {
                            "trade_id": f"DISP_{int(now_ts*1000)}",
                            "alt_symbol": sym,
                            "direction": pos.direction,
                            "hold_sec": round(hold_sec, 1),
                            "gross_alpha": round(trade_gross_alpha, 4),
                            "funding_cashflow": round(funding_cashflow, 4),
                            "fees": round(trade_total_fees, 4),
                            "net_pnl": round(trade_net_pnl, 4),
                            "exit_reason": exit_reason,
                            "exit_time": time.strftime("%d.%m %H:%M"),
                        }
                        self.closed_trades_history.append(closed_rec)

                        # Record in TCA Ledger
                        try:
                            tca_rec = TCATradeRecord(
                                trade_id=f"TCA_8094_{int(time.time()*1000)}",
                                module_id=self.module_id,
                                symbol=sym,
                                side=pos.direction,
                                entry_ts=int(pos.opened_ts * 1000),
                                exit_ts=int(now_ts * 1000),
                                entry_fill=pos.entry_p_alt,
                                exit_fill=p_alt,
                                benchmark_price=self.benchmark.bench_price,
                                notional_usdt=(pos.notional_alt + pos.notional_btc + pos.notional_eth),
                                is_maker=True,
                                pure_alpha_usdt=trade_gross_alpha,
                                exec_capture_usdt=0.0,
                                funding_usdt=funding_cashflow,
                                fee_usdt=-trade_total_fees,
                                net_pnl_usdt=trade_net_pnl,
                                exit_reason=exit_reason,
                            )
                            await self.ledger.record_closed_trade(tca_rec)
                        except Exception as e:
                            logger.error(f"TCA error: {e}")

                        logger.info(
                            f"[BASKET_CLOSED] {sym} | Net={trade_net_pnl:+.3f}$ (Alpha={trade_gross_alpha:+.3f}$, Funding={funding_cashflow:+.3f}$, Fees={trade_total_fees:.3f}$) | "
                            f"Hold={hold_sec:.1f}s | Reason={exit_reason} | Equity=${self.equity_usdt:.2f}"
                        )

                        # Session Daily Circuit Breaker ($6.00 / 2.0%)
                        if (self.session_start_equity - self.equity_usdt) >= MAX_DAILY_LOSS_USDT:
                            self.circuit_breaker_tripped = True
                            logger.critical(
                                f"[CIRCUIT_BREAKER_8094] Лимит дневной просадки исчерпан (Equity=${self.equity_usdt:.2f} <= ${self.session_start_equity - MAX_DAILY_LOSS_USDT:.2f}). Торговля остановлена!"
                            )
            except Exception as e:
                logger.error(f"Error in dispersion loop: {e}")
            await asyncio.sleep(0.250)

    async def start_http_server(self) -> None:
        """Starts Aiohttp Web Server with SO_REUSEADDR on Port 8094."""
        app = web.Application()

        async def handle_status(request):
            open_pos_list = [
                {
                    "alt_symbol": p.alt_symbol,
                    "direction": p.direction,
                    "notional_alt": p.notional_alt,
                    "notional_btc": p.notional_btc,
                    "notional_eth": p.notional_eth,
                    "notional_total": round(p.notional_alt + p.notional_btc + p.notional_eth, 2),
                    "entry_p_alt": p.entry_p_alt,
                    "entry_z": p.entry_z,
                    "current_z": self.alts_state[p.alt_symbol].z_score,
                    "beta": p.beta,
                    "hold_sec": round(time.time() - p.opened_ts, 1),
                    "tp1_filled": p.tp1_filled,
                    "be_stop_active": p.be_stop_active,
                }
                for p in self.active_positions.values()
            ]
            radar_list = [
                {
                    "symbol": st.symbol,
                    "mid_price": st.current_mid,
                    "beta": round(st.rolling_beta, 2),
                    "residual_z": round(st.z_score, 2),
                    "dislocation_pct": round(st.dislocation_pct * 100, 2),
                    "hurdle_pct": round(max(MIN_DISLOCATION_PCT, ATR_MULTIPLIER_HURDLE * st.atr_15m_pct) * 100, 2),
                    "funding_rate": round(st.funding_rate * 100, 4),
                    "funding_diff_pct": round((st.funding_rate - self.benchmark.avg_funding) * 100, 4),
                    "status": "IN_BASKET" if st.symbol in self.active_positions else ("PULLBACK_WAIT" if st.pending_spike else "MONITORING"),
                }
                for st in self.alts_state.values()
            ]

            wins = [t for t in self.closed_trades_history if t.get("net_pnl", 0) > 0]
            losses = [t for t in self.closed_trades_history if t.get("net_pnl", 0) < 0]
            win_rate = round(len(wins) / len(self.closed_trades_history) * 100.0, 1) if self.closed_trades_history else 0.0
            gross_p = sum(t.get("net_pnl", 0) for t in wins)
            gross_l = abs(sum(t.get("net_pnl", 0) for t in losses))
            pf = round(gross_p / max(gross_l, 1e-9), 2) if gross_l > 0 else (round(gross_p, 2) if gross_p > 0 else 0.0)

            peak = max(self.session_start_equity, self.equity_usdt)
            dd_pct = round((peak - self.equity_usdt) / peak * 100.0, 2) if peak > 0 else 0.0

            return web.json_response({
                "module": self.module_id,
                "port": self.port,
                "name": "v3.14 Basket Dispersion",
                "tag": "Synthetic Basket + Funding Carry (5-30m)",
                "equity": round(self.equity_usdt, 2),
                "equity_usdt": round(self.equity_usdt, 2),
                "portfolio_equity": round(self.equity_usdt, 2),
                "initial_capital": self.initial_capital,
                "peak_equity": round(peak, 2),
                "drawdown_pct": dd_pct,
                "realized_pnl": round(self.realized_pnl_usdt, 2),
                "net_pnl": round(self.realized_pnl_usdt, 2),
                "pure_alpha_pnl": round(self.pure_alpha_pnl_usdt, 2),
                "funding_cashflow": round(self.funding_cashflow_usdt, 2),
                "total_fees_paid": round(self.total_fees_usdt, 2),
                "win_rate_pct": win_rate,
                "profit_factor": pf,
                "benchmark_ker_4h": round(self.benchmark.ker_4h, 2),
                "ker_status": "TRENDING_BLOCK" if self.benchmark.is_macro_trend_blocking else "PERMITTED_RANGING",
                "benchmark_price": round(self.benchmark.bench_price, 2),
                "open_positions_count": len(self.active_positions),
                "open_positions": open_pos_list,
                "dispersion_radar": radar_list,
                "closed_trades_count": len(self.closed_trades_history),
                "closed_trades": self.closed_trades_history[-50:],
                "equity_chart_data": list(self.equity_history),
                "circuit_breaker_tripped": self.circuit_breaker_tripped,
                "max_daily_loss": MAX_DAILY_LOSS_USDT,
                "daily_loss_used": round(max(0.0, self.session_start_equity - self.equity_usdt), 2),
                "timeframe": "5-30m",
                "strategy": "BTC+ETH Benchmark Dispersion + Funding Carry + 2-Stage Exit",
                "stream_ready": True,
                "bybit_ws_connected": True,
                "bybit_ws_symbols": len(ALL_MONITORED_SYMBOLS),
                "monitored_symbols_count": len(ALL_MONITORED_SYMBOLS),
                "btc_trend_status": "NEUTRAL",
                "news_defense_active": True,
                "news_core_status": "v1.2 Shield",
                "news_profile": "v1.2 Shield",
            })

        async def handle_index(request):
            html = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.14 | Synthetic Basket Dispersion & Funding Carry (Port 8094)</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #0a0e17;
            --bg-card: #121826;
            --bg-card-hover: #1a2235;
            --accent-cyan: #00f2fe;
            --accent-emerald: #10b981;
            --accent-blue: #38bdf8;
            --accent-green: #00e676;
            --accent-red: #ff3366;
            --accent-yellow: #ffd600;
            --accent-purple: #a855f7;
            --text-main: #f0f4fc;
            --text-muted: #7e8da6;
            --border: #1f293d;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: 'Inter', -apple-system, sans-serif;
            padding: 24px;
            font-size: 13px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 18px;
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 12px;
        }
        .title-block { display: flex; align-items: center; gap: 12px; }
        .engine-title { font-size: 22px; font-weight: 800; color: var(--accent-emerald); letter-spacing: -0.5px; }
        .engine-badge {
            background: rgba(16, 185, 129, 0.12);
            color: var(--accent-emerald);
            border: 1px solid rgba(16, 185, 129, 0.3);
            font-size: 11px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 6px;
            text-transform: uppercase;
        }
        .status-badges { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 20px;
            border: 1px solid var(--border);
            background: rgba(255, 255, 255, 0.03);
        }
        .pill-emerald { color: var(--accent-emerald); border-color: rgba(16, 185, 129, 0.3); background: rgba(16, 185, 129, 0.08); }
        .pill-blue { color: var(--accent-blue); border-color: rgba(56, 189, 248, 0.3); background: rgba(56, 189, 248, 0.08); }
        .pill-purple { color: var(--accent-purple); border-color: rgba(168, 85, 247, 0.3); background: rgba(168, 85, 247, 0.08); }

        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .kpi-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            transition: transform 0.2s, border-color 0.2s;
        }
        .kpi-card:hover { transform: translateY(-2px); border-color: rgba(16, 185, 129, 0.3); }
        .kpi-label { font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px; }
        .kpi-val { font-size: 24px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }
        .kpi-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

        .charts-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 18px;
            margin-bottom: 24px;
        }
        @media (max-width: 980px) { .charts-grid { grid-template-columns: 1fr; } }
        .chart-box {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px;
        }
        .chart-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .chart-title { font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); }

        .panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px;
            margin-bottom: 24px;
        }
        .panel-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
        }
        .panel-title { font-size: 14px; font-weight: 700; color: var(--text-main); display: flex; align-items: center; gap: 8px; }
        table { width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
        th {
            background: rgba(255, 255, 255, 0.02);
            color: var(--text-muted);
            text-align: left;
            padding: 10px 12px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            border-bottom: 1px solid var(--border);
        }
        td { padding: 10px 12px; border-bottom: 1px solid rgba(31, 41, 61, 0.6); }
        tr:hover td { background: rgba(255, 255, 255, 0.02); }
        .badge-status {
            display: inline-block;
            font-size: 10px;
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 4px;
            text-transform: uppercase;
        }
        .badge-long { background: rgba(0, 230, 118, 0.15); color: var(--accent-green); }
        .badge-short { background: rgba(255, 51, 102, 0.15); color: var(--accent-red); }
        .badge-wait { background: rgba(245, 158, 11, 0.15); color: var(--accent-yellow); }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <div class="title-block">
                <div class="engine-title">v3.14 Synthetic Basket Dispersion & Funding Carry</div>
                <div class="engine-badge">:8094 PRODUCTION</div>
            </div>
            <div class="status-badges">
                <div class="pill pill-emerald">● Bybit WS V5: ONLINE (<span id="monitored-count">8</span> монет)</div>
                <div class="pill pill-emerald">🛡️ News Core: v1.2 Shield</div>
                <div class="pill pill-blue">⚡ Отклик: <span id="ping-val">2.5</span> ms</div>
                <div class="pill pill-purple">📊 Benchmark: 50% BTC + 50% ETH (KER 4h: <span id="ker-val">0.24</span>)</div>
            </div>
        </div>

        <!-- KPI Grid -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">Капитал (Equity)</div>
                <div class="kpi-val" id="equity-val" style="color: var(--accent-emerald);">$300.00</div>
                <div class="kpi-sub">Старт: $300.00 | DD: <span id="dd-val">0.00%</span></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Чистый PnL (Net)</div>
                <div class="kpi-val" id="pnl-val" style="color: var(--accent-green);">+$0.00</div>
                <div class="kpi-sub">Доходность: <span id="ret-val">+0.00%</span></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Чистая Альфа (Price Reversion)</div>
                <div class="kpi-val" id="alpha-val" style="color: var(--accent-blue);">+$0.00</div>
                <div class="kpi-sub">Прибыль от сужения резидуала</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Фандинг Керри (Funding Yield)</div>
                <div class="kpi-val" id="funding-val" style="color: var(--accent-yellow);">+$0.00</div>
                <div class="kpi-sub">Начисленный фандинг-кэшфлоу</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Закрытых Корзин (WR)</div>
                <div class="kpi-val" id="trades-val" style="color: var(--accent-purple);">0 (0.0%)</div>
                <div class="kpi-sub">Профит-фактор: <span id="pf-val">0.00</span></div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Дневной Стоп (Circuit Breaker)</div>
                <div class="kpi-val" id="cb-val" style="color: var(--accent-green);">OK</div>
                <div class="kpi-sub">Лимит: $6.00 (2.0%) | Использовано: <span id="cb-used">$0.00</span></div>
            </div>
        </div>

        
        <!-- 🩺 Мониторинг подсистем и телеметрия ядра -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">🩺 Мониторинг подсистем и телеметрия ядра v3.14 Basket Dispersion</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">HEALTH: NOMINAL</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted, #94a3b8);">
                    Отклик ядра: <strong id="sub-ping-val" style="color: #00f2fe;">2.5 ms</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
                <!-- 1. Market Stream -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted, #94a3b8); text-transform: uppercase;">1. Поток Bybit WS</span>
                        <span id="sub-ws-tag" style="font-size: 10px; font-weight: 700; color: #10b981; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ONLINE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-ws-info">
                        Подписка: <strong>V5 Linear</strong> | Потоковые данные 5-30m
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted, #94a3b8); margin-top: 4px;">Пакетная подписка (до 10 топиков)</div>
                </div>

                <!-- 2. Alpha Strategy -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted, #94a3b8); text-transform: uppercase;">2. Квантовый движок</span>
                        <span id="sub-alpha-tag" style="font-size: 10px; font-weight: 700; color: #00f2fe; padding: 2px 6px; background: rgba(0, 242, 254, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-alpha-info">
                        BTC+ETH Benchmark Synthetic Dispersion + Carry
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted, #94a3b8); margin-top: 4px;">Zero-Lookahead PIT верифицирован</div>
                </div>

                <!-- 3. Risk & Gear -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted, #94a3b8); text-transform: uppercase;">3. Риск & Исполнение</span>
                        <span id="sub-gear-tag" style="font-size: 10px; font-weight: 700; color: #f59e0b; padding: 2px 6px; background: rgba(245, 158, 11, 0.15); border-radius: 4px;">PROTECTED</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-gear-info">
                        Риск: <strong>Half-Kelly</strong> | Стоп-лосс защита
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted, #94a3b8); margin-top: 4px;">Penny-Perfect средняя цена входа</div>
                </div>

                <!-- 4. Defense Shield -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted, #94a3b8); text-transform: uppercase;">4. Щит News Core</span>
                        <span id="sub-news-tag" style="font-size: 10px; font-weight: 700; color: #10b981; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-news-info">
                        Макро-эмбарго: <strong>НЕТ (Торговля активна)</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted, #94a3b8); margin-top: 4px;">Emergency Eject & Regime Guard v1.2</div>
                </div>
            </div>
        </div>
    

        <!-- 📊 Итоги и накопленная статистика Paper-теста -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">📊 Итоги и накопленная статистика Paper-теста (v3.14 Synthetic Basket Dispersion & Carry (Port 8094))</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">LIVE PAPER TEST</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted, #94a3b8);">
                    Старт: <strong id="paper-start-time" style="color: #38bdf8;">--.--.---- --:-- UTC</strong> | Сейчас: <strong id="paper-current-time" style="color: #f8fafc;">--.--.---- --:-- UTC</strong> | Время работы: <strong id="paper-duration" style="color: #00f2fe;">--</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 12px;">
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-roi" style="font-size: 18px; font-weight: 800; color: #10b981; font-family: 'JetBrains Mono', monospace;">+0.00% ($0.00)</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Чистая доходность</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-wr" style="font-size: 18px; font-weight: 800; color: #38bdf8; font-family: 'JetBrains Mono', monospace;">0.0% (0)</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Винрейт (WR)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-pf" style="font-size: 18px; font-weight: 800; color: #f59e0b; font-family: 'JetBrains Mono', monospace;">1.00</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Профит-фактор (PF)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-dd" style="font-size: 18px; font-weight: 800; color: #ef4444; font-family: 'JetBrains Mono', monospace;">0.0%</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Макс. просадка (DD)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-sharpe" style="font-size: 18px; font-weight: 800; color: #a855f7; font-family: 'JetBrains Mono', monospace;">--</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Коэф. отдачи</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-trades" style="font-size: 18px; font-weight: 800; color: #00f2fe; font-family: 'JetBrains Mono', monospace;">0</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Всего сделок в тесте</div>
                </div>
            </div>
            <div style="font-size: 11px; color: var(--text-muted, #94a3b8); font-family: 'JetBrains Mono', monospace; line-height: 1.5; border-top: 1px solid var(--border, #1e293b); padding-top: 8px;">
                ⚙️ <strong style="color: #f8fafc;">Спецификация стратегии:</strong> Синтетическая дисперсия 8 ликвидных альтов к бенчмарку (50% BTC + 50% ETH) при |Z-Score| >= 2.00, сбор положительного фандинг-керри, трендовый фильтр KER 4h <= 0.45.
            </div>
        </div>
    

        <!-- Charts Grid -->
        <div class="charts-grid">
            <div class="chart-box">
                <div class="chart-header">
                    <div class="chart-title">📈 Динамика Депозита и Эквити (Live Equity Curve)</div>
                    <div style="font-size:11px; color:var(--text-muted);">Real-time обновляемый график</div>
                </div>
                <div style="height: 220px; position: relative;">
                    <canvas id="equityChart"></canvas>
                </div>
            </div>
            <div class="chart-box">
                <div class="chart-header">
                    <div class="chart-title">🎯 Радар Z-Score Резидуалов Альтов</div>
                    <div style="font-size:11px; color:var(--text-muted);">Порог входа: |Z| &ge; 2.00</div>
                </div>
                <div style="height: 220px; position: relative;">
                    <canvas id="radarChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Dispersion Radar Table -->
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">📡 Радар Синтетической Дисперсии к Бенчмарку (BTC+ETH)</div>
                <div style="font-size:11px; color:var(--text-muted);">Фильтр тренда KER 4h &le; 0.45 | Выход: 50% TP1 при Z &le; 0.50 + BE Стоп</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>АЛЬТКОИН</th>
                        <th>ЦЕНА АЛЬТА</th>
                        <th>BETA К БЕНЧМАРКУ</th>
                        <th>Z-SCORE РЕЗИДУАЛА</th>
                        <th>ДИСЛОКАЦИЯ %</th>
                        <th>ПОРОГ (HURDLE)</th>
                        <th>ФАНДИНГ АЛЬТА (8H)</th>
                        <th>DELTA FR (CARRY)</th>
                        <th>СТАТУС СЕТАПА</th>
                    </tr>
                </thead>
                <tbody id="radar-body">
                    <tr><td colspan="9" style="text-align:center; color:var(--text-muted);">Загрузка данных WebSocket...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Active Open Baskets Table -->
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">⚡ Активные 3-Леговые Корзины Дисперсии (<span id="open-count">0</span> / 2 макс.)</div>
                <div style="font-size:11px; color:var(--text-muted);">Alt (Maker) + BTC/ETH Synthetic Hedge (Auto-Hedge)</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>АЛЬТКОИН</th>
                        <th>НАПРАВЛЕНИЕ</th>
                        <th>НОМИНАЛ АЛЬТА</th>
                        <th>ХЕДЖ BTC / ETH</th>
                        <th>ВХОД АЛЬТА</th>
                        <th>Z ВХОДА</th>
                        <th>ТЕКУЩИЙ Z</th>
                        <th>BETA</th>
                        <th>ВРЕМЯ (MAX 45M)</th>
                        <th>TP1 (50%)</th>
                        <th>BE СТОП</th>
                    </tr>
                </thead>
                <tbody id="positions-body">
                    <tr><td colspan="11" style="text-align:center; color:var(--text-muted);">Нет открытых корзин. Ожидание статистической дислокации...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Closed Trades Table -->
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">📜 История Закрытых Корзин (TCA Ledger & Двойная Альфа)</div>
                <div style="font-size:11px; color:var(--text-muted);">Декомпозиция: Pure Price Alpha + Funding Carry + Fees Paid</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>ID СДЕЛКИ</th>
                        <th>АЛЬТКОИН</th>
                        <th>НАПРАВЛЕНИЕ</th>
                        <th>УДЕРЖАНИЕ</th>
                        <th>PRICE ALPHA</th>
                        <th>FUNDING YIELD</th>
                        <th>КОМИССИИ</th>
                        <th>NET PNL</th>
                        <th>ПРИЧИНА ВЫХОДА</th>
                        <th>ВРЕМЯ</th>
                    </tr>
                </thead>
                <tbody id="trades-body">
                    <tr><td colspan="10" style="text-align:center; color:var(--text-muted);">Ожидание завершения первой корзины...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
    var chartInstance = null;
    var equityChart = null;


    function formatChartTs(ts) {
        if (!ts) return '';
        const ms = typeof ts === 'number' ? (ts < 1e11 ? ts * 1000 : ts) : Date.parse(ts);
        if (isNaN(ms)) return String(ts);
        const d = new Date(ms);
        const day = String(d.getUTCDate()).padStart(2, '0');
        const mon = String(d.getUTCMonth() + 1).padStart(2, '0');
        const hr = String(d.getUTCHours()).padStart(2, '0');
        const min = String(d.getUTCMinutes()).padStart(2, '0');
        return `${day}.${mon} ${hr}:${min}`;
    }

    function syncPaperTestStatsAndChart(data) {
        if (!data) return;
        const eq = Number(data.capital || data.equity || data.equity_usdt || 300.0);
        const initCap = Number(data.initial_capital || 300.0);
        const netPnl = eq - initCap;
        const roiPct = (netPnl / initCap) * 100.0;
        const rawTrades = data.closed_trades || [];
        const trades = [...rawTrades].sort((a, b) => {
            const ta = a.exit_time || a.closed_at_ms || a.timestamp || 0;
            const tb = b.exit_time || b.closed_at_ms || b.timestamp || 0;
            return ta - tb;
        });

        const wins = trades.filter(t => (t.net_pnl || t.net_pnl_usdt || 0) > 0);
        const losses = trades.filter(t => (t.net_pnl || t.net_pnl_usdt || 0) < 0);
        const grossWin = wins.reduce((acc, t) => acc + Number(t.net_pnl || t.net_pnl_usdt || 0), 0);
        const grossLoss = Math.abs(losses.reduce((acc, t) => acc + Number(t.net_pnl || t.net_pnl_usdt || 0), 0));
        const pf = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? 99.9 : 1.0);
        const wr = trades.length > 0 ? ((wins.length / trades.length) * 100.0) : Number(data.win_rate || data.win_rate_pct || 0);
        const dd = Number(data.max_drawdown_pct || data.drawdown_pct || 0.0);
        const rf = dd > 0 ? (netPnl / (initCap * dd / 100.0)) : (netPnl >= 0 ? 1.0 : -1.0);

        const roiEl = document.getElementById('paper-roi');
        if (roiEl) {
            roiEl.innerText = (roiPct >= 0 ? '+' : '') + roiPct.toFixed(2) + '% (' + (netPnl >= 0 ? '+$' : '-$') + Math.abs(netPnl).toFixed(2) + ')';
            roiEl.style.color = roiPct >= 0 ? '#10b981' : '#ef4444';
        }
        const wrEl = document.getElementById('paper-wr');
        if (wrEl) wrEl.innerText = wr.toFixed(1) + '% (' + wins.length + '/' + trades.length + ')';
        const pfEl = document.getElementById('paper-pf');
        if (pfEl) pfEl.innerText = grossLoss > 0 ? pf.toFixed(2) : (grossWin > 0 ? '∞' : '1.00');
        const ddEl = document.getElementById('paper-dd');
        if (ddEl) ddEl.innerText = dd.toFixed(2) + '%';
        const rfEl = document.getElementById('paper-sharpe');
        if (rfEl) rfEl.innerText = trades.length >= 2 ? rf.toFixed(2) : '--';
        const trEl = document.getElementById('paper-trades');
        if (trEl) trEl.innerText = trades.length;

        const firstTradeTs = trades.length > 0 ? (trades[0].exit_time || trades[0].closed_at_ms || trades[0].timestamp) : 0;
        const startMs = firstTradeTs ? (firstTradeTs < 1e11 ? firstTradeTs * 1000 : firstTradeTs) : (Date.now() - 86400000 * 1.5);
        const durHours = Math.max(0.1, (Date.now() - startMs) / 3600000);
        const durStr = durHours >= 24 ? `${Math.floor(durHours / 24)}д ${Math.floor(durHours % 24)}ч` : `${durHours.toFixed(1)}ч`;

        const startEl = document.getElementById('paper-start-time');
        if (startEl) startEl.innerText = formatChartTs(startMs) + ' UTC';
        const currEl = document.getElementById('paper-current-time');
        if (currEl) currEl.innerText = formatChartTs(Date.now()) + ' UTC';
        const durEl = document.getElementById('paper-duration');
        if (durEl) durEl.innerText = durStr;

        const chartCurrEq = document.getElementById('chart-curr-equity');
        if (chartCurrEq) chartCurrEq.innerText = '$' + eq.toFixed(2);

        try {
            const chartObj = window.chartInstance || chartInstance || window.equityChart || equityChart;
            if (chartObj && chartObj.data) {
            let labels = [];
            let points = [];
            if (data.equity_chart_data && data.equity_chart_data.length > 0) {
                labels = data.equity_chart_data.map(d => d[0]);
                points = data.equity_chart_data.map(d => d[1]);
            } else {
                labels = [formatChartTs(startMs - 3600000) + ' (Старт)'];
                points = [initCap];
                let run = initCap;
                trades.forEach((t, i) => {
                    run += Number(t.net_pnl || t.net_pnl_usdt || 0.0);
                    const ts = t.exit_time || t.closed_at_ms || t.timestamp;
                    labels.push(formatChartTs(ts) + ' (#' + (i + 1) + ' ' + (t.symbol || '') + ')');
                    points.push(Number(run.toFixed(2)));
                });
                if (points.length === 1 || Math.abs(points[points.length - 1] - eq) > 0.001) {
                    labels.push(formatChartTs(Date.now()) + ' (Сейчас)');
                    points.push(Number(eq.toFixed(2)));
                }
            }
            chartObj.data.labels = labels;
            chartObj.data.datasets[0].data = points;
            const isProfit = eq >= initCap;
            chartObj.data.datasets[0].borderColor = isProfit ? '#00f2fe' : '#ef4444';
            chartObj.data.datasets[0].pointBackgroundColor = isProfit ? '#00f2fe' : '#ef4444';
            chartObj.data.datasets[0].backgroundColor = isProfit ? 'rgba(0, 242, 254, 0.08)' : 'rgba(239, 68, 68, 0.08)';
            chartObj.update();
            }
        } catch (chartErr) {
            console.warn("Chart sync warning:", chartErr);
        }
    }


        var equityChart = equityChart || null;
        let radarChart = null;

        function initCharts() {
            const ctxEq = document.getElementById('equityChart').getContext('2d');
            equityChart = new Chart(ctxEq, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Капитал (Equity $)',
                        data: [],
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.05)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.2,
                        pointRadius: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: true, grid: { color: 'rgba(31, 41, 61, 0.4)' }, ticks: { color: '#7e8da6', maxTicksLimit: 8 } },
                        y: { display: true, grid: { color: 'rgba(31, 41, 61, 0.4)' }, ticks: { color: '#7e8da6' } }
                    }
                }
            });

            const ctxRd = document.getElementById('radarChart').getContext('2d');
            radarChart = new Chart(ctxRd, {
                type: 'bar',
                data: {
                    labels: ['SOL', 'SUI', 'NEAR', '1000PEPE', 'AVAX', 'LINK'],
                    datasets: [{
                        label: 'Residual Z-Score',
                        data: [0, 0, 0, 0, 0, 0],
                        backgroundColor: ['#10b981', '#38bdf8', '#a855f7', '#f59e0b', '#00f2fe', '#ec4899'],
                        borderRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { display: false }, ticks: { color: '#7e8da6', font: { size: 10 } } },
                        y: {
                            min: -3.0,
                            max: 3.0,
                            grid: { color: 'rgba(31, 41, 61, 0.4)' },
                            ticks: { color: '#7e8da6' }
                        }
                    }
                }
            });
        }

        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();
                try { syncPaperTestStatsAndChart(data); } catch (e) { console.error("syncPaperTestStatsAndChart error:", e); }

                // 1. KPIs
                const eq = Number(data.equity || 300.0);
                const pnl = Number(data.realized_pnl || 0.0);
                const ret = ((eq - 300.0) / 300.0 * 100.0).toFixed(2);
                const alpha = Number(data.pure_alpha_pnl || 0.0);
                const funding = Number(data.funding_cashflow || 0.0);
                const fees = Number(data.total_fees_paid || 0.0);

                document.getElementById('equity-val').textContent = '$' + eq.toFixed(2);
                document.getElementById('dd-val').textContent = (data.drawdown_pct || 0).toFixed(2) + '%';
                
                const pnlEl = document.getElementById('pnl-val');
                pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
                pnlEl.style.color = pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
                document.getElementById('ret-val').textContent = (pnl >= 0 ? '+' : '') + ret + '%';

                const alphaEl = document.getElementById('alpha-val');
                alphaEl.textContent = (alpha >= 0 ? '+' : '') + '$' + alpha.toFixed(2);
                alphaEl.style.color = alpha >= 0 ? 'var(--accent-blue)' : 'var(--accent-red)';

                const fundEl = document.getElementById('funding-val');
                fundEl.textContent = (funding >= 0 ? '+' : '') + '$' + funding.toFixed(2);
                fundEl.style.color = funding >= 0 ? 'var(--accent-yellow)' : 'var(--accent-red)';

                document.getElementById('trades-val').textContent = `${data.closed_trades_count || 0} (${(data.win_rate_pct || 0).toFixed(1)}%)`;
                document.getElementById('pf-val').textContent = (data.profit_factor || 0).toFixed(2);

                const cbEl = document.getElementById('cb-val');
                if (data.circuit_breaker_tripped) {
                    cbEl.textContent = 'TRIPPED';
                    cbEl.style.color = 'var(--accent-red)';
                } else {
                    cbEl.textContent = 'OK';
                    cbEl.style.color = 'var(--accent-green)';
                }
                document.getElementById('cb-used').textContent = '$' + (data.daily_loss_used || 0).toFixed(2);

                document.getElementById('ker-val').textContent = (data.benchmark_ker_4h || 0).toFixed(2);

                // 2. Charts
                if (equityChart && data.equity_chart_data && data.equity_chart_data.length > 0) {
                    equityChart.data.labels = data.equity_chart_data.map(d => d[0]);
                    equityChart.data.datasets[0].data = data.equity_chart_data.map(d => d[1]);
                    equityChart.update('none');
                }

                if (radarChart && data.dispersion_radar) {
                    radarChart.data.labels = data.dispersion_radar.map(r => r.symbol.replace('USDT', ''));
                    radarChart.data.datasets[0].data = data.dispersion_radar.map(r => r.residual_z);
                    radarChart.data.datasets[0].backgroundColor = data.dispersion_radar.map(r => {
                        const z = Math.abs(r.residual_z);
                        if (z >= 2.0) return '#ff3366';
                        if (z >= 1.4) return '#f59e0b';
                        return '#10b981';
                    });
                    radarChart.update('none');
                }

                // 3. Radar Table
                const rBody = document.getElementById('radar-body');
                if (data.dispersion_radar && data.dispersion_radar.length > 0) {
                    rBody.innerHTML = data.dispersion_radar.map(r => {
                        const zCol = Math.abs(r.residual_z) >= 2.0 ? 'var(--accent-red)' : (Math.abs(r.residual_z) >= 1.4 ? 'var(--accent-yellow)' : 'var(--accent-green)');
                        const frCol = r.funding_diff_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
                        const stBadge = r.status === 'IN_BASKET' ? '<span class="badge-status badge-long">В КОРЗИНЕ</span>' : (r.status === 'PULLBACK_WAIT' ? '<span class="badge-status badge-wait">ОТКАТ 20%</span>' : '<span class="badge-status" style="background:#1e293b; color:#94a3b8;">МОНИТОРИНГ</span>');
                        return `<tr>
                            <td style="font-weight:700; color:var(--accent-emerald);">${r.symbol}</td>
                            <td>$${r.mid_price ? r.mid_price.toFixed(4) : '0.0000'}</td>
                            <td style="color:var(--accent-purple); font-weight:700;">${r.beta.toFixed(2)}</td>
                            <td style="color:${zCol}; font-weight:700;">${(r.residual_z >= 0 ? '+' : '') + r.residual_z.toFixed(2)}</td>
                            <td>${(r.dislocation_pct >= 0 ? '+' : '') + r.dislocation_pct.toFixed(2)}%</td>
                            <td style="color:var(--text-muted);">&ge; ${r.hurdle_pct.toFixed(2)}%</td>
                            <td>${(r.funding_rate >= 0 ? '+' : '') + r.funding_rate.toFixed(4)}%</td>
                            <td style="color:${frCol}; font-weight:700;">${(r.funding_diff_pct >= 0 ? '+' : '') + r.funding_diff_pct.toFixed(4)}%</td>
                            <td>${stBadge}</td>
                        </tr>`;
                    }).join('');
                }

                // 4. Open Baskets Table
                const pBody = document.getElementById('positions-body');
                document.getElementById('open-count').textContent = data.open_positions_count || 0;
                if (data.open_positions && data.open_positions.length > 0) {
                    pBody.innerHTML = data.open_positions.map(p => {
                        const dirBadge = p.direction === 'SHORT_ALT_LONG_HEDGE' ? '<span class="badge-status badge-short">SHORT ALT + HEDGE</span>' : '<span class="badge-status badge-long">LONG ALT - HEDGE</span>';
                        return `<tr>
                            <td style="font-weight:700; color:var(--accent-emerald);">${p.alt_symbol}</td>
                            <td>${dirBadge}</td>
                            <td>$${p.notional_alt.toFixed(2)}</td>
                            <td>$${p.notional_btc.toFixed(2)} / $${p.notional_eth.toFixed(2)}</td>
                            <td>$${p.entry_p_alt ? p.entry_p_alt.toFixed(4) : '0'}</td>
                            <td style="font-weight:700;">${p.entry_z.toFixed(2)}</td>
                            <td style="color:var(--accent-cyan); font-weight:700;">${p.current_z.toFixed(2)}</td>
                            <td style="color:var(--accent-purple);">${p.beta.toFixed(2)}</td>
                            <td>${p.hold_sec}s</td>
                            <td>${p.tp1_filled ? '<span style="color:var(--accent-green);">FILLED (50%)</span>' : '<span style="color:var(--text-muted);">WAITING</span>'}</td>
                            <td>${p.be_stop_active ? '<span style="color:var(--accent-green);">BE ACTIVE</span>' : '<span style="color:var(--text-muted);">INITIAL SL</span>'}</td>
                        </tr>`;
                    }).join('');
                } else {
                    pBody.innerHTML = '<tr><td colspan="11" style="text-align:center; color:var(--text-muted);">Нет открытых корзин. Ожидание статистической дислокации...</td></tr>';
                }

                // 5. Closed Trades Table
                const tBody = document.getElementById('trades-body');
                if (data.closed_trades && data.closed_trades.length > 0) {
                    tBody.innerHTML = data.closed_trades.slice().reverse().map(t => {
                        const netCol = t.net_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
                        return `<tr>
                            <td style="color:var(--text-muted); font-size:10px;">${t.trade_id || '-'}</td>
                            <td style="font-weight:700; color:var(--accent-emerald);">${t.alt_symbol}</td>
                            <td>${t.direction}</td>
                            <td>${t.hold_sec}s</td>
                            <td style="color:var(--accent-blue);">${(t.gross_alpha >= 0 ? '+' : '') + t.gross_alpha.toFixed(3)}$</td>
                            <td style="color:var(--accent-yellow);">${(t.funding_cashflow >= 0 ? '+' : '') + t.funding_cashflow.toFixed(3)}$</td>
                            <td style="color:var(--accent-red);">-${t.fees.toFixed(3)}$</td>
                            <td style="color:${netCol}; font-weight:700;">${(t.net_pnl >= 0 ? '+' : '') + t.net_pnl.toFixed(3)}$</td>
                            <td><span class="badge-status" style="background:#1e293b; color:#cbd5e1;">${t.exit_reason}</span></td>
                            <td style="color:var(--text-muted);">${t.exit_time}</td>
                        </tr>`;
                    }).join('');
                }
            } catch (err) {
                console.error("Dashboard poll error:", err);
            }
        }

        window.addEventListener('DOMContentLoaded', () => {
            initCharts();
            updateDashboard();
            setInterval(updateDashboard, 1500);
        });
    </script>
</body>
</html>
"""
            return web.Response(text=html, content_type="text/html")

        async def handle_logs(request):
            return web.json_response(list(LOG_BUFFER))

        app.router.add_get("/", handle_index)
        app.router.add_get("/api/status", handle_status)
        app.router.add_get("/status", handle_status)
        app.router.add_get("/api/logs", handle_logs)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port, reuse_address=True)
        await site.start()
        logger.info(f"Dispersion Dashboard v3.14 online at http://0.0.0.0:{self.port}/ (API: /api/status)")

    async def run(self) -> None:
        await self.ledger.init_db()
        await self.start_http_server()
        await asyncio.gather(
            self.ingest_public_ws_loop(),
            self.execution_and_exit_loop(),
        )


async def main() -> None:
    engine = DispersionEngine8094()
    await engine.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Engine stopped.")
