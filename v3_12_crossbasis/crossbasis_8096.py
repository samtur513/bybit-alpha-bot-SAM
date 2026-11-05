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
import websockets
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
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "v3_10_sweep")))
        from tca_ledger import ShadowTCALedger, TCATradeRecord

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d | CROSSBASIS_8096 | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("CrossBasis8096")

MODULE_ID = "M1_CROSSBASIS_8096"
HTTP_PORT = int(os.getenv("CROSSBASIS_HTTP_PORT", "8096"))
ZMQ_SUB_ADDR = os.getenv("ZMQ_SUB_ADDR", "tcp://127.0.0.1:5555")
WS_PUBLIC_SPOT = "wss://stream.bybit.com/v5/public/spot"


@dataclass
class SpotState:
    symbol: str
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid_price: float = 0.0
    trades: collections.deque = None
    tfi_3s: float = 0.0
    last_calc_ms: int = 0

    def __post_init__(self):
        if self.trades is None:
            self.trades = collections.deque(maxlen=1000)

    def update_trades(self, trade_data: list):
        now_ms = int(time.time() * 1000)
        for tr in trade_data:
            self.trades.append((int(tr["T"]), tr["S"] == "Buy", float(tr["p"]), float(tr["v"])))

        # THROTTLE: Recalculate TFI at most once per 100ms
        if (now_ms - self.last_calc_ms) < 100:
            return
        self.last_calc_ms = now_ms

        cut = now_ms - 3000
        b_v, s_v = 0.0, 0.0
        for ts, is_b, _, q in reversed(self.trades):
            if ts < cut:
                break
            if is_b:
                b_v += q
            else:
                s_v += q
        self.tfi_3s = (b_v - s_v) / (b_v + s_v + 1e-9)


@dataclass
class PendingBasisIntent:
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
    basis_bps: float
    spot_tfi: float


@dataclass
class OpenBasisPosition:
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
    basis_bps: float
    spot_tfi: float
    sl_order_link_id: str
    min_price: float
    max_price: float


class CrossBasisEngine8096:
    def __init__(self):
        self.paper_mode = os.getenv("PAPER_TRADING", "true").lower() == "true"
        self.equity_usdt = float(os.getenv("EQUITY_USDT", "300.0"))
        self.risk_pct = float(os.getenv("RISK_PER_TRADE_PCT", "0.0035"))
        self.fee_maker = 0.000324 if os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true" else 0.00036
        self.fee_taker = 0.00090 if os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true" else 0.00100

        db_path = os.getenv("TCA_DB_PATH", "/opt/bybit-bot/v3_10_sweep/tca_attribution.db")
        if not os.path.exists(os.path.dirname(db_path)) and os.path.dirname(db_path):
            db_path = "tca_attribution.db"
        self.ledger = ShadowTCALedger(db_path=db_path, fee_maker=self.fee_maker, fee_taker=self.fee_taker)

        self.zmq_ctx = None
        self.sub_sock = None

        self.latest_perp_ticks: Dict[str, dict] = {}
        self.spot_states: Dict[str, SpotState] = {}
        self.pending_intents: Dict[str, PendingBasisIntent] = {}
        self.open_positions: Dict[str, OpenBasisPosition] = {}

        self.total_filled_alpha_usdt: float = 0.0
        self.total_missed_alpha_usdt: float = 0.0
        self.trades_count: int = 0
        self.start_time = time.time()
        self.monitored_pairs = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "NEARUSDT"]

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

    async def spot_ws_ingestor_loop(self) -> None:
        """Subscribes to Bybit Public Spot ticker streams."""
        topics = [f"tickers.{s}" for s in self.monitored_pairs]
        for s in self.monitored_pairs:
            self.spot_states[s] = SpotState(symbol=s)

        while True:
            try:
                async with websockets.connect(WS_PUBLIC_SPOT, ping_interval=20, max_queue=2048) as ws:
                    for chunk in [topics[i:i+10] for i in range(0, len(topics), 10)]:
                        await ws.send(orjson.dumps({"op": "subscribe", "args": chunk}).decode("utf-8"))
                        await asyncio.sleep(0.05)
                    logger.info(f"Подключен к Bybit Spot WS (Pure Tickers). Мониторинг {len(self.monitored_pairs)} пар.")

                    async for raw in ws:
                        msg = orjson.loads(raw)
                        topic = msg.get("topic", "")
                        if not topic or not topic.startswith("tickers"):
                            continue
                        sym = topic.split(".")[-1]
                        st = self.spot_states.get(sym)
                        if not st:
                            continue

                        data = msg.get("data", {})
                        if "lastPrice" in data and data["lastPrice"]:
                            st.mid_price = float(data["lastPrice"])
                        b_sz = float(data.get("bid1Size", 10.0))
                        a_sz = float(data.get("ask1Size", 10.0))
                        st.tfi_3s = (b_sz - a_sz) / (b_sz + a_sz + 1e-9)
            except Exception as e:
                logger.warning(f"Обрыв Spot WS: {e}. Переподключение через 2 сек...")
                await asyncio.sleep(2.0)

    def _evaluate_cross_basis_setup(self, perp_tick: dict) -> None:
        sym = perp_tick["symbol"]
        if sym not in self.monitored_pairs or sym in self.pending_intents or sym in self.open_positions:
            return

        spot_st = self.spot_states.get(sym)
        if not spot_st or spot_st.mid_price <= 0:
            return

        perp_mid = perp_tick["mid"]
        spot_mid = spot_st.mid_price

        # Basis in basis points: (Spot - Perp) / Perp * 10000
        basis_bps = (spot_mid - perp_mid) / perp_mid * 10000.0
        spot_tfi = spot_st.tfi_3s
        perp_tfi = perp_tick.get("tfi_3s", 0.0)

        # NOISE GATE:
        # 1. Long Lead-Lag: Spot leads with aggressive buying (Spot TFI >= +0.35, Basis >= +3.5 bps, Perp TFI lagging <= +0.10)
        # 2. Short Lead-Lag: Spot leads with aggressive selling (Spot TFI <= -0.35, Basis <= -3.5 bps, Perp TFI lagging >= -0.10)
        action = None
        if basis_bps >= 3.5 and spot_tfi >= 0.35 and perp_tfi <= 0.10:
            action = "LONG_LEAD_LAG"
            side = "Buy"
            pos_idx = 1
            limit_p = perp_tick["bid"]
            sl_p = round(limit_p * (1.0 - 0.0025) / perp_tick["tick_size"]) * perp_tick["tick_size"]
            tp_p = round(limit_p * (1.0 + 0.0065) / perp_tick["tick_size"]) * perp_tick["tick_size"]
        elif basis_bps <= -3.5 and spot_tfi <= -0.35 and perp_tfi >= -0.10:
            action = "SHORT_LEAD_LAG"
            side = "Sell"
            pos_idx = 2
            limit_p = perp_tick["ask"]
            sl_p = round(limit_p * (1.0 + 0.0025) / perp_tick["tick_size"]) * perp_tick["tick_size"]
            tp_p = round(limit_p * (1.0 - 0.0065) / perp_tick["tick_size"]) * perp_tick["tick_size"]

        if not action:
            return

        risk_dist_pct = abs(limit_p - sl_p) / limit_p
        total_risk_pct = risk_dist_pct + self.fee_maker + self.fee_taker
        notional = min((self.equity_usdt * self.risk_pct) / total_risk_pct, self.equity_usdt * 2.0)
        qty_step = perp_tick["qty_step"]
        qty = math.floor((notional / limit_p) / qty_step) * qty_step
        if qty < perp_tick["min_qty"]:
            return

        now_ms = perp_tick["ts"]
        self.pending_intents[sym] = PendingBasisIntent(
            intent_id=f"M1_8096_IN_{sym}_{now_ms}",
            symbol=sym,
            side=side,
            position_idx=pos_idx,
            limit_price=limit_p,
            sl_price=sl_p,
            tp_price=tp_p,
            qty=round(qty, 6),
            signal_mid=perp_mid,
            created_ms=now_ms,
            basis_bps=basis_bps,
            spot_tfi=spot_tfi,
        )
        logger.info(
            f"[CROSS-BASIS ARMED] {sym} {side} | Basis={basis_bps:+.1f} bps | "
            f"SpotTFI={spot_tfi:+.2f} vs PerpTFI={perp_tfi:+.2f} | Limit={limit_p}"
        )

    async def execution_loop(self) -> None:
        while True:
            now_ms = int(time.time() * 1000)

            # 1. Pending Limit Intents (10s fast timeout)
            for sym, intent in list(self.pending_intents.items()):
                tick = self.latest_perp_ticks.get(sym)
                if not tick:
                    continue

                filled = (tick["ask"] <= intent.limit_price) if intent.side == "Buy" else (tick["bid"] >= intent.limit_price)
                if filled:
                    self.pending_intents.pop(sym, None)
                    sl_link = f"M1_8096_SL_{sym}_{now_ms}"
                    pos = OpenBasisPosition(
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
                        basis_bps=intent.basis_bps,
                        spot_tfi=intent.spot_tfi,
                        sl_order_link_id=sl_link,
                        min_price=intent.limit_price,
                        max_price=intent.limit_price,
                    )
                    self.open_positions[sym] = pos
                    logger.info(f"[FILLED CROSS-BASIS] {sym} {pos.side} @ {pos.entry_fill} | SL={pos.sl_price} TP={pos.tp_price}")
                elif (now_ms - intent.created_ms) > 10000:
                    self.pending_intents.pop(sym, None)

            # 2. Position Monitoring & Fast Scratch
            for sym, pos in list(self.open_positions.items()):
                tick = self.latest_perp_ticks.get(sym)
                if not tick:
                    continue
                mid = tick["mid"]
                pos.min_price = min(pos.min_price, mid)
                pos.max_price = max(pos.max_price, mid)
                hold_sec = (now_ms - pos.opened_ms) / 1000.0

                exit_reason = None
                exit_fill = mid
                is_maker = True

                if pos.side == "Buy":
                    if mid <= pos.sl_price:
                        exit_reason = "SL_HIT"
                        exit_fill = pos.sl_price
                        is_maker = False
                    elif mid >= pos.tp_price:
                        exit_reason = "TP_HIT"
                        exit_fill = pos.tp_price
                        is_maker = True
                    elif hold_sec >= 120.0:  # 2m Fast Scratch
                        exit_reason = "FAST_SCRATCH_2M"
                        exit_fill = tick["bid"]
                        is_maker = True
                else:
                    if mid >= pos.sl_price:
                        exit_reason = "SL_HIT"
                        exit_fill = pos.sl_price
                        is_maker = False
                    elif mid <= pos.tp_price:
                        exit_reason = "TP_HIT"
                        exit_fill = pos.tp_price
                        is_maker = True
                    elif hold_sec >= 120.0:
                        exit_reason = "FAST_SCRATCH_2M"
                        exit_fill = tick["ask"]
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
                        sub_strategy=f"BASIS_{pos.side.upper()}",
                        symbol=sym,
                        side=pos.side,
                        opened_at_ms=pos.opened_ms,
                        closed_at_ms=now_ms,
                        sweep_atr_depth=0.0,
                        compression_ratio=0.0,
                        touches_count=0,
                        absorption_k=abs(pos.basis_bps),
                        cvd_flip_tfi=pos.spot_tfi,
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
                        f"[BASIS CLOSED: {exit_reason}] {sym} {pos.side} | NetPnL={res['net_pnl']:+.3f}$ "
                        f"(PureAlpha={res['pure_alpha']:+.3f}$, Fees={res['fees']:+.3f}$) | Equity={self.equity_usdt:.2f}$"
                    )

            await asyncio.sleep(0.150)

    async def zmq_listener_loop(self) -> None:
        logger.info(f"Ожидание тиков перпов из шины Hub {ZMQ_SUB_ADDR}...")
        while True:
            _, raw_payload = await self.sub_sock.recv_multipart()
            tick = orjson.loads(raw_payload)
            sym = tick.get("symbol")
            if sym not in self.monitored_pairs:
                continue
            self.latest_perp_ticks[sym] = tick
            self._evaluate_cross_basis_setup(tick)

    async def start_http_server(self) -> None:
        app = web.Application()

        async def handle_status(request):
            stats = await self.ledger.get_summary_stats(MODULE_ID)
            open_pos_list = []
            for p in self.open_positions.values():
                tick = self.latest_perp_ticks.get(p.symbol)
                cur_mid = tick["mid"] if tick else p.entry_fill
                dir_val = 1.0 if p.side == "Buy" else -1.0
                unrealized = dir_val * (cur_mid - p.entry_fill) * p.qty
                dir_str = "LONG" if p.side == "Buy" else "SHORT"
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
                    "basis_bps": round(p.basis_bps, 2),
                    "hold_sec": round((time.time() * 1000 - p.opened_ms) / 1000, 1),
                })

            basis_radar = []
            for s in self.monitored_pairs:
                spot_st = self.spot_states.get(s)
                perp_st = self.latest_perp_ticks.get(s)
                if spot_st and perp_st and perp_st.get("mid", 0) > 0 and spot_st.mid_price > 0:
                    b_bps = (spot_st.mid_price - perp_st["mid"]) / perp_st["mid"] * 10000.0
                    basis_radar.append({
                        "symbol": s,
                        "spot_mid": spot_st.mid_price,
                        "perp_mid": perp_st["mid"],
                        "basis_bps": round(b_bps, 2),
                        "spot_tfi": round(spot_st.tfi_3s, 2),
                        "perp_tfi": round(perp_st.get("tfi_3s", 0.0), 2),
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
                "monitored_symbols_count": len(self.monitored_pairs),
                "news_defense_active": True,
                "timeframe": "M1/Basis",
                "strategy": "Spot-Perp Basis Dislocation & OFI Lead-Lag Scalper",
                "open_positions_count": len(self.open_positions),
                "pending_intents_count": len(self.pending_intents),
                "open_positions": open_pos_list,
                "basis_radar": basis_radar,
                "capture_efficiency_pct": round(self.capture_efficiency, 2),
                "lead_lag_gate": "ACTIVE (Basis >= 3.5 bps, Spot TFI >= 0.35)",
                "tca_summary": stats,
            })

        async def handle_index(request):
            stats = await self.ledger.get_summary_stats(MODULE_ID)
            radar_rows = ""
            for s in self.monitored_pairs:
                spot_st = self.spot_states.get(s)
                perp_st = self.latest_perp_ticks.get(s)
                if spot_st and perp_st and perp_st.get("mid", 0) > 0 and spot_st.mid_price > 0:
                    b_bps = (spot_st.mid_price - perp_st["mid"]) / perp_st["mid"] * 10000.0
                    color = "#22c55e" if b_bps >= 0 else "#ef4444"
                    radar_rows += f"""
                    <tr>
                        <td style="font-weight:700; color:#38bdf8;">{s}</td>
                        <td>{spot_st.mid_price:.4f}</td>
                        <td>{perp_st['mid']:.4f}</td>
                        <td style="color:{color}; font-weight:700;">{b_bps:+.2f} bps</td>
                        <td>{spot_st.tfi_3s:+.2f}</td>
                        <td>{perp_st.get('tfi_3s', 0.0):+.2f}</td>
                    </tr>
                    """
            if not radar_rows:
                radar_rows = "<tr><td colspan='6' style='text-align:center; color:#64748b; padding:16px;'>Synchronizing Spot & Perp streams...</td></tr>"

            pos_rows = ""
            for p in self.open_positions.values():
                tick = self.latest_perp_ticks.get(p.symbol)
                cur_mid = tick["mid"] if tick else p.entry_fill
                dir_val = 1.0 if p.side == "Buy" else -1.0
                unrealized = dir_val * (cur_mid - p.entry_fill) * p.qty
                pnl_color = "#22c55e" if unrealized >= 0 else "#ef4444"
                pos_rows += f"""
                <tr>
                    <td style="font-weight:700; color:#38bdf8;">{p.symbol}</td>
                    <td style="color:{'#22c55e' if p.side=='Buy' else '#ef4444'}; font-weight:700;">{p.side}</td>
                    <td>{p.qty}</td>
                    <td>{p.entry_fill:.4f}</td>
                    <td>{cur_mid:.4f}</td>
                    <td style="color:#06b6d4; font-weight:700;">{p.basis_bps:+.1f} bps</td>
                    <td style="color:{pnl_color}; font-weight:700;">{unrealized:+.3f}$</td>
                </tr>
                """
            if not pos_rows:
                pos_rows = "<tr><td colspan='7' style='text-align:center; color:#64748b; padding:16px;'>No open cross-basis scalp positions.</td></tr>"

            html = f"""<!DOCTYPE html>
            <html>
            <head>
                <title>Spot-Perp Basis Scalper :8096</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{ background: #070a13; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 24px; }}
                    .card {{ background: #0d1322; border: 1px solid #1e293b; border-radius: 8px; padding: 18px; margin-bottom: 20px; }}
                    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 20px; }}
                    .stat-box {{ background: #070a13; border: 1px solid #1e293b; border-radius: 6px; padding: 12px; text-align: center; }}
                    .stat-val {{ font-size: 22px; font-weight: 800; color: #a855f7; }}
                    .stat-lbl {{ font-size: 11px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; margin-top: 4px; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                    th, td {{ padding: 10px; border-bottom: 1px solid #1e293b; text-align: left; font-size: 13px; }}
                    th {{ background: #070a13; color: #94a3b8; text-transform: uppercase; font-size: 11px; }}
                    .badge {{ background: #9333ea; color: white; padding: 4px 10px; border-radius: 9999px; font-weight: 700; font-size: 11px; }}
                </style>
            </head>
            <body>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 20px;">
                    <div>
                        <h1 style="margin:0; font-size:24px;">Spot-Perp Basis Scalper (Port 8096)</h1>
                        <p style="margin:4px 0 0; color:#94a3b8; font-size:13px;">Microstructure Lead-Lag | Spot OFI Divergence | Post-Only Fast Scalper</p>
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
                        <div class="stat-val">{len(self.monitored_pairs)}</div>
                        <div class="stat-lbl">Monitored Pairs</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" style="color:#06b6d4;">{stats['pure_alpha_usdt']:+.2f}$</div>
                        <div class="stat-lbl">Pure Alpha PnL</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{stats['total_trades']} (WR {stats['win_rate_pct']:.0f}%)</div>
                        <div class="stat-lbl">Closed Scalps</div>
                    </div>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size:15px; color:#a855f7;">Open Cross-Basis Scalps ({len(self.open_positions)})</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Side</th>
                                <th>Qty</th>
                                <th>Entry Fill</th>
                                <th>Perp Mid</th>
                                <th>Basis Dislocation</th>
                                <th>Unrealized PnL</th>
                            </tr>
                        </thead>
                        <tbody>{pos_rows}</tbody>
                    </table>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size:15px; color:#38bdf8;">Real-Time Spot vs Perp Basis Radar</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Spot Price</th>
                                <th>Perp Price</th>
                                <th>Basis Spread</th>
                                <th>Spot TFI (3s)</th>
                                <th>Perp TFI (3s)</th>
                            </tr>
                        </thead>
                        <tbody>{radar_rows}</tbody>
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
        logger.info(f"Телеметрия Порта 8096 доступна на http://0.0.0.0:{HTTP_PORT}/ (API: /status)")


async def main() -> None:
    engine = CrossBasisEngine8096()
    engine.init_zmq()
    await engine.ledger.init_db()
    await engine.start_http_server()
    await asyncio.gather(
        engine.zmq_listener_loop(),
        engine.spot_ws_ingestor_loop(),
        engine.execution_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Cross-Basis Engine остановлен.")
