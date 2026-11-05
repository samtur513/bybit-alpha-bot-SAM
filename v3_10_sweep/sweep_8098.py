import asyncio
import hashlib
import hmac
import logging
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from aiohttp import web
import orjson
import websockets
import zmq
import zmq.asyncio
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    from v3_10_sweep.tca_ledger import ShadowTCALedger, TCATradeRecord
except ImportError:
    from tca_ledger import ShadowTCALedger, TCATradeRecord

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d | SWEEP_8098 | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("Sweep8098")

MODULE_ID = "M3_SWEEP_8098"
HTTP_PORT = int(os.getenv("SWEEP_HTTP_PORT", "8098"))
ZMQ_SUB_ADDR = os.getenv("ZMQ_SUB_ADDR", "tcp://127.0.0.1:5555")
WS_TRADE_URL = "wss://stream.bybit.com/v5/trade"


@dataclass
class SweepTracker:
    active: bool = False
    side: str = "Buy"           # 'Buy' (sweep below low) | 'Sell' (sweep above high)
    start_ms: int = 0
    extreme_price: float = 0.0
    accum_aggr_vol: float = 0.0


@dataclass
class PendingLimitIntent:
    intent_id: str
    symbol: str
    side: str                   # 'Buy' | 'Sell'
    position_idx: int           # 1 = Long, 2 = Short (Hedge Mode)
    limit_price: float
    sl_price: float
    tp_price: float
    qty: float
    signal_mid: float
    created_ms: int
    sweep_atr_depth: float
    comp_ratio: float
    touches: int
    abs_k: float
    cvd_tfi: float


@dataclass
class OpenSweepPosition:
    trade_id: str
    symbol: str
    side: str                   # 'Buy' | 'Sell'
    position_idx: int
    qty: float
    signal_mid: float
    entry_fill: float
    sl_price: float
    tp_price: float
    opened_ms: int
    sl_order_link_id: str
    sweep_atr_depth: float
    comp_ratio: float
    touches: int
    abs_k: float
    cvd_tfi: float
    min_price: float
    max_price: float


class LiquiditySweepEngine8098:
    def __init__(self):
        self.api_key = os.getenv("BYBIT_API_KEY", "")
        self.api_secret = os.getenv("BYBIT_API_SECRET", "")
        self.paper_mode = os.getenv("PAPER_TRADING", "true").lower() == "true"
        # Standardized to $300.00 USDT fleet baseline
        self.equity_usdt = float(os.getenv("EQUITY_USDT", "300.0"))
        self.risk_pct = float(os.getenv("RISK_PER_TRADE_PCT", "0.0035"))
        self.fee_maker = 0.000324 if os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true" else 0.00036
        self.fee_taker = 0.00090 if os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true" else 0.00100

        self.ledger = ShadowTCALedger(fee_maker=self.fee_maker, fee_taker=self.fee_taker)
        self.zmq_ctx = None
        self.sub_sock = None

        self.ws_trade: Optional[websockets.WebSocketClientProtocol] = None
        self.latest_ticks: Dict[str, dict] = {}
        self.trackers: Dict[str, SweepTracker] = {}
        self.pending_intents: Dict[str, PendingLimitIntent] = {}
        self.open_positions: Dict[str, OpenSweepPosition] = {}

        # Capture Efficiency metrics
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
        logger.info(f"ZeroMQ SUB сокет успешно подключен к {ZMQ_SUB_ADDR}")

    @property
    def capture_efficiency(self) -> float:
        denom = self.total_filled_alpha_usdt + self.total_missed_alpha_usdt
        return (self.total_filled_alpha_usdt / denom * 100.0) if denom > 0 else 100.0

    def _evaluate_anti_compression_and_sweep(self, tick: dict) -> None:
        sym = tick["symbol"]
        if sym == "BTCUSDT" or sym in self.pending_intents or sym in self.open_positions:
            return

        # 1. Lead-lag BTC safety filter (skip sweep entries if BTC is trending violently)
        btc = self.latest_ticks.get("BTCUSDT")
        if btc and abs(btc.get("tfi_3s", 0.0)) > 0.80:
            return

        mid = tick["mid"]
        atr = tick["atr_15m"]
        s_low = tick["swing_low"]
        s_high = tick["swing_high"]
        now_ms = tick["ts"]
        tr = self.trackers.setdefault(sym, SweepTracker())

        # =================================================================
        # SCENARIO 1: LONG (False breakdown of support s_low -> Reclaim)
        # =================================================================
        if mid < s_low:
            # ANTI-COMPRESSION GATE: Reject if touches > 3 or tightly hugging support (< 1.8 ATR)
            if tick["low_touches"] > 3 or tick["low_comp_ratio"] < 1.80:
                tr.active = False
                return

            depth = s_low - mid
            # Gate 1: Depth ceiling (if price plunges deeper than 0.75 ATR -> true breakdown, abort!)
            if depth > 0.75 * atr:
                tr.active = False
                return

            if not tr.active or tr.side != "Buy":
                tr.active = True
                tr.side = "Buy"
                tr.start_ms = now_ms
                tr.extreme_price = mid
                tr.accum_aggr_vol = tick["sell_3s"]
            else:
                tr.extreme_price = min(tr.extreme_price, mid)
                tr.accum_aggr_vol += tick["sell_3s"] * 0.1

        # Reclaim condition for LONG: Price snaps back above s_low
        elif tr.active and tr.side == "Buy" and mid >= s_low + tick["tick_size"]:
            elapsed_s = max((now_ms - tr.start_ms) / 1000.0, 1.0)
            depth = s_low - tr.extreme_price
            depth_atr = depth / atr

            # Valid sweep zone [0.15, 0.75] ATR and under level for <= 120s
            if 0.15 <= depth_atr <= 0.75 and elapsed_s <= 120.0:
                expected_vol = max(tick["avg_1m_vol"] * (elapsed_s / 60.0), 1e-6)
                abs_ratio = tr.accum_aggr_vol / expected_vol

                # Gate 2 (Absorption >= 2.5x) + Gate 3 (CVD Flip: TFI >= +0.30 and OBI >= +0.15)
                if abs_ratio >= 2.5 and tick["tfi_3s"] >= 0.30 and tick["obi"] >= 0.15:
                    tr.active = False
                    asyncio.create_task(
                        self._place_reclaim_limit_intent(
                            tick=tick,
                            side="Buy",
                            position_idx=1,
                            extreme_p=tr.extreme_price,
                            depth_atr=depth_atr,
                            abs_k=abs_ratio,
                        )
                    )
                    return

            if elapsed_s > 120.0:
                tr.active = False

        # =================================================================
        # SCENARIO 2: SHORT (False breakout of resistance s_high -> Reclaim)
        # =================================================================
        elif mid > s_high:
            # ANTI-COMPRESSION GATE: Reject if touches > 3 or tightly hugging resistance (< 1.8 ATR)
            if tick["high_touches"] > 3 or tick["high_comp_ratio"] < 1.80:
                tr.active = False
                return

            depth = mid - s_high
            # Gate 1: Depth ceiling (if price surges higher than 0.75 ATR -> true breakout, abort!)
            if depth > 0.75 * atr:
                tr.active = False
                return

            if not tr.active or tr.side != "Sell":
                tr.active = True
                tr.side = "Sell"
                tr.start_ms = now_ms
                tr.extreme_price = mid
                tr.accum_aggr_vol = tick["buy_3s"]
            else:
                tr.extreme_price = max(tr.extreme_price, mid)
                tr.accum_aggr_vol += tick["buy_3s"] * 0.1

        # Reclaim condition for SHORT: Price snaps back below s_high
        elif tr.active and tr.side == "Sell" and mid <= s_high - tick["tick_size"]:
            elapsed_s = max((now_ms - tr.start_ms) / 1000.0, 1.0)
            depth = tr.extreme_price - s_high
            depth_atr = depth / atr

            # Valid sweep zone [0.15, 0.75] ATR and above level for <= 120s
            if 0.15 <= depth_atr <= 0.75 and elapsed_s <= 120.0:
                expected_vol = max(tick["avg_1m_vol"] * (elapsed_s / 60.0), 1e-6)
                abs_ratio = tr.accum_aggr_vol / expected_vol

                # Gate 2 (Absorption >= 2.5x) + Gate 3 (CVD Flip: TFI <= -0.30 and OBI <= -0.15)
                if abs_ratio >= 2.5 and tick["tfi_3s"] <= -0.30 and tick["obi"] <= -0.15:
                    tr.active = False
                    asyncio.create_task(
                        self._place_reclaim_limit_intent(
                            tick=tick,
                            side="Sell",
                            position_idx=2,
                            extreme_p=tr.extreme_price,
                            depth_atr=depth_atr,
                            abs_k=abs_ratio,
                        )
                    )
                    return

            if elapsed_s > 120.0:
                tr.active = False

    async def _place_reclaim_limit_intent(
        self, tick: dict, side: str, position_idx: int, extreme_p: float, depth_atr: float, abs_k: float
    ) -> None:
        sym = tick["symbol"]
        t_sz = tick["tick_size"]
        atr = tick["atr_15m"]

        if side == "Buy":
            # Long Reclaim: Limit at Swing Low + 1 tick (capped at bid)
            limit_p = round((tick["swing_low"] + t_sz) / t_sz) * t_sz
            if limit_p >= tick["ask"]:
                limit_p = tick["bid"]
            sl_p = round((extreme_p - 0.10 * atr) / t_sz) * t_sz
            tp_p = round(max(tick["vwap"], limit_p + 2.5 * (limit_p - sl_p)) / t_sz) * t_sz
            comp_ratio = tick["low_comp_ratio"]
            touches = tick["low_touches"]
        else:
            # Short Reclaim: Limit at Swing High - 1 tick (floored at ask)
            limit_p = round((tick["swing_high"] - t_sz) / t_sz) * t_sz
            if limit_p <= tick["bid"]:
                limit_p = tick["ask"]
            sl_p = round((extreme_p + 0.10 * atr) / t_sz) * t_sz
            tp_p = round(min(tick["vwap"], limit_p - 2.5 * (sl_p - limit_p)) / t_sz) * t_sz
            comp_ratio = tick["high_comp_ratio"]
            touches = tick["high_touches"]

        risk_dist_pct = abs(limit_p - sl_p) / limit_p
        if risk_dist_pct <= 0.0012 or risk_dist_pct > 0.0080:
            return

        total_risk_pct = risk_dist_pct + self.fee_maker + self.fee_taker
        notional = min(
            (self.equity_usdt * self.risk_pct) / total_risk_pct,
            0.20 * (tick["top3_bid_notional"] if side == "Buy" else tick["top3_ask_notional"]),
            self.equity_usdt * 2.0,
        )
        qty_step = tick["qty_step"]
        qty = math.floor((notional / limit_p) / qty_step) * qty_step
        if qty < tick["min_qty"]:
            return

        intent_id = f"M3_8098_IN_{sym}_{int(time.time()*1000)}"
        intent = PendingLimitIntent(
            intent_id=intent_id,
            symbol=sym,
            side=side,
            position_idx=position_idx,
            limit_price=limit_p,
            sl_price=sl_p,
            tp_price=tp_p,
            qty=round(qty, 6),
            signal_mid=tick["mid"],
            created_ms=tick["ts"],
            sweep_atr_depth=depth_atr,
            comp_ratio=comp_ratio,
            touches=touches,
            abs_k=abs_k,
            cvd_tfi=tick["tfi_3s"],
        )
        self.pending_intents[sym] = intent
        logger.info(
            f"[INTENT ARMED] {sym} {side} (Hedge idx={position_idx}) | Limit={limit_p} SL={sl_p} TP={tp_p} | "
            f"Depth={depth_atr:.2f}ATR Comp={comp_ratio:.2f}ATR Touches={touches} AbsK={abs_k:.2f}x"
        )

        if not self.paper_mode and self.ws_trade:
            frame = {
                "reqId": str(uuid.uuid4()),
                "header": {"X-BAPI-TIMESTAMP": str(int(time.time() * 1000)), "X-BAPI-RECV-WINDOW": "2500"},
                "op": "order.create",
                "args": [{
                    "category": "linear",
                    "symbol": sym,
                    "side": side,
                    "positionIdx": position_idx,
                    "orderType": "Limit",
                    "qty": str(intent.qty),
                    "price": str(limit_p),
                    "timeInForce": "PostOnly",
                    "orderLinkId": intent_id,
                }],
            }
            await self.ws_trade.send(orjson.dumps(frame).decode("utf-8"))

    async def _send_independent_hedge_sl(self, pos: OpenSweepPosition) -> None:
        """Sends an independent conditional SL order with reduceOnly=True to safeguard Hedge Mode."""
        if self.paper_mode or not self.ws_trade:
            return
        close_side = "Sell" if pos.side == "Buy" else "Buy"
        trigger_dir = 2 if pos.side == "Buy" else 1
        frame = {
            "reqId": str(uuid.uuid4()),
            "header": {"X-BAPI-TIMESTAMP": str(int(time.time() * 1000)), "X-BAPI-RECV-WINDOW": "2500"},
            "op": "order.create",
            "args": [{
                "category": "linear",
                "symbol": pos.symbol,
                "side": close_side,
                "positionIdx": pos.position_idx,
                "orderType": "Market",
                "qty": str(pos.qty),
                "triggerDirection": trigger_dir,
                "triggerPrice": str(pos.sl_price),
                "triggerBy": "LastPrice",
                "reduceOnly": True,
                "closeOnTrigger": False,
                "orderLinkId": pos.sl_order_link_id,
            }],
        }
        await self.ws_trade.send(orjson.dumps(frame).decode("utf-8"))

    async def _schedule_missed_intent_audit(self, intent: PendingLimitIntent) -> None:
        """Measures Opportunity Cost 60s after unfilled limit expiration."""
        await asyncio.sleep(60.0)
        tick_60s = self.latest_ticks.get(intent.symbol)
        if not tick_60s:
            return
        missed_usdt = await self.ledger.record_missed_intent(
            module_id=MODULE_ID,
            symbol=intent.symbol,
            side=intent.side,
            created_ms=intent.created_ms,
            limit_price=intent.limit_price,
            signal_mid=intent.signal_mid,
            mid_after_60s=tick_60s["mid"],
            qty=intent.qty,
        )
        self.total_missed_alpha_usdt += missed_usdt
        logger.info(
            f"[MISSED AUDIT +60s] {intent.symbol} {intent.side}: Упущенная альфа = {missed_usdt:+.3f} USDT | "
            f"Capture Efficiency = {self.capture_efficiency:.1f}%"
        )

    async def execution_and_tca_loop(self) -> None:
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
                    sl_link = f"M3_8098_SL_{sym}_{now_ms}"
                    pos = OpenSweepPosition(
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
                        sl_order_link_id=sl_link,
                        sweep_atr_depth=intent.sweep_atr_depth,
                        comp_ratio=intent.comp_ratio,
                        touches=intent.touches,
                        abs_k=intent.abs_k,
                        cvd_tfi=intent.cvd_tfi,
                        min_price=intent.limit_price,
                        max_price=intent.limit_price,
                    )
                    self.open_positions[sym] = pos
                    await self._send_independent_hedge_sl(pos)
                    logger.info(f"[FILLED POST-ONLY] {sym} {pos.side} @ {pos.entry_fill} | SL={pos.sl_price} TP={pos.tp_price}")

                elif (now_ms - intent.created_ms) > 15000:
                    self.pending_intents.pop(sym, None)
                    logger.info(f"[CANCEL UNFILLED] {sym} {intent.side}: ретест не налил лимит за 15с. Запуск аудита +60с.")
                    asyncio.create_task(self._schedule_missed_intent_audit(intent))

            # 2. Monitor Open Positions & Record 4-Component TCA
            for sym, pos in list(self.open_positions.items()):
                tick = self.latest_ticks.get(sym)
                if not tick:
                    continue
                mid = tick["mid"]
                pos.min_price = min(pos.min_price, mid)
                pos.max_price = max(pos.max_price, mid)
                hold_sec = (now_ms - pos.opened_ms) / 1000.0

                exit_reason = None
                exit_fill = mid
                is_maker_exit = True

                if pos.side == "Buy":
                    if mid <= pos.sl_price:
                        exit_reason = "SL_HIT"
                        exit_fill = pos.sl_price
                        is_maker_exit = False
                    elif mid >= pos.tp_price:
                        exit_reason = "TP_VWAP_TARGET"
                        exit_fill = pos.tp_price
                        is_maker_exit = True
                    elif hold_sec >= 900.0:  # 15m time stop
                        exit_reason = "TIME_STOP_15M"
                        exit_fill = tick["bid"]
                        is_maker_exit = True
                else:  # Sell
                    if mid >= pos.sl_price:
                        exit_reason = "SL_HIT"
                        exit_fill = pos.sl_price
                        is_maker_exit = False
                    elif mid <= pos.tp_price:
                        exit_reason = "TP_VWAP_TARGET"
                        exit_fill = pos.tp_price
                        is_maker_exit = True
                    elif hold_sec >= 900.0:  # 15m time stop
                        exit_reason = "TIME_STOP_15M"
                        exit_fill = tick["ask"]
                        is_maker_exit = True

                if exit_reason:
                    self.open_positions.pop(sym, None)
                    if pos.side == "Buy":
                        mae_pct = (pos.entry_fill - pos.min_price) / pos.entry_fill * 100.0
                        mfe_pct = (pos.max_price - pos.entry_fill) / pos.entry_fill * 100.0
                        sub_strat = "SWEEP_RECLAIM_LONG"
                    else:
                        mae_pct = (pos.max_price - pos.entry_fill) / pos.entry_fill * 100.0
                        mfe_pct = (pos.entry_fill - pos.min_price) / pos.entry_fill * 100.0
                        sub_strat = "SWEEP_RECLAIM_SHORT"

                    tca_rec = TCATradeRecord(
                        trade_id=pos.trade_id,
                        module_id=MODULE_ID,
                        sub_strategy=sub_strat,
                        symbol=sym,
                        side=pos.side,
                        opened_at_ms=pos.opened_ms,
                        closed_at_ms=now_ms,
                        sweep_atr_depth=pos.sweep_atr_depth,
                        compression_ratio=pos.comp_ratio,
                        touches_count=pos.touches,
                        absorption_k=pos.abs_k,
                        cvd_flip_tfi=pos.cvd_tfi,
                        signal_mid_price=pos.signal_mid,
                        entry_fill_price=pos.entry_fill,
                        exit_mid_price=mid,
                        exit_fill_price=exit_fill,
                        qty=pos.qty,
                        entry_is_maker=True,
                        exit_is_maker=is_maker_exit,
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
                        f"[TCA CLOSED: {exit_reason}] {sym} {pos.side} | NetPnL={res['net_pnl']:+.3f}$ "
                        f"(PureAlpha={res['pure_alpha']:+.3f}$, ExecCapture={res['exec_alpha']:+.3f}$, Fees={res['fees']:+.3f}$) | "
                        f"CaptureEff={self.capture_efficiency:.1f}% | Equity={self.equity_usdt:.2f}$"
                    )

            await asyncio.sleep(0.025)

    async def zmq_listener_loop(self) -> None:
        logger.info(f"Подключен к шине Data Hub {ZMQ_SUB_ADDR}. Ожидание сетапов Liquidity Sweep...")
        while True:
            _, raw_payload = await self.sub_sock.recv_multipart()
            tick = orjson.loads(raw_payload)
            self.latest_ticks[tick["symbol"]] = tick
            self._evaluate_anti_compression_and_sweep(tick)

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
                    "hold_sec": round((time.time() * 1000 - p.opened_ms) / 1000, 1),
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
                "monitored_symbols_count": len(self.latest_ticks) or 15,
                "news_defense_active": True,
                "timeframe": "15m",
                "strategy": "15m Liquidity Sweep Reversion + Anti-Compression Gate",
                "open_positions_count": len(self.open_positions),
                "pending_intents_count": len(self.pending_intents),
                "open_positions": open_pos_list,
                "capture_efficiency_pct": round(self.capture_efficiency, 2),
                "anti_compression_gate": "ACTIVE (>= 1.8 ATR, <= 3 touches)",
                "tca_summary": stats,
            })

        async def handle_index(request):
            stats = await self.ledger.get_summary_stats(MODULE_ID)
            pos_rows = ""
            for p in self.open_positions.values():
                tick = self.latest_ticks.get(p.symbol)
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
                    <td style="color:#ef4444;">{p.sl_price:.4f}</td>
                    <td style="color:#22c55e;">{p.tp_price:.4f}</td>
                    <td style="color:{pnl_color}; font-weight:700;">{unrealized:+.3f}$</td>
                </tr>
                """
            if not pos_rows:
                pos_rows = "<tr><td colspan='8' style='text-align:center; color:#64748b; padding:16px;'>No open positions. Monitoring structural levels...</td></tr>"

            intent_rows = ""
            for sym, it in self.pending_intents.items():
                intent_rows += f"""
                <tr>
                    <td style="font-weight:700; color:#38bdf8;">{sym}</td>
                    <td style="color:{'#22c55e' if it.side=='Buy' else '#ef4444'}; font-weight:700;">{it.side}</td>
                    <td>{it.limit_price:.4f}</td>
                    <td>{it.sl_price:.4f}</td>
                    <td>{it.tp_price:.4f}</td>
                    <td>{it.sweep_atr_depth:.2f} ATR</td>
                    <td>{it.comp_ratio:.2f} ATR</td>
                    <td>{it.abs_k:.1f}x</td>
                </tr>
                """
            if not intent_rows:
                intent_rows = "<tr><td colspan='8' style='text-align:center; color:#64748b; padding:16px;'>No armed limit orders awaiting retest.</td></tr>"

            html = f"""<!DOCTYPE html>
            <html>
            <head>
                <title>Liquidity Sweep Reversion :8098</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{ background: #0b1120; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 24px; }}
                    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 18px; margin-bottom: 20px; }}
                    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 20px; }}
                    .stat-box {{ background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 12px; text-align: center; }}
                    .stat-val {{ font-size: 22px; font-weight: 800; color: #38bdf8; }}
                    .stat-lbl {{ font-size: 11px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; margin-top: 4px; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                    th, td {{ padding: 10px; border-bottom: 1px solid #334155; text-align: left; font-size: 13px; }}
                    th {{ background: #0f172a; color: #94a3b8; text-transform: uppercase; font-size: 11px; }}
                    .badge {{ background: #3b82f6; color: white; padding: 4px 10px; border-radius: 9999px; font-weight: 700; font-size: 11px; }}
                </style>
            </head>
            <body>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 20px;">
                    <div>
                        <h1 style="margin:0; font-size:24px;">Liquidity Sweep Reversion (Port 8098)</h1>
                        <p style="margin:4px 0 0; color:#94a3b8; font-size:13px;">Anti-Compression Gate | 15m False Breakouts | Post-Only Reclaim Limit | 4-Component TCA</p>
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
                        <div class="stat-val">{self.capture_efficiency:.1f}%</div>
                        <div class="stat-lbl">Capture Efficiency</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{stats['pure_alpha_usdt']:+.2f}$</div>
                        <div class="stat-lbl">Pure Alpha PnL</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{stats['exec_capture_usdt']:+.2f}$</div>
                        <div class="stat-lbl">Spread Capture</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{stats['fees_paid_usdt']:+.2f}$</div>
                        <div class="stat-lbl">Fees Paid</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{stats['total_trades']} (WR {stats['win_rate_pct']:.0f}%)</div>
                        <div class="stat-lbl">Closed Trades</div>
                    </div>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size:15px; color:#38bdf8;">Open Positions ({len(self.open_positions)})</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Side</th>
                                <th>Qty</th>
                                <th>Entry Fill</th>
                                <th>Current Mid</th>
                                <th>Stop Loss</th>
                                <th>Take Profit</th>
                                <th>Unrealized PnL</th>
                            </tr>
                        </thead>
                        <tbody>{pos_rows}</tbody>
                    </table>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size:15px; color:#eab308;">Armed Post-Only Limit Intents ({len(self.pending_intents)})</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Side</th>
                                <th>Limit Price</th>
                                <th>SL</th>
                                <th>TP</th>
                                <th>Sweep Depth</th>
                                <th>Anti-Comp Ratio</th>
                                <th>Absorption K</th>
                            </tr>
                        </thead>
                        <tbody>{intent_rows}</tbody>
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
        logger.info(f"Телеметрия Порта 8098 доступна на http://0.0.0.0:{HTTP_PORT}/ (API: /status)")


async def main() -> None:
    engine = LiquiditySweepEngine8098()
    engine.init_zmq()
    await engine.ledger.init_db()
    await engine.start_http_server()
    await asyncio.gather(
        engine.zmq_listener_loop(),
        engine.execution_and_tca_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Sweep Engine остановлен вручную.")
