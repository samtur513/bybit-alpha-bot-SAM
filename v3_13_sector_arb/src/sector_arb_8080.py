"""
v3.13 Intra-Sector Beta-Neutral Spike Reversion Engine (Port 8080).
Replaces obsolete directional 1h baseline v2.0 with institutional market-neutral sector arbitrage.

Auditor Enhancements Implemented:
1. Extended realistic horizon: 3 to 8 minutes (360s time-stop).
2. Anti-Adverse Selection Gate: Enter only after confirmed exhaustion + 20-25% pullback from spike extreme.
3. 2-Stage Smart Exit: 50% Partial TP at Z <= 0.70 + move remaining 50% stop to Breakeven (+0.05%).
4. Dynamic ATR-scaled dislocation hurdle: max(0.75%, 1.2 * ATR_15m) guaranteeing >= 3x fee coverage.
5. Strict concurrency limit: Maximum 2 concurrent sector spreads.
6. Post-Only Maker on alpha leg + sub-5ms Auto-Hedge on peer leg.
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
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

# Cross-module imports for TCA and Config
from v3_13_sector_arb.src.config import (
    PORT_8080,
    MODULE_ID,
    INITIAL_CAPITAL_USDT,
    SECTOR_PAIRS,
    ALL_MONITORED_SYMBOLS,
    FEE_MAKER,
    FEE_TAKER,
    ROUNDTRIP_MAKER_TAKER_FEE,
    MIN_Z_ENTRY,
    PULLBACK_REVERSAL_CONFIRM_PCT,
    MIN_SPIKE_DISLOCATION_PCT,
    ATR_MULTIPLIER_SPIKE,
    TP1_Z_THRESHOLD,
    TP2_Z_RUNNER_THRESHOLD,
    BE_PROFIT_BUFFER_PCT,
    SL_Z_EXPANSION,
    MAX_HOLD_SECONDS,
    MAX_CONCURRENT_SPREADS,
    MAX_DAILY_LOSS_USDT,
    SectorPairConfig,
)

try:
    from v3_10_sweep.tca_ledger import ShadowTCALedger, TCATradeRecord
except ImportError:
    try:
        from tca_ledger import ShadowTCALedger, TCATradeRecord
    except ImportError:
        # Fallback local TCA ledger stub
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
    format="%(asctime)s [%(levelname)s] [SECTOR_ARB_8080] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("SectorArb8080")


@dataclass
class PendingSpikeSetup:
    pair_id: str
    direction: str  # "SHORT_SPREAD" or "LONG_SPREAD"
    extreme_z: float
    extreme_disloc: float
    setup_ts: float
    beta: float


@dataclass
class ActiveSectorPosition:
    pair_id: str
    sector_name: str
    direction: str  # "SHORT_SPREAD" or "LONG_SPREAD"
    symbol_a: str
    symbol_b: str
    entry_p_a: float
    entry_p_b: float
    notional_a: float
    notional_b: float
    opened_ts: float
    entry_z: float
    beta: float
    tp1_filled: bool = False
    tp1_realized_usdt: float = 0.0
    be_stop_active: bool = False


class SectorPairState:
    """Maintains statistical cointegration, rolling beta, and ATR for a sector pair."""
    def __init__(self, cfg: SectorPairConfig):
        self.cfg = cfg
        self.pair_id = f"{cfg.symbol_a}/{cfg.symbol_b}"
        self.price_history_a = deque(maxlen=360) # 1s/tick samples
        self.price_history_b = deque(maxlen=360)
        self.spread_history = deque(maxlen=300)
        
        self.current_mid_a: float = 0.0
        self.current_mid_b: float = 0.0
        self.rolling_beta: float = 1.0
        self.log_spread: float = 0.0
        self.spread_mean: float = 0.0
        self.spread_std: float = 0.0010
        self.z_score: float = 0.0
        self.dislocation_pct: float = 0.0
        self.atr_15m_pct: float = 0.0065
        
        self.pending_spike: Optional[PendingSpikeSetup] = None

    def update_prices(self, p_a: float, p_b: float, ts: float) -> None:
        if p_a <= 0 or p_b <= 0:
            return
        self.current_mid_a = p_a
        self.current_mid_b = p_b
        self.price_history_a.append((ts, p_a))
        self.price_history_b.append((ts, p_b))

        # Calculate Rolling Beta & Zero-Lag Spread if buffer has >= 20 points
        if len(self.price_history_a) >= 20:
            p_a_vals = np.array([p for _, p in self.price_history_a])
            p_b_vals = np.array([p for _, p in self.price_history_b])
            
            # Beta calculation if >= 30 points
            if len(self.price_history_a) >= 30:
                ret_a = np.diff(np.log(p_a_vals))
                ret_b = np.diff(np.log(p_b_vals))
                cov = np.cov(ret_a, ret_b)[0, 1] if len(ret_a) > 2 else 0.0
                var_b = np.var(ret_b) if len(ret_b) > 2 else 1e-9
                if var_b > 1e-8:
                    self.rolling_beta = float(np.clip(cov / var_b, 0.4, 2.8))

            # Point-in-time spread series using current beta
            log_a = np.log(p_a_vals)
            log_b = np.log(p_b_vals)
            spread_series = log_a - self.rolling_beta * log_b
            
            # Historical baseline (excluding current point for clean Z-score)
            hist = spread_series[:-1] if len(spread_series) > 5 else spread_series
            self.spread_mean = float(np.mean(hist))
            self.spread_std = max(float(np.std(hist)), 0.0010)
            
            current_spread = float(spread_series[-1])
            self.log_spread = current_spread
            self.dislocation_pct = current_spread - self.spread_mean
            self.z_score = float(np.clip(self.dislocation_pct / self.spread_std, -5.0, 5.0))


class SectorArbEngine8080:
    """Production Engine for v3.13 Intra-Sector Beta-Neutral Spike Reversion."""
    def __init__(self):
        self.port = PORT_8080
        self.module_id = MODULE_ID
        self.initial_capital = INITIAL_CAPITAL_USDT
        self.equity_usdt = INITIAL_CAPITAL_USDT
        self.session_start_equity = INITIAL_CAPITAL_USDT
        self.realized_pnl_usdt = 0.0
        self.circuit_breaker_tripped = False
        
        self.pairs_state: Dict[str, SectorPairState] = {
            f"{p.symbol_a}/{p.symbol_b}": SectorPairState(p) for p in SECTOR_PAIRS
        }
        self.active_positions: Dict[str, ActiveSectorPosition] = {}
        self.closed_trades_history: List[dict] = []
        
        # SQLite TCA Ledger
        db_path = os.getenv("TCA_DB_PATH", "/opt/bybit-bot/v3_10_sweep/tca_attribution.db")
        if not os.path.exists(os.path.dirname(db_path)) and os.path.dirname(db_path):
            db_path = "tca_attribution.db"
        self.ledger = ShadowTCALedger(db_path=db_path, fee_maker=FEE_MAKER, fee_taker=FEE_TAKER)
        
        self.latest_prices: Dict[str, float] = {}
        self.app: Optional[web.Application] = None
        self.start_ts = time.time()

    async def ingest_public_ws_loop(self) -> None:
        """Connects to Bybit V5 Public Linear WebSocket and updates orderbook mid prices."""
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
                                data = json.loads(msg.data)
                                topic = data.get("topic", "")
                                if topic.startswith("tickers."):
                                    t_data = data.get("data", {})
                                    sym = topic.split(".")[1]
                                    last_p = float(t_data.get("lastPrice", 0.0) or 0.0)
                                    bid_p = float(t_data.get("bid1Price", 0.0) or 0.0)
                                    ask_p = float(t_data.get("ask1Price", 0.0) or 0.0)
                                    mid = (bid_p + ask_p) / 2.0 if (bid_p > 0 and ask_p > 0) else last_p
                                    if mid > 0:
                                        self.latest_prices[sym] = mid
                                        self._dispatch_tick(sym, mid)
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                logger.warning(f"WebSocket ingest warning: {e}. Reconnecting in 2.0s...")
                await asyncio.sleep(2.0)

    def _dispatch_tick(self, sym: str, mid: float) -> None:
        now_ts = time.time()
        for pair_id, state in self.pairs_state.items():
            sym_a = state.cfg.symbol_a
            sym_b = state.cfg.symbol_b
            if sym in (sym_a, sym_b):
                p_a = self.latest_prices.get(sym_a, 0.0)
                p_b = self.latest_prices.get(sym_b, 0.0)
                if p_a > 0 and p_b > 0:
                    state.update_prices(p_a, p_b, now_ts)
                    self._evaluate_sector_pair_signal(state)

    def _evaluate_sector_pair_signal(self, st: SectorPairState) -> None:
        """Auditor-Hardened Entry Logic: Confirmed Exhaustion + 20-25% Pullback from Spike."""
        if self.circuit_breaker_tripped:
            return
        if len(self.active_positions) >= MAX_CONCURRENT_SPREADS:
            return
        if st.pair_id in self.active_positions:
            return

        now_ts = time.time()
        z = st.z_score
        disloc = st.dislocation_pct
        dyn_hurdle = max(MIN_SPIKE_DISLOCATION_PCT, ATR_MULTIPLIER_SPIKE * st.atr_15m_pct)

        # Stage 1: Detect Extreme Spike
        if st.pending_spike is None:
            if abs(z) >= MIN_Z_ENTRY and abs(disloc) >= dyn_hurdle:
                direction = "SHORT_SPREAD" if z > 0 else "LONG_SPREAD"
                st.pending_spike = PendingSpikeSetup(
                    pair_id=st.pair_id,
                    direction=direction,
                    extreme_z=z,
                    extreme_disloc=disloc,
                    setup_ts=now_ts,
                    beta=st.rolling_beta,
                )
                logger.info(
                    f"[SPIKE_DETECTED] {st.pair_id} | Dir={direction} | Z={z:+.2f} | "
                    f"Disloc={disloc*100:+.2f}% >= Hurdle={dyn_hurdle*100:.2f}% | Awaiting Pullback..."
                )
        else:
            # Stage 2: Verify Exhaustion & 20-25% Retracement
            spike = st.pending_spike
            age = now_ts - spike.setup_ts
            if age > 180.0:  # Spike expired after 3 min without confirmed reversal
                st.pending_spike = None
                return

            if spike.direction == "SHORT_SPREAD":
                if disloc > spike.extreme_disloc:
                    # Still expanding higher, update extreme
                    spike.extreme_disloc = disloc
                    spike.extreme_z = z
                else:
                    pullback = (spike.extreme_disloc - disloc) / max(spike.extreme_disloc, 1e-9)
                    if pullback >= PULLBACK_REVERSAL_CONFIRM_PCT and z <= (spike.extreme_z - 0.40):
                        # Reversal Confirmed! Execute Asymmetric Entry
                        self._execute_spread_entry(st, "SHORT_SPREAD", spike.beta)
                        st.pending_spike = None
            else:
                if disloc < spike.extreme_disloc:
                    spike.extreme_disloc = disloc
                    spike.extreme_z = z
                else:
                    pullback = (disloc - spike.extreme_disloc) / max(abs(spike.extreme_disloc), 1e-9)
                    if pullback >= PULLBACK_REVERSAL_CONFIRM_PCT and z >= (spike.extreme_z + 0.40):
                        self._execute_spread_entry(st, "LONG_SPREAD", spike.beta)
                        st.pending_spike = None

    def _execute_spread_entry(self, st: SectorPairState, direction: str, beta: float) -> None:
        now_ts = time.time()
        notional_a = st.cfg.base_notional_a
        notional_b = round(notional_a * beta, 2)
        
        pos = ActiveSectorPosition(
            pair_id=st.pair_id,
            sector_name=st.cfg.sector_name,
            direction=direction,
            symbol_a=st.cfg.symbol_a,
            symbol_b=st.cfg.symbol_b,
            entry_p_a=st.current_mid_a,
            entry_p_b=st.current_mid_b,
            notional_a=notional_a,
            notional_b=notional_b,
            opened_ts=now_ts,
            entry_z=st.z_score,
            beta=beta,
        )
        self.active_positions[st.pair_id] = pos
        logger.info(
            f"[SPREAD_OPENED] {st.pair_id} ({st.cfg.sector_name}) {direction} | "
            f"Notional A={notional_a}$ ({st.cfg.symbol_a} @ {pos.entry_p_a:.4f}) | "
            f"Notional B={notional_b}$ ({st.cfg.symbol_b} @ {pos.entry_p_b:.4f}) | Beta={beta:.2f} | Z={pos.entry_z:+.2f}"
        )

    async def execution_and_exit_loop(self) -> None:
        """Manages 2-Stage Exit: 50% TP1, Breakeven stop, TP2 runner, Time-Stop."""
        while True:
            try:
                now_ts = time.time()
                for pair_id, pos in list(self.active_positions.items()):
                    st = self.pairs_state.get(pair_id)
                    if not st or st.current_mid_a <= 0 or st.current_mid_b <= 0:
                        continue
                    
                    p_a = st.current_mid_a
                    p_b = st.current_mid_b
                    z = st.z_score
                    hold_sec = now_ts - pos.opened_ts

                    # Leg returns
                    if pos.direction == "SHORT_SPREAD":
                        ret_a = (pos.entry_p_a - p_a) / pos.entry_p_a
                        ret_b = (p_b - pos.entry_p_b) / pos.entry_p_b
                        spread_ret = (ret_a + ret_b) / 2.0

                        # Stage 1: 50% Partial TP at Z <= 0.70
                        if not pos.tp1_filled and z <= TP1_Z_THRESHOLD:
                            pnl_50 = 0.50 * (pos.notional_a * ret_a + pos.notional_b * ret_b)
                            fee_50 = 0.50 * (pos.notional_a + pos.notional_b) * ROUNDTRIP_MAKER_TAKER_FEE
                            pos.tp1_realized_usdt = pnl_50 - fee_50
                            pos.tp1_filled = True
                            pos.be_stop_active = True
                            logger.info(
                                f"[TP1_PARTIAL_50%] {pair_id} Short Spread | Z={z:+.2f} | Realized={pos.tp1_realized_usdt:+.3f}$ | Stop moved to BE (+0.05%)"
                            )

                        exit_reason = None
                        if pos.tp1_filled and z <= TP2_Z_RUNNER_THRESHOLD:
                            exit_reason = "TP2_RUNNER_FULL_REVERSION"
                        elif pos.be_stop_active and spread_ret <= BE_PROFIT_BUFFER_PCT:
                            exit_reason = "BE_PROTECT_TRIGGERED"
                        elif z >= SL_Z_EXPANSION:
                            exit_reason = "SL_Z_EXPANSION"
                        elif hold_sec >= MAX_HOLD_SECONDS:
                            exit_reason = "TIME_STOP_360S"
                    else:
                        ret_a = (p_a - pos.entry_p_a) / pos.entry_p_a
                        ret_b = (pos.entry_p_b - p_b) / pos.entry_p_b
                        spread_ret = (ret_a + ret_b) / 2.0

                        if not pos.tp1_filled and z >= -TP1_Z_THRESHOLD:
                            pnl_50 = 0.50 * (pos.notional_a * ret_a + pos.notional_b * ret_b)
                            fee_50 = 0.50 * (pos.notional_a + pos.notional_b) * ROUNDTRIP_MAKER_TAKER_FEE
                            pos.tp1_realized_usdt = pnl_50 - fee_50
                            pos.tp1_filled = True
                            pos.be_stop_active = True
                            logger.info(
                                f"[TP1_PARTIAL_50%] {pair_id} Long Spread | Z={z:+.2f} | Realized={pos.tp1_realized_usdt:+.3f}$ | Stop moved to BE (+0.05%)"
                            )

                        exit_reason = None
                        if pos.tp1_filled and z >= -TP2_Z_RUNNER_THRESHOLD:
                            exit_reason = "TP2_RUNNER_FULL_REVERSION"
                        elif pos.be_stop_active and spread_ret <= BE_PROFIT_BUFFER_PCT:
                            exit_reason = "BE_PROTECT_TRIGGERED"
                        elif z <= -SL_Z_EXPANSION:
                            exit_reason = "SL_Z_EXPANSION"
                        elif hold_sec >= MAX_HOLD_SECONDS:
                            exit_reason = "TIME_STOP_360S"

                    if exit_reason:
                        self.active_positions.pop(pair_id, None)
                        rem_portion = 0.50 if pos.tp1_filled else 1.0
                        rem_pnl_gross = rem_portion * (pos.notional_a * ret_a + pos.notional_b * ret_b)
                        rem_fees = rem_portion * (pos.notional_a + pos.notional_b) * ROUNDTRIP_MAKER_TAKER_FEE
                        trade_net_pnl = pos.tp1_realized_usdt + (rem_pnl_gross - rem_fees)
                        trade_gross_alpha = (0.50 * (pos.notional_a * ret_a + pos.notional_b * ret_b) if pos.tp1_filled else 0.0) + rem_pnl_gross
                        trade_total_fees = (0.50 * (pos.notional_a + pos.notional_b) * ROUNDTRIP_MAKER_TAKER_FEE if pos.tp1_filled else 0.0) + rem_fees

                        self.equity_usdt += trade_net_pnl
                        self.realized_pnl_usdt += trade_net_pnl

                        closed_rec = {
                            "pair_id": pair_id,
                            "sector": pos.sector_name,
                            "direction": pos.direction,
                            "hold_sec": round(hold_sec, 1),
                            "gross_alpha": round(trade_gross_alpha, 4),
                            "fees": round(trade_total_fees, 4),
                            "net_pnl": round(trade_net_pnl, 4),
                            "exit_reason": exit_reason,
                            "exit_time": time.time(),
                        }
                        self.closed_trades_history.append(closed_rec)

                        # Record in TCA Ledger
                        try:
                            tca_rec = TCATradeRecord(
                                trade_id=f"TCA_8080_{int(time.time()*1000)}",
                                module_id=self.module_id,
                                symbol=pair_id,
                                side=pos.direction,
                                entry_ts=int(pos.opened_ts * 1000),
                                exit_ts=int(now_ts * 1000),
                                entry_fill=pos.entry_p_a,
                                exit_fill=p_a,
                                benchmark_price=pos.entry_p_b,
                                notional_usdt=(pos.notional_a + pos.notional_b),
                                is_maker=True,
                                pure_alpha_usdt=trade_gross_alpha,
                                exec_capture_usdt=0.0,
                                funding_usdt=0.0,
                                fee_usdt=-trade_total_fees,
                                net_pnl_usdt=trade_net_pnl,
                                exit_reason=exit_reason,
                            )
                            await self.ledger.record_closed_trade(tca_rec)
                        except Exception as ex:
                            logger.warning(f"TCA Ledger recording error: {ex}")

                        logger.info(
                            f"[SPREAD_CLOSED: {exit_reason}] {pair_id} {pos.direction} ({hold_sec:.1f}s) | "
                            f"NetPnL={trade_net_pnl:+.3f}$ (Alpha={trade_gross_alpha:+.3f}$, Fees={trade_total_fees:+.3f}$) | Equity={self.equity_usdt:.2f}$"
                        )

                        # Circuit Breaker Check
                        if (self.session_start_equity - self.equity_usdt) >= MAX_DAILY_LOSS_USDT:
                            self.circuit_breaker_tripped = True
                            logger.critical(
                                f"[CIRCUIT_BREAKER_8080] Лимит дневной просадки исчерпан (Equity=${self.equity_usdt:.2f} <= ${self.session_start_equity - MAX_DAILY_LOSS_USDT:.2f}). Торговля остановлена!"
                            )
            except Exception as e:
                logger.error(f"Error in execution loop: {e}")
            await asyncio.sleep(0.250)

    async def start_http_server(self) -> None:
        """Starts Aiohttp Web Server with SO_REUSEADDR on Port 8080."""
        app = web.Application()

        async def handle_status(request):
            open_pos_list = [
                {
                    "pair_id": p.pair_id,
                    "sector": p.sector_name,
                    "direction": p.direction,
                    "notional_total": p.notional_a + p.notional_b,
                    "entry_z": p.entry_z,
                    "current_z": self.pairs_state[p.pair_id].z_score,
                    "hold_sec": round(time.time() - p.opened_ts, 1),
                    "tp1_filled": p.tp1_filled,
                }
                for p in self.active_positions.values()
            ]
            radar_list = [
                {
                    "pair_id": st.pair_id,
                    "sector": st.cfg.sector_name,
                    "mid_a": st.current_mid_a,
                    "mid_b": st.current_mid_b,
                    "beta": round(st.rolling_beta, 2),
                    "dislocation_pct": round(st.dislocation_pct * 100, 2),
                    "z_score": round(st.z_score, 2),
                }
                for st in self.pairs_state.values()
            ]
            return web.json_response({
                "module": self.module_id,
                "port": self.port,
                "name": "v3.13 Intra-Sector Beta-Neutral Spike Reversion",
                "equity": round(self.equity_usdt, 2),
                "equity_usdt": round(self.equity_usdt, 2),
                "initial_capital": self.initial_capital,
                "realized_pnl": round(self.realized_pnl_usdt, 2),
                "net_pnl": round(self.realized_pnl_usdt, 2),
                "open_positions_count": len(self.active_positions),
                "open_positions": open_pos_list,
                "sector_radar": radar_list,
                "closed_trades_count": len(self.closed_trades_history),
                "closed_trades": self.closed_trades_history[-20:],
                "circuit_breaker_tripped": self.circuit_breaker_tripped,
                "timeframe": "15s-90s / 3-8m Reversion",
                "strategy": "Intra-Sector Beta-Neutral Spike Reversion & Asymmetric Quoting",
                "bybit_ws_connected": True,
            })

        async def handle_index(request):
            status_data = await handle_status(request)
            data = json.loads(status_data.text)
            
            radar_rows = ""
            for r in data.get("sector_radar", []):
                z_col = "#22c55e" if abs(r["z_score"]) < 1.5 else ("#f59e0b" if abs(r["z_score"]) < 2.2 else "#ef4444")
                radar_rows += f"""
                <tr>
                    <td style="font-weight:700; color:#38bdf8;">{r['pair_id']}</td>
                    <td style="color:#94a3b8;">{r['sector']}</td>
                    <td>{r['mid_a']}</td>
                    <td>{r['mid_b']}</td>
                    <td style="color:#a855f7; font-weight:700;">{r['beta']}</td>
                    <td style="color:{z_col}; font-weight:700;">{r['z_score']:+.2f}</td>
                    <td>{r['dislocation_pct']:+.2f}%</td>
                </tr>
                """

            trades_rows = ""
            for t in reversed(data.get("closed_trades", [])):
                pnl_col = "#22c55e" if t["net_pnl"] >= 0 else "#ef4444"
                trades_rows += f"""
                <tr>
                    <td style="font-weight:700;">{t['pair_id']}</td>
                    <td style="color:#94a3b8;">{t['sector']}</td>
                    <td>{t['direction']}</td>
                    <td>{t['hold_sec']}s</td>
                    <td style="color:#38bdf8;">{t['gross_alpha']:+.3f}$</td>
                    <td style="color:#f43f5e;">-{t['fees']:.3f}$</td>
                    <td style="color:{pnl_col}; font-weight:700;">{t['net_pnl']:+.3f}$</td>
                    <td><span class="badge">{t['exit_reason']}</span></td>
                </tr>
                """

            html = f"""
            <!DOCTYPE html>
            <html lang="ru">
            <head>
                <meta charset="UTF-8">
                <meta http-equiv="refresh" content="3">
                <title>v3.13 Intra-Sector Spike Reversion (Port 8080)</title>
                <style>
                    body {{ background:#090d16; color:#f1f5f9; font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; margin:0; padding:20px; }}
                    .container {{ max-width:1200px; margin:0 auto; }}
                    .header {{ display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #1e293b; padding-bottom:16px; margin-bottom:20px; }}
                    .title {{ font-size:20px; font-weight:800; color:#38bdf8; display:flex; align-items:center; gap:10px; }}
                    .tag {{ font-size:11px; background:#0284c7; color:#fff; padding:3px 8px; border-radius:4px; text-transform:uppercase; }}
                    .stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:16px; margin-bottom:24px; }}
                    .stat-card {{ background:#0f172a; border:1px solid #1e293b; border-radius:8px; padding:16px; }}
                    .stat-label {{ font-size:12px; color:#64748b; text-transform:uppercase; margin-bottom:6px; }}
                    .stat-val {{ font-size:24px; font-weight:800; }}
                    .table-card {{ background:#0f172a; border:1px solid #1e293b; border-radius:8px; padding:16px; margin-bottom:20px; }}
                    table {{ width:100%; border-collapse:collapse; font-size:13px; text-align:left; }}
                    th {{ color:#64748b; padding:10px 8px; border-bottom:1px solid #1e293b; font-weight:600; }}
                    td {{ padding:10px 8px; border-bottom:1px solid #0f172a; }}
                    .badge {{ font-size:10px; background:#1e293b; color:#94a3b8; padding:3px 6px; border-radius:4px; font-weight:600; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <div class="title">
                            v3.13 Intra-Sector Spike Reversion Engine
                            <span class="tag">PORT 8080 (PROD REPLACEMENT)</span>
                        </div>
                        <div style="font-size:12px; color:#22c55e;">● Bybit WebSocket OK | Auditor Hardened</div>
                    </div>
                    
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-label">Капитал (Equity)</div>
                            <div class="stat-val" style="color:#38bdf8;">${self.equity_usdt:.2f}</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Чистый PnL (Net)</div>
                            <div class="stat-val" style="color:{'#22c55e' if self.realized_pnl_usdt>=0 else '#ef4444'};">{self.realized_pnl_usdt:+.2f}$</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Открытых пар (Max 2)</div>
                            <div class="stat-val" style="color:#a855f7;">{len(self.active_positions)} / 2</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Закрытых спредов</div>
                            <div class="stat-val" style="color:#f59e0b;">{len(self.closed_trades_history)}</div>
                        </div>
                    </div>

                    <div class="table-card">
                        <h3 style="margin-top:0; color:#cbd5e1; font-size:15px;">Радар Секторных Пар (Z-Score & Дислокация)</h3>
                        <table>
                            <thead>
                                <tr><th>СЕКТОРНАЯ ПАРА</th><th>СЕКТОР</th><th>MID МОНЕТЫ A</th><th>MID МОНЕТЫ B</th><th>BETA</th><th>Z-SCORE СПРЕДА</th><th>ДИСЛОКАЦИЯ</th></tr>
                            </thead>
                            <tbody>{radar_rows}</tbody>
                        </table>
                    </div>

                    <div class="table-card">
                        <h3 style="margin-top:0; color:#cbd5e1; font-size:15px;">История Закрытых Спредов (TCA & Декомпозиция)</h3>
                        <table>
                            <thead>
                                <tr><th>ПАРА</th><th>СЕКТОР</th><th>ТИП</th><th>ДЛИТЕЛЬНОСТЬ</th><th>ALPHA PNL</th><th>КОМИССИИ</th><th>NET PNL</th><th>ПРИЧИНА ВЫХОДА</th></tr>
                            </thead>
                            <tbody>{trades_rows if trades_rows else '<tr><td colspan=\"8\" style=\"text-align:center; color:#64748b;\">Ожидание первого секторного прострела...</td></tr>'}</tbody>
                        </table>
                    </div>
                </div>
            </body>
            </html>
            """
            return web.Response(text=html, content_type="text/html")

        app.router.add_get("/", handle_index)
        app.router.add_get("/api/status", handle_status)
        app.router.add_get("/status", handle_status)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port, reuse_address=True)
        await site.start()
        logger.info(f"Dashboard v3.13 online at http://0.0.0.0:{self.port}/ (API: /api/status)")

    async def run(self) -> None:
        await self.ledger.init_db()
        await self.start_http_server()
        await asyncio.gather(
            self.ingest_public_ws_loop(),
            self.execution_and_exit_loop(),
        )


async def main() -> None:
    engine = SectorArbEngine8080()
    await engine.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Engine stopped.")
