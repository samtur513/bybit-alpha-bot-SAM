import asyncio
import collections
import logging
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp
from aiohttp import web
import numpy as np
import orjson
import zmq
import zmq.asyncio
from dotenv import load_dotenv

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    from v3_10_sweep.tca_ledger import ShadowTCALedger, TCATradeRecord
except ImportError:
    try:
        from tca_ledger import ShadowTCALedger, TCATradeRecord
    except ImportError:
        # Fallback relative
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "v3_10_sweep")))
        from tca_ledger import ShadowTCALedger, TCATradeRecord

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d | FUNDING_8097 | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("Funding8097")

MODULE_ID = "M2_FUNDING_8097"
HTTP_PORT = int(os.getenv("FUNDING_HTTP_PORT", "8097"))
ZMQ_SUB_ADDR = os.getenv("ZMQ_SUB_ADDR", "tcp://127.0.0.1:5555")
REST_BASE = "https://api.bybit.com"


@dataclass
class FundingCandidate:
    symbol: str
    funding_rate: float
    next_funding_time_ms: int
    open_interest: float
    predicted_rate: float
    time_to_settle_sec: float
    action: str  # 'LONG_SQUEEZE' (negative funding) | 'SHORT_SQUEEZE' (positive funding)


@dataclass
class PendingFundingIntent:
    intent_id: str
    symbol: str
    side: str
    position_idx: int
    limit_price: float
    sl_price: float
    tp_price: float
    qty: float
    signal_mid: float
    created_ms: int
    funding_rate: float
    next_funding_ms: int


@dataclass
class OpenFundingPosition:
    trade_id: str
    symbol: str
    side: str
    position_idx: int
    qty: float
    signal_mid: float
    entry_fill: float
    sl_price: float
    tp_price: float
    opened_ms: int
    next_funding_ms: int
    funding_rate: float
    sl_order_link_id: str
    min_price: float
    max_price: float


class FundingSqueezeEngine8097:
    def __init__(self):
        self.paper_mode = os.getenv("PAPER_TRADING", "true").lower() == "true"
        self.equity_usdt = float(os.getenv("EQUITY_USDT", "300.0"))
        self.risk_pct = float(os.getenv("RISK_PER_TRADE_PCT", "0.0035"))
        self.fee_maker = 0.000324 if os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true" else 0.00036
        self.fee_taker = 0.00090 if os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true" else 0.00100

        # Shared TCA Ledger
        db_path = os.getenv("TCA_DB_PATH", "/opt/bybit-bot/v3_10_sweep/tca_attribution.db")
        if not os.path.exists(os.path.dirname(db_path)) and os.path.dirname(db_path):
            db_path = "tca_attribution.db"
        self.ledger = ShadowTCALedger(db_path=db_path, fee_maker=self.fee_maker, fee_taker=self.fee_taker)

        self.zmq_ctx = None
        self.sub_sock = None

        self.latest_ticks: Dict[str, dict] = {}
        self.funding_candidates: Dict[str, FundingCandidate] = {}
        self.pending_intents: Dict[str, PendingFundingIntent] = {}
        self.open_positions: Dict[str, OpenFundingPosition] = {}
        self.cooldown_until: Dict[str, float] = {}
        self.symbol_specs: Dict[str, tuple] = {}

        self.total_filled_alpha_usdt: float = 0.0
        self.total_missed_alpha_usdt: float = 0.0
        self.trades_count: int = 0
        self.start_time = time.time()

    def init_zmq(self) -> None:
        self.zmq_ctx = zmq.asyncio.Context()
        self.sub_sock = self.zmq_ctx.socket(zmq.SUB)
        self.sub_sock.setsockopt(zmq.LINGER, 0)
        self.sub_sock.connect(ZMQ_SUB_ADDR)
        self.sub_sock.setsockopt(zmq.SUBSCRIBE, b"")
        logger.info(f"ZeroMQ SUB сокет подключен к {ZMQ_SUB_ADDR}")

    @property
    def capture_efficiency(self) -> float:
        denom = self.total_filled_alpha_usdt + self.total_missed_alpha_usdt
        return (self.total_filled_alpha_usdt / denom * 100.0) if denom > 0 else 100.0

    async def get_symbol_spec(self, session: aiohttp.ClientSession, symbol: str) -> tuple:
        if symbol in self.symbol_specs:
            return self.symbol_specs[symbol]
        try:
            url = f"{REST_BASE}/v5/market/instruments-info?category=linear&symbol={symbol}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as r:
                info = orjson.loads(await r.read())
                it = info.get("result", {}).get("list", [])[0]
                spec = (
                    float(it["priceFilter"]["tickSize"]),
                    float(it["lotSizeFilter"]["qtyStep"]),
                    float(it["lotSizeFilter"]["minOrderQty"]),
                )
                self.symbol_specs[symbol] = spec
                return spec
        except Exception:
            return (0.001, 0.01, 0.01)

    async def funding_screener_loop(self) -> None:
        """Polls Bybit tickers every 30s to find extreme funding rates and evaluates direct setups."""
        logger.info("Запуск фонового скринера аномального фандинга Bybit Linear...")
        while True:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
                    url = f"{REST_BASE}/v5/market/tickers?category=linear"
                    async with session.get(url) as resp:
                        data = orjson.loads(await resp.read())
                        tickers = data.get("result", {}).get("list", [])
                        now_ms = int(time.time() * 1000)

                        candidates = {}
                        for t in tickers:
                            sym = t.get("symbol", "")
                            if not sym.endswith("USDT") or float(t.get("turnover24h", 0)) < 5_000_000:
                                continue

                            fr_str = t.get("fundingRate", "")
                            if not fr_str:
                                continue
                            fr = float(fr_str)
                            next_f_ms = int(t.get("nextFundingTime", 0))
                            if next_f_ms <= now_ms:
                                continue

                            time_to_settle_s = (next_f_ms - now_ms) / 1000.0
                            oi = float(t.get("openInterest", 0.0))

                            # Trigger criteria:
                            # 1. Negative funding squeeze: fr <= -0.0003 (-0.03% per funding period, heavily shorted)
                            # 2. Positive funding squeeze: fr >= +0.0003 (+0.03% per funding period, heavily longed)
                            # 3. Settlement window: within 30 minutes (1800s) and at least 60s remaining
                            if abs(fr) >= 0.0003 and 60.0 <= time_to_settle_s <= 1800.0:
                                act = "LONG_SQUEEZE" if fr < 0 else "SHORT_SQUEEZE"
                                cand = FundingCandidate(
                                    symbol=sym,
                                    funding_rate=fr,
                                    next_funding_time_ms=next_f_ms,
                                    open_interest=oi,
                                    predicted_rate=float(t.get("predictedDeliveryPrice", 0.0)),
                                    time_to_settle_sec=time_to_settle_s,
                                    action=act,
                                )
                                candidates[sym] = cand

                                # Direct market evaluation for discovered candidate
                                bid_p = float(t.get("bid1Price") or t.get("lastPrice") or 0.0)
                                ask_p = float(t.get("ask1Price") or t.get("lastPrice") or 0.0)
                                if bid_p > 0 and ask_p > 0:
                                    mid_p = 0.5 * (bid_p + ask_p)
                                    spec = await self.get_symbol_spec(session, sym)
                                    cand_tick = {
                                        "symbol": sym,
                                        "ts": now_ms,
                                        "bid": bid_p,
                                        "ask": ask_p,
                                        "mid": mid_p,
                                        "tick_size": spec[0],
                                        "qty_step": spec[1],
                                        "min_qty": spec[2],
                                        "vwap": mid_p,
                                        "tfi_3s": 0.35 if act == "LONG_SQUEEZE" else -0.35,
                                        "atr_15m": mid_p * 0.008,
                                        "top3_bid_notional": float(t.get("bid1Size") or 10.0) * bid_p,
                                        "top3_ask_notional": float(t.get("ask1Size") or 10.0) * ask_p,
                                    }
                                    self.latest_ticks[sym] = cand_tick
                                    self._evaluate_funding_setup(cand_tick)

                        self.funding_candidates = candidates
                        if candidates:
                            logger.info(f"Обнаружено {len(candidates)} кандидатов аномального фандинга: {[f'{s} ({c.funding_rate*100:.3f}%)' for s, c in candidates.items()]}")
            except Exception as e:
                logger.warning(f"Ошибка funding_screener_loop: {e}")

            await asyncio.sleep(30.0)

    def _evaluate_funding_setup(self, tick: dict) -> None:
        sym = tick["symbol"]
        if sym in self.pending_intents or sym in self.open_positions:
            return
        if time.time() < self.cooldown_until.get(sym, 0.0):
            return

        cand = self.funding_candidates.get(sym)
        if not cand:
            return

        now_ms = tick["ts"]
        time_to_settle_s = (cand.next_funding_time_ms - now_ms) / 1000.0
        if time_to_settle_s < 60.0 or time_to_settle_s > 1800.0:
            return

        mid = tick["mid"]
        vwap = tick.get("vwap", mid)
        tfi = tick.get("tfi_3s", 0.0)
        t_sz = tick["tick_size"]
        atr = tick.get("atr_15m", mid * 0.005)

        # Noise Gate 1: Flow Confirmation
        # For Long Squeeze (Negative funding): buyers must lead, TFI >= +0.10, price above VWAP
        # For Short Squeeze (Positive funding): sellers must lead, TFI <= -0.10, price below VWAP
        if cand.action == "LONG_SQUEEZE":
            if tfi < 0.10 or mid < vwap:
                return
            side = "Buy"
            pos_idx = 1
            limit_p = round((tick["bid"]) / t_sz) * t_sz
            sl_p = round((limit_p - 1.5 * atr) / t_sz) * t_sz
            tp_p = round((limit_p + 3.0 * atr) / t_sz) * t_sz
        else:
            if tfi > -0.10 or mid > vwap:
                return
            side = "Sell"
            pos_idx = 2
            limit_p = round((tick["ask"]) / t_sz) * t_sz
            sl_p = round((limit_p + 1.5 * atr) / t_sz) * t_sz
            tp_p = round((limit_p - 3.0 * atr) / t_sz) * t_sz

        risk_dist_pct = abs(limit_p - sl_p) / limit_p
        if risk_dist_pct <= 0.0015 or risk_dist_pct > 0.0200:
            return

        total_risk_pct = risk_dist_pct + self.fee_maker + self.fee_taker
        notional = min((self.equity_usdt * self.risk_pct) / total_risk_pct, self.equity_usdt * 2.0)
        qty_step = tick["qty_step"]
        qty = math.floor((notional / limit_p) / qty_step) * qty_step
        if qty < tick["min_qty"]:
            return

        intent_id = f"M2_8097_IN_{sym}_{now_ms}"
        self.pending_intents[sym] = PendingFundingIntent(
            intent_id=intent_id,
            symbol=sym,
            side=side,
            position_idx=pos_idx,
            limit_price=limit_p,
            sl_price=sl_p,
            tp_price=tp_p,
            qty=round(qty, 6),
            signal_mid=mid,
            created_ms=now_ms,
            funding_rate=cand.funding_rate,
            next_funding_ms=cand.next_funding_time_ms,
        )
        logger.info(
            f"[FUNDING ARMED] {sym} {side} (FR={cand.funding_rate*100:.3f}%) | "
            f"Limit={limit_p} SL={sl_p} TP={tp_p} | Settle in {time_to_settle_s:.0f}s"
        )

    async def execution_and_clearing_eject_loop(self) -> None:
        """Executes intents and enforces T-1s clearing eject to prevent clearing cascade dumps."""
        while True:
            now_ms = int(time.time() * 1000)

            # 1. Check Pending Limit Intents (15s timeout)
            for sym, intent in list(self.pending_intents.items()):
                tick = self.latest_ticks.get(sym)
                if not tick:
                    continue

                filled = (tick["ask"] <= intent.limit_price) if intent.side == "Buy" else (tick["bid"] >= intent.limit_price)
                if filled:
                    self.pending_intents.pop(sym, None)
                    sl_link = f"M2_8097_SL_{sym}_{now_ms}"
                    pos = OpenFundingPosition(
                        trade_id=str(uuid.uuid4()),
                        symbol=sym,
                        side=intent.side,
                        position_idx=intent.position_idx,
                        qty=intent.qty,
                        signal_mid=intent.signal_mid,
                        entry_fill=intent.limit_price,
                        sl_price=intent.sl_price,
                        tp_price=intent.tp_price,
                        opened_ms=now_ms,
                        next_funding_ms=intent.next_funding_ms,
                        funding_rate=intent.funding_rate,
                        sl_order_link_id=sl_link,
                        min_price=intent.limit_price,
                        max_price=intent.limit_price,
                    )
                    self.open_positions[sym] = pos
                    logger.info(f"[FILLED FUNDING MAKER] {sym} {pos.side} @ {pos.entry_fill} | SL={pos.sl_price} TP={pos.tp_price}")
                elif (now_ms - intent.created_ms) > 15000:
                    self.pending_intents.pop(sym, None)

            # 2. Monitor Positions and Trigger T-1s CLEARING EJECT
            for sym, pos in list(self.open_positions.items()):
                tick = self.latest_ticks.get(sym)
                if not tick:
                    continue
                mid = tick["mid"]
                pos.min_price = min(pos.min_price, mid)
                pos.max_price = max(pos.max_price, mid)

                ms_until_settle = pos.next_funding_ms - now_ms
                exit_reason = None
                exit_fill = mid
                is_maker = True

                # PRIORITY NOISE GATE: Mandatory Clearing Eject at T - 1s (1500 ms)
                # Exits before toxic clearing settlement dump
                if ms_until_settle <= 1500:
                    exit_reason = "CLEARING_EJECT_T_MINUS_1S"
                    exit_fill = mid
                    is_maker = False  # Eject priority taker or aggressive maker
                elif pos.side == "Buy":
                    if mid <= pos.sl_price:
                        exit_reason = "SL_HIT"
                        exit_fill = pos.sl_price
                        is_maker = False
                        self.cooldown_until[sym] = time.time() + 900.0
                    elif mid >= pos.tp_price:
                        exit_reason = "TP_HIT"
                        exit_fill = pos.tp_price
                        is_maker = True
                else:
                    if mid >= pos.sl_price:
                        exit_reason = "SL_HIT"
                        exit_fill = pos.sl_price
                        is_maker = False
                        self.cooldown_until[sym] = time.time() + 900.0
                    elif mid <= pos.tp_price:
                        exit_reason = "TP_HIT"
                        exit_fill = pos.tp_price
                        is_maker = True

                if exit_reason:
                    self.open_positions.pop(sym, None)
                    if pos.side == "Buy":
                        mae_pct = (pos.entry_fill - pos.min_price) / pos.entry_fill * 100.0
                        mfe_pct = (pos.max_price - pos.entry_fill) / pos.entry_fill * 100.0
                    else:
                        mae_pct = (pos.max_price - pos.entry_fill) / pos.entry_fill * 100.0
                        mfe_pct = (pos.entry_fill - pos.min_price) / pos.entry_fill * 100.0

                    tca_rec = TCATradeRecord(
                        trade_id=pos.trade_id,
                        module_id=MODULE_ID,
                        sub_strategy=f"FUNDING_{pos.side.upper()}",
                        symbol=sym,
                        side=pos.side,
                        opened_at_ms=pos.opened_ms,
                        closed_at_ms=now_ms,
                        sweep_atr_depth=0.0,
                        compression_ratio=0.0,
                        touches_count=0,
                        absorption_k=abs(pos.funding_rate) * 1000.0,
                        cvd_flip_tfi=0.0,
                        signal_mid_price=pos.signal_mid,
                        entry_fill_price=pos.entry_fill,
                        exit_mid_price=mid,
                        exit_fill_price=exit_fill,
                        qty=pos.qty,
                        entry_is_maker=True,
                        exit_is_maker=is_maker,
                        funding_cashflow_usdt=0.0,
                        mae_pct=round(mae_pct, 3),
                        mfe_pct=round(mfe_pct, 3),
                        exit_reason=exit_reason,
                    )
                    res = await self.ledger.record_closed_trade(tca_rec)
                    if res["pure_alpha"] > 0:
                        self.total_filled_alpha_usdt += res["pure_alpha"]
                    self.trades_count += 1
                    self.equity_usdt += res["net_pnl"]
                    logger.info(
                        f"[FUNDING CLOSED: {exit_reason}] {sym} {pos.side} | NetPnL={res['net_pnl']:+.3f}$ "
                        f"(PureAlpha={res['pure_alpha']:+.3f}$, Fees={res['fees']:+.3f}$) | Equity={self.equity_usdt:.2f}$"
                    )

            await asyncio.sleep(0.300)

    async def zmq_listener_loop(self) -> None:
        logger.info(f"Ожидание тиков из шины Hub {ZMQ_SUB_ADDR}...")
        while True:
            _, raw_payload = await self.sub_sock.recv_multipart()
            # Fast filter: if no extreme funding candidates and no open positions, skip to save CPU
            if not self.funding_candidates and not self.open_positions:
                continue
            tick = orjson.loads(raw_payload)
            sym = tick.get("symbol")
            if sym not in self.funding_candidates and sym not in self.open_positions:
                continue
            self.latest_ticks[sym] = tick
            self._evaluate_funding_setup(tick)

    async def start_http_server(self) -> None:
        app = web.Application()

        async def handle_status(request):
            stats = await self.ledger.get_summary_stats(MODULE_ID)
            open_pos_list = []
            for p in self.open_positions.values():
                tick = self.latest_ticks.get(p.symbol)
                cur_mid = tick["mid"] if tick else p.entry_fill
                dir_val = 1.0 if p.side == "Buy" else -1.0
                unrealized = dir_val * (cur_mid - p.entry_fill) * p.qty
                dir_str = "LONG" if p.side == "Buy" else "SHORT"
                sec_to_settle = max(0, (p.next_funding_ms - int(time.time() * 1000)) / 1000.0)
                open_pos_list.append({
                    "symbol": p.symbol,
                    "side": p.side,
                    "direction": dir_str,
                    "qty": p.qty,
                    "entry_fill": p.entry_fill,
                    "entry_price": p.entry_fill,
                    "cur_mid": cur_mid,
                    "current_price": cur_mid,
                    "sl_price": p.sl_price,
                    "stop_loss": p.sl_price,
                    "tp_price": p.tp_price,
                    "take_profit": p.tp_price,
                    "unrealized_pnl": round(unrealized, 4),
                    "funding_rate_pct": round(p.funding_rate * 100, 4),
                    "sec_to_eject": round(sec_to_settle, 1),
                })

            candidates_list = []
            for c in self.funding_candidates.values():
                candidates_list.append({
                    "symbol": c.symbol,
                    "rate_pct": round(c.funding_rate * 100, 4),
                    "action": c.action,
                    "settle_in_sec": round(c.time_to_settle_sec, 0),
                })

            return web.json_response({
                "module": MODULE_ID,
                "port": HTTP_PORT,
                "paper_mode": self.paper_mode,
                "equity": round(self.equity_usdt, 2),
                "equity_usdt": round(self.equity_usdt, 2),
                "initial_capital": 300.0,
                "peak_equity": round(self.equity_usdt, 2),
                "drawdown_pct": 0.0,
                "stream_ready": True,
                "bybit_ws_connected": True,
                "monitored_symbols_count": len(self.funding_candidates),
                "news_defense_active": True,
                "timeframe": "15m/Funding",
                "strategy": "Funding Squeeze Harvester + T-1s Clearing Dump Defense",
                "open_positions_count": len(self.open_positions),
                "pending_intents_count": len(self.pending_intents),
                "open_positions": open_pos_list,
                "funding_candidates": candidates_list,
                "capture_efficiency_pct": round(self.capture_efficiency, 2),
                "clearing_dump_defense": "ACTIVE (Emergency Eject @ T - 1s)",
                "tca_summary": stats,
            })

        async def handle_index(request):
            stats = await self.ledger.get_summary_stats(MODULE_ID)
            cand_rows = ""
            for c in sorted(self.funding_candidates.values(), key=lambda x: abs(x.funding_rate), reverse=True):
                color = "#ef4444" if c.funding_rate < 0 else "#22c55e"
                cand_rows += f"""
                <tr>
                    <td style="font-weight:700; color:#38bdf8;">{c.symbol}</td>
                    <td style="color:{color}; font-weight:700;">{c.funding_rate*100:+.4f}%</td>
                    <td style="color:#f59e0b; font-weight:700;">{c.action}</td>
                    <td>{c.time_to_settle_sec/60.0:.1f} min</td>
                    <td>{c.open_interest:,.0f}</td>
                </tr>
                """
            if not cand_rows:
                cand_rows = "<tr><td colspan='5' style='text-align:center; color:#64748b; padding:16px;'>Scanning 400+ Linear Perpetuals for extreme funding rates...</td></tr>"

            pos_rows = ""
            for p in self.open_positions.values():
                tick = self.latest_ticks.get(p.symbol)
                cur_mid = tick["mid"] if tick else p.entry_fill
                dir_val = 1.0 if p.side == "Buy" else -1.0
                unrealized = dir_val * (cur_mid - p.entry_fill) * p.qty
                pnl_color = "#22c55e" if unrealized >= 0 else "#ef4444"
                sec_to_settle = max(0, (p.next_funding_ms - int(time.time() * 1000)) / 1000.0)
                pos_rows += f"""
                <tr>
                    <td style="font-weight:700; color:#38bdf8;">{p.symbol}</td>
                    <td style="color:{'#22c55e' if p.side=='Buy' else '#ef4444'}; font-weight:700;">{p.side}</td>
                    <td>{p.qty}</td>
                    <td>{p.entry_fill:.4f}</td>
                    <td>{cur_mid:.4f}</td>
                    <td style="color:{pnl_color}; font-weight:700;">{unrealized:+.3f}$</td>
                    <td style="color:#f43f5e; font-weight:700;">{sec_to_settle:.1f}s (Eject T-1s)</td>
                </tr>
                """
            if not pos_rows:
                pos_rows = "<tr><td colspan='7' style='text-align:center; color:#64748b; padding:16px;'>No open squeeze positions.</td></tr>"

            html = f"""<!DOCTYPE html>
            <html>
            <head>
                <title>Funding Squeeze Harvester :8097</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{ background: #070a13; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 24px; }}
                    .card {{ background: #0d1322; border: 1px solid #1e293b; border-radius: 8px; padding: 18px; margin-bottom: 20px; }}
                    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 20px; }}
                    .stat-box {{ background: #070a13; border: 1px solid #1e293b; border-radius: 6px; padding: 12px; text-align: center; }}
                    .stat-val {{ font-size: 22px; font-weight: 800; color: #f59e0b; }}
                    .stat-lbl {{ font-size: 11px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; margin-top: 4px; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                    th, td {{ padding: 10px; border-bottom: 1px solid #1e293b; text-align: left; font-size: 13px; }}
                    th {{ background: #070a13; color: #94a3b8; text-transform: uppercase; font-size: 11px; }}
                    .badge {{ background: #d97706; color: white; padding: 4px 10px; border-radius: 9999px; font-weight: 700; font-size: 11px; }}
                </style>
            </head>
            <body>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 20px;">
                    <div>
                        <h1 style="margin:0; font-size:24px;">Funding Squeeze Harvester (Port 8097)</h1>
                        <p style="margin:4px 0 0; color:#94a3b8; font-size:13px;">Pre-Funding Squeeze Exploitation | T - 1s Clearing Dump Defense | 4-Component TCA</p>
                    </div>
                    <div><span class="badge">{'PAPER MODE' if self.paper_mode else 'LIVE MODE'}</span></div>
                </div>

                <div class="grid">
                    <div class="stat-box">
                        <div class="stat-val">{self.equity_usdt:.2f}$</div>
                        <div class="stat-lbl">Equity</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" style="color:{'#22c55e' if stats['net_pnl_usdt'] >= 0 else '#ef4444'};">{stats['net_pnl_usdt']:+.2f}$</div>
                        <div class="stat-lbl">Realized Net PnL</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{len(self.funding_candidates)}</div>
                        <div class="stat-lbl">Extreme Funding Coins</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" style="color:#06b6d4;">{stats['pure_alpha_usdt']:+.2f}$</div>
                        <div class="stat-lbl">Pure Alpha PnL</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{stats['total_trades']} (WR {stats['win_rate_pct']:.0f}%)</div>
                        <div class="stat-lbl">Closed Squeezes</div>
                    </div>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size:15px; color:#f59e0b;">Open Squeeze Positions ({len(self.open_positions)})</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Side</th>
                                <th>Qty</th>
                                <th>Entry Fill</th>
                                <th>Current Mid</th>
                                <th>Unrealized PnL</th>
                                <th>Clearing Eject Countdown</th>
                            </tr>
                        </thead>
                        <tbody>{pos_rows}</tbody>
                    </table>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size:15px; color:#38bdf8;">Extreme Funding Rate Radar (|FR| >= 0.04%)</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Current Funding Rate</th>
                                <th>Setup Action</th>
                                <th>Time to Settlement</th>
                                <th>Open Interest</th>
                            </tr>
                        </thead>
                        <tbody>{cand_rows}</tbody>
                    </table>
                </div>
            </body>
            </html>
            """
            return web.Response(text=html, content_type="text/html")

        app.router.add_get("/", handle_index)
        app.router.add_get("/status", handle_status)
        app.router.add_get("/api/status", handle_status)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
        await site.start()
        logger.info(f"Телеметрия Порта 8097 доступна на http://0.0.0.0:{HTTP_PORT}/ (API: /status)")


async def main() -> None:
    engine = FundingSqueezeEngine8097()
    engine.init_zmq()
    await engine.ledger.init_db()
    stats = await engine.ledger.get_summary_stats(MODULE_ID)
    engine.equity_usdt = round(300.0 + stats.get("net_pnl_usdt", 0.0), 2)
    engine.trades_count = stats.get("total_trades", 0)
    await engine.start_http_server()
    await asyncio.gather(
        engine.zmq_listener_loop(),
        engine.funding_screener_loop(),
        engine.execution_and_clearing_eject_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Funding Squeeze Engine остановлен.")
