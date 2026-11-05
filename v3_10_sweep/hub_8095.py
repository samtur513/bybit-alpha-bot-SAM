import asyncio
import collections
import logging
import math
import os
import sys
import time
from typing import Dict, List, Tuple

import aiohttp
from aiohttp import web
import numpy as np
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
    from v3_10_sweep.tca_ledger import ShadowTCALedger
except ImportError:
    from tca_ledger import ShadowTCALedger

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d | HUB_8095 | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("Hub8095")

WS_PUBLIC_LINEAR = "wss://stream.bybit.com/v5/public/linear"
REST_BASE = "https://api.bybit.com"
ZMQ_PUB_ADDR = os.getenv("ZMQ_PUB_ADDR", "tcp://127.0.0.1:5555")
HTTP_PORT = int(os.getenv("HUB_HTTP_PORT", "8095"))


class SymbolHubState:
    def __init__(self, symbol: str, tick_size: float, qty_step: float, min_qty: float):
        self.symbol = symbol
        self.tick_size = tick_size
        self.qty_step = qty_step
        self.min_qty = min_qty

        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.mid_price: float = 0.0
        self.obi_10: float = 0.0
        self.top3_bid_notional: float = 0.0
        self.top3_ask_notional: float = 0.0
        self.obi_weights = np.exp(-0.25 * np.arange(10))

        # Ring buffer of recent trades: (ts_ms, is_buy, price, qty)
        self.trades: collections.deque = collections.deque(maxlen=3000)

        # 15m structural metrics for Anti-Compression Gate (refreshed every 60s)
        self.atr_15m: float = 0.0
        self.vwap_range: float = 0.0
        self.swing_low: float = 0.0
        self.swing_high: float = 0.0
        self.low_touches: int = 0
        self.high_touches: int = 0
        self.low_compression_ratio: float = 0.0   # (recent_6_high - swing_low) / ATR_15m
        self.high_compression_ratio: float = 0.0  # (swing_high - recent_6_low) / ATR_15m
        self.avg_1m_vol: float = 1.0
        self.last_pub_ms: int = 0
        self.cur_vol_24h: float = 0.0

    def update_ticker(self, data: dict) -> None:
        old_mid = self.mid_price
        old_vol = self.cur_vol_24h

        if "bid1Price" in data and data["bid1Price"]:
            self.best_bid = float(data["bid1Price"])
        if "ask1Price" in data and data["ask1Price"]:
            self.best_ask = float(data["ask1Price"])
        if "lastPrice" in data and data["lastPrice"]:
            last_p = float(data["lastPrice"])
            if self.best_bid <= 0:
                self.best_bid = last_p
            if self.best_ask <= 0:
                self.best_ask = last_p
            self.mid_price = 0.5 * (self.best_bid + self.best_ask)
        elif self.best_bid > 0 and self.best_ask > 0:
            self.mid_price = 0.5 * (self.best_bid + self.best_ask)

        b_sz = float(data.get("bid1Size", 10.0))
        a_sz = float(data.get("ask1Size", 10.0))
        self.top3_bid_notional = b_sz * self.best_bid
        self.top3_ask_notional = a_sz * self.best_ask
        self.obi_10 = (b_sz - a_sz) / (b_sz + a_sz + 1e-9)

        # Tick-rule flow estimation without flood of publicTrade
        now_ms = int(time.time() * 1000)
        if "volume24h" in data and data["volume24h"]:
            new_vol = float(data["volume24h"])
            if old_vol > 0 and new_vol > old_vol:
                delta_q = new_vol - old_vol
                is_buy = self.mid_price >= old_mid
                self.trades.append((now_ms, is_buy, self.mid_price, delta_q))
            self.cur_vol_24h = new_vol
        elif self.mid_price != old_mid and old_mid > 0:
            delta_q = max(0.05 * b_sz, self.min_qty)
            is_buy = self.mid_price > old_mid
            self.trades.append((now_ms, is_buy, self.mid_price, delta_q))

    def update_l2_book(self, msg_type: str, data: dict) -> None:
        if msg_type == "snapshot":
            self.bids = {float(p): float(v) for p, v in data.get("b", [])}
            self.asks = {float(p): float(v) for p, v in data.get("a", [])}
        else:
            for p_s, v_s in data.get("b", []):
                p, v = float(p_s), float(v_s)
                if v == 0.0:
                    self.bids.pop(p, None)
                else:
                    self.bids[p] = v
            for p_s, v_s in data.get("a", []):
                p, v = float(p_s), float(v_s)
                if v == 0.0:
                    self.asks.pop(p, None)
                else:
                    self.asks[p] = v

    def recompute_l2_metrics(self) -> bool:
        if not self.bids or not self.asks:
            return False
        s_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:10]
        s_asks = sorted(self.asks.items(), key=lambda x: x[0])[:10]
        if not s_bids or not s_asks:
            return False

        self.best_bid = s_bids[0][0]
        self.best_ask = s_asks[0][0]
        self.mid_price = 0.5 * (self.best_bid + self.best_ask)
        self.top3_bid_notional = sum(p * v for p, v in s_bids[:3])
        self.top3_ask_notional = sum(p * v for p, v in s_asks[:3])

        n = min(10, len(s_bids), len(s_asks))
        w = self.obi_weights[:n]
        wb = float(np.dot(w, [v for _, v in s_bids[:n]]))
        wa = float(np.dot(w, [v for _, v in s_asks[:n]]))
        self.obi_10 = (wb - wa) / (wb + wa + 1e-9)
        return True

    def update_trades(self, trades_data: list) -> None:
        for tr in trades_data:
            self.trades.append((int(tr["T"]), tr["S"] == "Buy", float(tr["p"]), float(tr["v"])))

    def compute_flow(self, now_ms: int, window_ms: int = 3000) -> Tuple[float, float, float]:
        """Returns (TFI_window, buy_vol_window, sell_vol_window)"""
        cut = now_ms - window_ms
        b_v, s_v = 0.0, 0.0
        for ts, is_b, _, q in reversed(self.trades):
            if ts < cut:
                break
            if is_b:
                b_v += q
            else:
                s_v += q
        tfi = (b_v - s_v) / (b_v + s_v + 1e-9)
        return tfi, b_v, s_v


class DataAndTCAHub8095:
    def __init__(self):
        self.zmq_ctx = None
        self.pub_sock = None
        self.states: Dict[str, SymbolHubState] = {}
        self.ledger = ShadowTCALedger()
        self.ticks_broadcasted: int = 0
        self.start_time = time.time()

    def init_zmq(self) -> None:
        self.zmq_ctx = zmq.asyncio.Context()
        self.pub_sock = self.zmq_ctx.socket(zmq.PUB)
        self.pub_sock.setsockopt(zmq.LINGER, 0)
        self.pub_sock.bind(ZMQ_PUB_ADDR)
        logger.info(f"ZeroMQ PUB сокет успешно привязан к {ZMQ_PUB_ADDR}")

    async def bootstrap_universe(self, max_symbols: int = 15) -> None:
        logger.info("Инициализация торговой вселенной из Bybit Linear...")
        specs = {}
        ranked = []
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(f"{REST_BASE}/v5/market/instruments-info?category=linear&limit=1000") as r1:
                    info = orjson.loads(await r1.read())
                    for it in info.get("result", {}).get("list", []):
                        if it.get("status") == "Trading" and it.get("quoteCoin") == "USDT" and it.get("contractType") == "LinearPerpetual":
                            specs[it["symbol"]] = (
                                float(it["priceFilter"]["tickSize"]),
                                float(it["lotSizeFilter"]["qtyStep"]),
                                float(it["lotSizeFilter"]["minOrderQty"]),
                            )

                async with session.get(f"{REST_BASE}/v5/market/tickers?category=linear") as r2:
                    tickers = orjson.loads(await r2.read())
                    for t in tickers.get("result", {}).get("list", []):
                        sym = t["symbol"]
                        if sym in specs and float(t.get("turnover24h", 0)) >= 50_000_000:
                            ranked.append((sym, float(t["turnover24h"])))
                    ranked.sort(key=lambda x: x[1], reverse=True)
        except Exception as e:
            logger.warning(f"Ошибка загрузки вселенной через REST: {e}. Используем резервный список.")

        fallback_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SUIUSDT", "NEARUSDT", "APTUSDT", "AVAXUSDT", "LINKUSDT", "ADAUSDT", "BNBUSDT", "ENAUSDT", "INJUSDT", "PEPEUSDT"]
        chosen = ["BTCUSDT"] + [s for s, _ in ranked if s != "BTCUSDT"][: max_symbols - 1]
        if len(chosen) < 5:
            chosen = fallback_symbols[:max_symbols]

        for sym in chosen:
            tk, qs, mq = specs.get(sym, (0.001, 0.01, 0.01))
            self.states[sym] = SymbolHubState(sym, tk, qs, mq)

        logger.info(f"Вселенная сформирована ({len(self.states)} монет): {list(self.states.keys())}")
        await self.refresh_structural_levels()

    async def refresh_structural_levels(self) -> None:
        """
        Calculates 15m Swing High/Low (4-24h window), touch count,
        and Anti-Compression ratios.
        """
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            for sym, st in self.states.items():
                try:
                    url = f"{REST_BASE}/v5/market/kline?category=linear&symbol={sym}&interval=15&limit=96"
                    async with session.get(url) as resp:
                        data = orjson.loads(await resp.read())
                        rows = data.get("result", {}).get("list", [])
                        if len(rows) < 40:
                            continue
                        rows.reverse()  # Oldest to newest
                        highs = np.array([float(r[2]) for r in rows])
                        lows = np.array([float(r[3]) for r in rows])
                        closes = np.array([float(r[4]) for r in rows])
                        vols = np.array([float(r[5]) for r in rows])

                        tr = np.maximum(
                            highs[1:] - lows[1:],
                            np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
                        )
                        atr = float(np.mean(tr[-14:]))
                        st.atr_15m = atr
                        st.avg_1m_vol = float(np.median(vols[-30:]) / 15.0)
                        st.vwap_range = float(np.sum(closes[-32:] * vols[-32:]) / max(np.sum(vols[-32:]), 1e-9))

                        # Window for structural level: -64 to -2 (excludes current active candles)
                        hist_lows = lows[-36:-2]
                        hist_highs = highs[-36:-2]
                        s_low = float(np.min(hist_lows))
                        s_high = float(np.max(hist_highs))
                        st.swing_low = s_low
                        st.swing_high = s_high

                        # Touch counting within 0.20 * ATR
                        touch_zone = 0.20 * atr
                        l_touches, h_touches = 0, 0
                        last_l_idx, last_h_idx = -10, -10
                        for idx, (lo, hi) in enumerate(zip(hist_lows, hist_highs)):
                            if abs(lo - s_low) <= touch_zone and (idx - last_l_idx) >= 3:
                                l_touches += 1
                                last_l_idx = idx
                            if abs(s_high - hi) <= touch_zone and (idx - last_h_idx) >= 3:
                                h_touches += 1
                                last_h_idx = idx

                        st.low_touches = max(l_touches, 2)
                        st.high_touches = max(h_touches, 2)

                        # Anti-Compression Gate metrics:
                        recent_6_high = float(np.max(highs[-7:-1]))
                        recent_6_low = float(np.min(lows[-7:-1]))
                        st.low_compression_ratio = max(recent_6_high - s_low, 0.0) / max(atr, 1e-9)
                        st.high_compression_ratio = max(s_high - recent_6_low, 0.0) / max(atr, 1e-9)
                except Exception as e:
                    logger.warning(f"Ошибка расчета уровней для {sym}: {e}")

        logger.info("Структурные уровни 15m и метрики Anti-Compression успешно обновлены.")

    async def periodic_kline_updater(self) -> None:
        while True:
            await asyncio.sleep(180.0)
            try:
                await self.refresh_structural_levels()
            except Exception as e:
                logger.warning(f"Ошибка periodic_kline_updater: {e}")

    async def ws_ingestor_loop(self) -> None:
        topics = [f"tickers.{s}" for s in self.states]
        topic_chunks = [topics[i:i+10] for i in range(0, len(topics), 10)]

        while True:
            try:
                async with websockets.connect(WS_PUBLIC_LINEAR, ping_interval=20, max_queue=2048) as ws:
                    for chunk in topic_chunks:
                        await ws.send(orjson.dumps({"op": "subscribe", "args": chunk}).decode("utf-8"))
                        await asyncio.sleep(0.05)

                    logger.info(f"Подключен к Bybit WS (Clean Tickers). Трансляция для {len(self.states)} пар.")

                    async for raw in ws:
                        msg = orjson.loads(raw)
                        topic = msg.get("topic", "")
                        if not topic or not topic.startswith("tickers"):
                            continue
                        now_ms = int(msg.get("ts", time.time() * 1000))
                        sym = topic.split(".")[-1]
                        st = self.states.get(sym)
                        if not st:
                            continue

                        st.update_ticker(msg.get("data", {}))

                        # THROTTLING GATE: Broadcast max 2.5 Hz (400ms) per symbol
                        # Keeps 1-core VPS CPU < 1.5% while maintaining quantitative accuracy
                        if (now_ms - st.last_pub_ms) < 400:
                            continue

                        if st.mid_price <= 0 or st.atr_15m <= 0:
                            continue

                        st.last_pub_ms = now_ms
                        tfi_3s, buy_3s, sell_3s = st.compute_flow(now_ms, 3000)
                        payload = {
                            "ts": now_ms,
                            "symbol": sym,
                            "bid": st.best_bid,
                            "ask": st.best_ask,
                            "mid": st.mid_price,
                            "obi": st.obi_10,
                            "tfi_3s": tfi_3s,
                            "buy_3s": buy_3s,
                            "sell_3s": sell_3s,
                            "avg_1m_vol": st.avg_1m_vol,
                            "atr_15m": st.atr_15m,
                            "vwap": st.vwap_range,
                            "swing_low": st.swing_low,
                            "swing_high": st.swing_high,
                            "low_touches": st.low_touches,
                            "high_touches": st.high_touches,
                            "low_comp_ratio": st.low_compression_ratio,
                            "high_comp_ratio": st.high_compression_ratio,
                            "tick_size": st.tick_size,
                            "qty_step": st.qty_step,
                            "min_qty": st.min_qty,
                            "top3_bid_notional": st.top3_bid_notional,
                            "top3_ask_notional": st.top3_ask_notional,
                        }
                        if self.pub_sock:
                            await self.pub_sock.send_multipart([sym.encode("utf-8"), orjson.dumps(payload)])
                            self.ticks_broadcasted += 1
            except Exception as e:
                logger.error(f"Обрыв Bybit WS: {e}. Переподключение через 2 сек...")
                await asyncio.sleep(2.0)

    async def start_http_status_server(self) -> None:
        app = web.Application()

        async def handle_status(request):
            uptime_s = int(time.time() - self.start_time)
            symbols_summary = {}
            for sym, st in self.states.items():
                symbols_summary[sym] = {
                    "mid": st.mid_price,
                    "spread_bps": round((st.best_ask - st.best_bid) / max(st.mid_price, 1e-9) * 10000, 2),
                    "atr_15m": round(st.atr_15m, 4),
                    "swing_low": round(st.swing_low, 4),
                    "swing_high": round(st.swing_high, 4),
                    "low_touches": st.low_touches,
                    "high_touches": st.high_touches,
                    "low_comp_ratio": round(st.low_compression_ratio, 2),
                    "high_comp_ratio": round(st.high_compression_ratio, 2),
                }
            return web.json_response({
                "service": "Data_and_TCA_Hub",
                "port": HTTP_PORT,
                "status": "ONLINE",
                "uptime_seconds": uptime_s,
                "zmq_pub": ZMQ_PUB_ADDR,
                "ticks_broadcasted": self.ticks_broadcasted,
                "symbols_count": len(self.states),
                "symbols": symbols_summary,
            })

        async def handle_index(request):
            uptime_min = round((time.time() - self.start_time) / 60, 1)
            rows = ""
            for sym, st in self.states.items():
                spread_bps = round((st.best_ask - st.best_bid) / max(st.mid_price, 1e-9) * 10000, 2)
                rows += f"""
                <tr>
                    <td style="font-weight:700; color:#38bdf8;">{sym}</td>
                    <td>{st.mid_price:.4f}</td>
                    <td>{spread_bps} bps</td>
                    <td>{st.atr_15m:.4f}</td>
                    <td style="color:#ef4444;">{st.swing_low:.4f} ({st.low_touches}t, comp {st.low_compression_ratio:.1f}x)</td>
                    <td style="color:#22c55e;">{st.swing_high:.4f} ({st.high_touches}t, comp {st.high_compression_ratio:.1f}x)</td>
                    <td>{st.obi_10:+.2f}</td>
                </tr>
                """
            html = f"""<!DOCTYPE html>
            <html>
            <head>
                <title>Data & TCA Hub :8095</title>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{ background: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 24px; }}
                    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 18px; margin-bottom: 20px; }}
                    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 20px; }}
                    .stat-box {{ background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 14px; text-align: center; }}
                    .stat-val {{ font-size: 24px; font-weight: 800; color: #38bdf8; }}
                    .stat-lbl {{ font-size: 11px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; margin-top: 4px; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
                    th, td {{ padding: 10px; border-bottom: 1px solid #334155; text-align: left; font-size: 13px; }}
                    th {{ background: #0f172a; color: #94a3b8; text-transform: uppercase; font-size: 11px; }}
                    .badge {{ background: #16a34a; color: white; padding: 4px 10px; border-radius: 9999px; font-weight: 700; font-size: 11px; }}
                </style>
            </head>
            <body>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 20px;">
                    <div>
                        <h1 style="margin:0; font-size:24px;">Data & TCA Hub (Port 8095)</h1>
                        <p style="margin:4px 0 0; color:#94a3b8; font-size:13px;">Bybit L2 Orderbook + Trade Stream & ZeroMQ Real-Time Bus</p>
                    </div>
                    <div><span class="badge">ONLINE</span></div>
                </div>

                <div class="grid">
                    <div class="stat-box">
                        <div class="stat-val">{self.ticks_broadcasted:,}</div>
                        <div class="stat-lbl">Broadcasted Ticks</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{len(self.states)}</div>
                        <div class="stat-lbl">Active Symbols</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val">{uptime_min}m</div>
                        <div class="stat-lbl">Uptime</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-val" style="font-size:16px; margin-top:6px;">{ZMQ_PUB_ADDR}</div>
                        <div class="stat-lbl">ZeroMQ PUB Bus</div>
                    </div>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size:16px;">15m Structural Levels & Anti-Compression State</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Symbol</th>
                                <th>Mid Price</th>
                                <th>Spread</th>
                                <th>ATR (15m)</th>
                                <th>Swing Low (Touches, Comp)</th>
                                <th>Swing High (Touches, Comp)</th>
                                <th>OBI (10)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows}
                        </tbody>
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
        logger.info(f"HTTP-телеметрия Data Hub запущена на http://0.0.0.0:{HTTP_PORT}/ (API: /status)")


async def main() -> None:
    hub = DataAndTCAHub8095()
    hub.init_zmq()
    await hub.ledger.init_db()
    await hub.start_http_status_server()
    await hub.bootstrap_universe(max_symbols=15)
    await asyncio.gather(
        hub.ws_ingestor_loop(),
        hub.periodic_kline_updater(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Hub остановлен вручную.")
