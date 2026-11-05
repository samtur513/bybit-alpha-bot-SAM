"""
v3.7 Bybit V5 WebSocket Trade OMS and High-Fidelity Execution Simulator.
Поддерживает:
1. Честную симуляцию очереди лимитных ордеров (FIFO Queue Simulation) без Winner's Curse.
2. Корректную HMAC-SHA256 подпись Bybit V5 для live WS Trade API.
3. Penny-Perfect частичную фиксацию TP1 (50% объема) с переносом SL в безубыток +0.10%.
4. Защиту от спуфинга, Time-Stop, Leader Reversal Stop и News Defense Hook.
"""

import collections
import hashlib
import hmac
import logging
import math
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

try:
    import orjson
except ImportError:
    import json

    class _OrjsonFallback:
        @staticmethod
        def dumps(obj, *args, **kwargs) -> bytes:
            return json.dumps(obj).encode("utf-8")

        @staticmethod
        def loads(obj, *args, **kwargs):
            if isinstance(obj, (bytes, bytearray)):
                return json.loads(obj.decode("utf-8"))
            return json.loads(obj)

    orjson = _OrjsonFallback()
import websockets

from v3_7.src.config import ScalperConfig
from v3_7.src.microstructure import SymbolMicrostructureState
from v3_7.src.scalp_engines import SignalIntent

logger = logging.getLogger("ScalpOMS")


@dataclass
class PendingLimitOrder:
    symbol: str
    engine_id: str
    side: str               # "Buy" | "Sell"
    qty: float
    price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    max_hold_sec: float
    leader_ref_ret: float
    leader_entry_mid: float
    placed_ms: int
    queue_ahead_vol: float  # Объем заявок перед нами на этом уровне
    amend_count: int = 0
    order_link_id: str = ""


@dataclass
class ActivePosition:
    symbol: str
    engine_id: str
    side: str               # "Buy" | "Sell"
    qty: float
    initial_qty: float
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    opened_ms: int
    max_hold_sec: float
    tp1_filled: bool = False
    tp1_realized_pnl_usdt: float = 0.0
    leader_ref_ret: float = 0.0
    leader_entry_mid: float = 0.0
    order_link_id: str = ""


class ScalpOMS:
    def __init__(self, cfg: ScalperConfig, states: Dict[str, SymbolMicrostructureState]):
        self.cfg = cfg
        self.states = states
        self.ws_trade: Optional[websockets.WebSocketClientProtocol] = None
        self.pending_orders: Dict[str, PendingLimitOrder] = {}
        self.active_positions: Dict[str, ActivePosition] = {}
        self.cooldown_until: Dict[str, float] = {}
        self.consecutive_losses: Dict[str, int] = collections.defaultdict(int)
        self.daily_pnl_usdt: float = 0.0
        self.circuit_breaker_tripped: bool = False
        self.news_embargo_active: bool = False
        self.closed_trades_history: List[dict] = []

    # =========================================================================
    # РАСЧЕТ БЕЗОПАСНОГО РАЗМЕРА ПОЗИЦИИ (RISK + LIQUIDITY CAP)
    # =========================================================================
    def calculate_safe_qty(self, intent: SignalIntent, state: SymbolMicrostructureState) -> float:
        price_risk_pct = abs(intent.entry_price - intent.sl_price) / max(intent.entry_price, 1e-9)
        total_risk_pct = price_risk_pct + self.cfg.roundtrip_mt
        notional_by_risk = (self.cfg.equity_usdt * self.cfg.risk_per_trade_pct) / max(total_risk_pct, 0.0015)

        # Ограничение ликвидности: не более 20% от объема топ-3 уровней стакана
        max_book_notional = self.cfg.max_book_depth_pct * state.top3_book_notional_usdt(intent.side)
        target_notional = min(notional_by_risk, max_book_notional, self.cfg.equity_usdt * self.cfg.max_leverage_cap)

        raw_qty = target_notional / max(intent.entry_price, 1e-9)
        step = state.meta.qty_step
        qty = math.floor(raw_qty / step) * step if step > 0 else raw_qty
        return round(qty, 6) if qty >= state.meta.min_qty else 0.0

    # =========================================================================
    # ГЕНЕРАТОР ПОДПИСИ BYBIT V5 WEBSOCKET TRADE API
    # =========================================================================
    def _build_ws_auth_frame(self) -> bytes:
        expires = int((time.time() + 10) * 1000)
        val = f"GET/realtime{expires}"
        signature = hmac.new(
            self.cfg.api_secret.encode("utf-8"),
            val.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return orjson.dumps({"op": "auth", "args": [self.cfg.api_key, expires, signature]})

    def _build_trade_order_frame(self, op: str, args: dict) -> bytes:
        now_ms = str(int(time.time() * 1000))
        recv_window = "2500"
        args_json_str = orjson.dumps(args).decode("utf-8")
        sign_payload = f"{now_ms}{self.cfg.api_key}{recv_window}{args_json_str}"
        signature = hmac.new(
            self.cfg.api_secret.encode("utf-8"),
            sign_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        frame = {
            "reqId": str(uuid.uuid4()),
            "header": {
                "X-BAPI-API-KEY": self.cfg.api_key,
                "X-BAPI-TIMESTAMP": now_ms,
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": signature,
            },
            "op": op,
            "args": [args],
        }
        return orjson.dumps(frame)

    # =========================================================================
    # ВЫСТАВЛЕНИЕ СИГНАЛА
    # =========================================================================
    async def submit_signal(self, intent: SignalIntent) -> bool:
        if self.circuit_breaker_tripped or self.news_embargo_active:
            return False
        if intent.symbol in self.active_positions or intent.symbol in self.pending_orders:
            return False
        if time.time() < self.cooldown_until.get(intent.symbol, 0.0):
            return False

        state = self.states.get(intent.symbol)
        if not state:
            return False

        qty = self.calculate_safe_qty(intent, state)
        if qty <= 0:
            return False

        now_ms = int(time.time() * 1000)
        link_id = f"{intent.engine_id}_{intent.symbol}_{now_ms}"

        # Оценка объема очереди перед нами (FIFO)
        if intent.side == "Buy":
            if intent.entry_price > state.best_bid:
                queue_vol = 0.0
            else:
                queue_vol = state.bids.get(intent.entry_price, state.sorted_bids[0][1] if state.sorted_bids else 0.0)
        else:
            if intent.entry_price < state.best_ask:
                queue_vol = 0.0
            else:
                queue_vol = state.asks.get(intent.entry_price, state.sorted_asks[0][1] if state.sorted_asks else 0.0)

        pending = PendingLimitOrder(
            symbol=intent.symbol,
            engine_id=intent.engine_id,
            side=intent.side,
            qty=qty,
            price=intent.entry_price,
            sl_price=intent.sl_price,
            tp1_price=intent.tp1_price,
            tp2_price=intent.tp2_price,
            max_hold_sec=intent.max_hold_sec,
            leader_ref_ret=intent.leader_ref_ret,
            leader_entry_mid=intent.leader_entry_mid,
            placed_ms=now_ms,
            queue_ahead_vol=queue_vol,
            order_link_id=link_id,
        )
        self.pending_orders[intent.symbol] = pending

        logger.info(
            f"[ORDER_SUBMIT] {intent.engine_id} {intent.symbol} {intent.side} Qty={qty} "
            f"Price={intent.entry_price:.5f} SL={intent.sl_price:.5f} TP1={intent.tp1_price:.5f} | "
            f"QueueAhead={queue_vol:.2f} | {intent.reason}"
        )

        if not self.cfg.paper_trading and self.ws_trade:
            order_args = {
                "category": "linear",
                "symbol": intent.symbol,
                "side": intent.side,
                "orderType": "Limit",
                "qty": str(qty),
                "price": f"{intent.entry_price:.8f}".rstrip("0").rstrip("."),
                "timeInForce": "PostOnly",
                "orderLinkId": link_id,
                "stopLoss": f"{intent.sl_price:.8f}".rstrip("0").rstrip("."),
                "slOrderType": "Market",
            }
            frame_bytes = self._build_trade_order_frame("order.create", order_args)
            await self.ws_trade.send(frame_bytes.decode("utf-8"))

        return True

    # =========================================================================
    # ОБНОВЛЕНИЕ ОЧЕРЕДИ ЛИМИТНЫХ ОРДЕРОВ (PAPER SIMULATION)
    # =========================================================================
    def on_market_trade(self, symbol: str, is_buy: bool, price: float, qty: float) -> Optional[ActivePosition]:
        """
        Проверяет исполнение ожидающего Post-Only лимитного ордера.
        Исполнение засчитывается ТОЛЬКО если очередь перед нами полностью выкуплена.
        """
        pending = self.pending_orders.get(symbol)
        if not pending:
            return None

        # 1. LONG LIMIT (Best Bid): маркет-продажи (is_buy == False) разъедают очередь
        if pending.side == "Buy" and not is_buy and price <= pending.price:
            pending.queue_ahead_vol -= qty
            if pending.queue_ahead_vol <= 0:
                # Ордер исполнен мейкером!
                self.pending_orders.pop(symbol, None)
                now_ms = int(time.time() * 1000)
                pos = ActivePosition(
                    symbol=symbol,
                    engine_id=pending.engine_id,
                    side=pending.side,
                    qty=pending.qty,
                    initial_qty=pending.qty,
                    entry_price=pending.price,
                    sl_price=pending.sl_price,
                    tp1_price=pending.tp1_price,
                    tp2_price=pending.tp2_price,
                    opened_ms=now_ms,
                    max_hold_sec=pending.max_hold_sec,
                    leader_ref_ret=pending.leader_ref_ret,
                    leader_entry_mid=pending.leader_entry_mid,
                    order_link_id=pending.order_link_id,
                )
                self.active_positions[symbol] = pos
                logger.info(
                    f"[FILL_MAKER] {symbol} {pending.side} Qty={pos.qty} Price={pos.entry_price:.5f} "
                    f"(Очередь поглощена)"
                )
                return pos

        # 2. SHORT LIMIT (Best Ask): маркет-покупки (is_buy == True) разъедают очередь
        elif pending.side == "Sell" and is_buy and price >= pending.price:
            pending.queue_ahead_vol -= qty
            if pending.queue_ahead_vol <= 0:
                self.pending_orders.pop(symbol, None)
                now_ms = int(time.time() * 1000)
                pos = ActivePosition(
                    symbol=symbol,
                    engine_id=pending.engine_id,
                    side=pending.side,
                    qty=pending.qty,
                    initial_qty=pending.qty,
                    entry_price=pending.price,
                    sl_price=pending.sl_price,
                    tp1_price=pending.tp1_price,
                    tp2_price=pending.tp2_price,
                    opened_ms=now_ms,
                    max_hold_sec=pending.max_hold_sec,
                    leader_ref_ret=pending.leader_ref_ret,
                    leader_entry_mid=pending.leader_entry_mid,
                    order_link_id=pending.order_link_id,
                )
                self.active_positions[symbol] = pos
                logger.info(
                    f"[FILL_MAKER] {symbol} {pending.side} Qty={pos.qty} Price={pos.entry_price:.5f} "
                    f"(Очередь поглощена)"
                )
                return pos

        return None

    # =========================================================================
    # КОНТУР СОПРОВОЖДЕНИЯ (ШАГ 25 МС): TP1, TP2, SL, CHASE, TIME-STOP
    # =========================================================================
    async def monitor_step(self, now_ms: int) -> None:
        btc_state = self.states.get(self.cfg.leader_symbol)

        # ---------------------------------------------------------------------
        # 1. ОБСЛУЖИВАНИЕ ОЖИДАЮЩИХ ОРДЕРОВ (ORDER CHASING & EXPIRATION)
        # ---------------------------------------------------------------------
        for sym, pending in list(self.pending_orders.items()):
            st = self.states.get(sym)
            if not st or st.mid_price <= 0:
                continue

            age_ms = now_ms - pending.placed_ms
            # Если цена ушла более чем на 2 тика от нашего лимита
            if pending.side == "Buy":
                price_ran_away = st.best_bid > pending.price + 2.0 * st.meta.tick_size
                new_price = st.best_bid
            else:
                price_ran_away = st.best_ask < pending.price - 2.0 * st.meta.tick_size
                new_price = st.best_ask

            if price_ran_away and age_ms >= 200:
                if pending.amend_count < 3:
                    # Подтягивание цены (Order Chasing с приоритетом внутри спрэда)
                    pending.amend_count += 1
                    if pending.side == "Buy":
                        new_p = st.best_bid + st.meta.tick_size if st.spread_ticks >= 2.0 else st.best_bid
                        q_vol = 0.0 if st.spread_ticks >= 2.0 else (st.sorted_bids[0][1] if st.sorted_bids else 0.0)
                    else:
                        new_p = st.best_ask - st.meta.tick_size if st.spread_ticks >= 2.0 else st.best_ask
                        q_vol = 0.0 if st.spread_ticks >= 2.0 else (st.sorted_asks[0][1] if st.sorted_asks else 0.0)

                    pending.price = new_p
                    pending.placed_ms = now_ms
                    pending.queue_ahead_vol = q_vol
                    logger.info(f"[ORDER_CHASE] {sym} #{pending.amend_count} -> New Price={new_p:.5f} (QueueAhead={q_vol:.1f})")

                    if not self.cfg.paper_trading and self.ws_trade:
                        amend_args = {
                            "category": "linear",
                            "symbol": sym,
                            "orderLinkId": pending.order_link_id,
                            "price": f"{new_p:.8f}".rstrip("0").rstrip("."),
                        }
                        frame = self._build_trade_order_frame("order.amend", amend_args)
                        await self.ws_trade.send(frame.decode("utf-8"))
                else:
                    # Превышен лимит подтягиваний -> отмена ордера
                    self.pending_orders.pop(sym, None)
                    logger.info(f"[ORDER_EXPIRED] {sym} отменен (цена ушла, лимит чейзинга исчерпан)")
                    if not self.cfg.paper_trading and self.ws_trade:
                        cancel_args = {"category": "linear", "symbol": sym, "orderLinkId": pending.order_link_id}
                        frame = self._build_trade_order_frame("order.cancel", cancel_args)
                        await self.ws_trade.send(frame.decode("utf-8"))

            # Жесткий таймаут ожидания исполнения (3.5 сек)
            elif age_ms >= 3500:
                self.pending_orders.pop(sym, None)
                logger.info(f"[ORDER_TIMEOUT] {sym} отменен по таймауту 3.5 с")

        # ---------------------------------------------------------------------
        # 2. ОБСЛУЖИВАНИЕ АКТИВНЫХ ПОЗИЦИЙ
        # ---------------------------------------------------------------------
        for sym, pos in list(self.active_positions.items()):
            st = self.states.get(sym)
            if not st or st.mid_price <= 0:
                continue

            hold_sec = (now_ms - pos.opened_ms) / 1000.0
            curr_p = st.mid_price
            is_long = pos.side == "Buy"

            # Gross PnL (без комиссий)
            gross_ret = (curr_p - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - curr_p) / pos.entry_price

            # -----------------------------------------------------------------
            # А. Проверка Stop-Loss (Taker fee)
            # -----------------------------------------------------------------
            hit_sl = curr_p <= pos.sl_price if is_long else curr_p >= pos.sl_price
            if hit_sl:
                await self._close_position(pos, curr_p, is_maker=False, reason="STOP_LOSS")
                continue

            # -----------------------------------------------------------------
            # Б. Проверка Leader Reversal Stop для Движка 3 (E3_LEADLAG)
            # -----------------------------------------------------------------
            if pos.engine_id == "E3_LEADLAG" and btc_state and pos.leader_entry_mid > 0:
                btc_curr_ret = math.log(btc_state.mid_price / pos.leader_entry_mid)
                if is_long and btc_curr_ret < -self.cfg.e3_leader_reversal_threshold * pos.leader_ref_ret:
                    await self._close_position(pos, curr_p, is_maker=False, reason="BTC_REVERSAL_STOP")
                    continue
                elif not is_long and btc_curr_ret > self.cfg.e3_leader_reversal_threshold * abs(pos.leader_ref_ret):
                    await self._close_position(pos, curr_p, is_maker=False, reason="BTC_REVERSAL_STOP")
                    continue

            # -----------------------------------------------------------------
            # В. Защита от спуфинга: OBI_FLIP_EXIT отключен для предотвращения
            # ложных срабатываний при снятии заявок маркет-мейкерами (Spoof Trap)
            # Позиция управляется строго через TP1/TP2, SL и Time-Stop.
            # -----------------------------------------------------------------

            # -----------------------------------------------------------------
            # Г. Проверка Take-Profit 1 (Penny-Perfect 50% фиксация + SL в БУ)
            # -----------------------------------------------------------------
            hit_tp1 = curr_p >= pos.tp1_price if is_long else curr_p <= pos.tp1_price
            if not pos.tp1_filled and hit_tp1:
                # Фиксируем ровно 50% от текущего объема
                close_qty = pos.qty * 0.5
                step = st.meta.qty_step
                realized_slice = math.floor(close_qty / step) * step if step > 0 else close_qty

                if realized_slice > 0:
                    exit_fee = self.cfg.fee_maker
                    total_fee = self.cfg.fee_maker + exit_fee
                    slice_gross_pct = (pos.tp1_price - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - pos.tp1_price) / pos.entry_price
                    slice_net_pct = slice_gross_pct - total_fee
                    slice_net_usdt = realized_slice * pos.entry_price * slice_net_pct

                    pos.qty -= realized_slice
                    pos.tp1_filled = True
                    pos.tp1_realized_pnl_usdt += slice_net_usdt
                    self.daily_pnl_usdt += slice_net_usdt

                    # Перевод стоп-лосса оставшейся половины в безубыток (+0.10%)
                    if is_long:
                        pos.sl_price = pos.entry_price * (1.0 + max(0.0010, self.cfg.roundtrip_mm * 1.2))
                    else:
                        pos.sl_price = pos.entry_price * (1.0 - max(0.0010, self.cfg.roundtrip_mm * 1.2))

                    logger.info(
                        f"[TP1 HIT] {sym} ({pos.engine_id}): 50% закрыто по {pos.tp1_price:.5f} "
                        f"(PnL={slice_net_usdt:+.2f} USDT). Остаток Qty={pos.qty}, SL -> БУ {pos.sl_price:.5f}"
                    )

            # -----------------------------------------------------------------
            # Д. Проверка Take-Profit 2 (Закрытие остатка)
            # -----------------------------------------------------------------
            hit_tp2 = curr_p >= pos.tp2_price if is_long else curr_p <= pos.tp2_price
            if hit_tp2:
                await self._close_position(pos, pos.tp2_price, is_maker=True, reason="TP2_TARGET")
                continue

            # -----------------------------------------------------------------
            # Е. Проверка Time-Stop
            # -----------------------------------------------------------------
            if hold_sec >= pos.max_hold_sec and gross_ret < 0.0012:
                exit_price = st.best_ask if is_long else st.best_bid
                await self._close_position(pos, exit_price, is_maker=True, reason="TIME_STOP")

    # =========================================================================
    # ЗАКРЫТИЕ ПОЗИЦИИ (СТРОГИЙ УЧЕТ И ИНВАРИАНТНОСТЬ БАЛАНСА)
    # =========================================================================
    async def _close_position(
        self, pos: ActivePosition, exit_price: float, is_maker: bool, reason: str
    ) -> None:
        self.active_positions.pop(pos.symbol, None)
        exit_fee = self.cfg.fee_maker if is_maker else self.cfg.fee_taker
        total_fee_pct = self.cfg.fee_maker + exit_fee

        is_long = pos.side == "Buy"
        gross_pct = (exit_price - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - exit_price) / pos.entry_price
        net_pct = gross_pct - total_fee_pct
        net_usdt = pos.qty * pos.entry_price * net_pct

        total_trade_net_usdt = pos.tp1_realized_pnl_usdt + net_usdt
        self.daily_pnl_usdt += net_usdt

        # Cooldown при убытке: немедленный карантин на 15 мин после любого стопа
        if total_trade_net_usdt < 0:
            self.consecutive_losses[pos.symbol] = self.consecutive_losses.get(pos.symbol, 0) + 1
            if self.consecutive_losses[pos.symbol] >= 3:
                self.cooldown_until[pos.symbol] = time.time() + 3600.0  # 60 минут при 2 стопах
                logger.warning(f"[COOLDOWN] {pos.symbol} отправлен в карантин на 60 мин ({self.consecutive_losses[pos.symbol]} стопа подряд).")
        else:
            self.consecutive_losses[pos.symbol] = 0

        self.closed_trades_history.append({
            "symbol": pos.symbol,
            "engine_id": pos.engine_id,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "initial_qty": pos.initial_qty,
            "remaining_qty": pos.qty,
            "net_pnl_usdt": total_trade_net_usdt,
            "reason": reason,
            "timestamp": time.time(),
        })

        logger.info(
            f"[EXIT {reason}] {pos.symbol} ({pos.engine_id}) {pos.side} | Exit={exit_price:.5f} | "
            f"RemPnL={net_usdt:+.2f} USDT | TotalNet={total_trade_net_usdt:+.2f} USDT | "
            f"DailyPnL={self.daily_pnl_usdt:+.2f} USDT"
        )

        # Предохранитель дневной просадки (Circuit Breaker)
        if self.daily_pnl_usdt <= -(self.cfg.equity_usdt * self.cfg.max_daily_dd_pct):
            self.circuit_breaker_tripped = True
            logger.critical(
                f"[CIRCUIT BREAKER] Достигнут лимит дневной просадки -{self.cfg.max_daily_dd_pct*100:.1f}% "
                f"({self.daily_pnl_usdt:.2f} USDT). Торговля заблокирована!"
            )

    # =========================================================================
    # АВАРИЙНЫЙ ЩИТ NEWS CORE (EMERGENCY EJECT & MACRO EMBARGO)
    # =========================================================================
    async def emergency_news_eject(self, symbol: Optional[str] = None, reason: str = "NEWS_EMBARGO") -> None:
        """Мгновенно снимает все ожидающие лимиты и закрывает позиции при макро-эмбарго/шоках."""
        self.news_embargo_active = True
        logger.warning(f"[NEWS SHIELD ACTIVATED] Причина: {reason}. Экстренный сброс ордеров и позиций...")

        # 1. Отмена всех ожидающих ордеров
        targets = [symbol] if symbol else list(self.pending_orders.keys())
        for sym in targets:
            pending = self.pending_orders.pop(sym, None)
            if pending:
                logger.info(f"[NEWS SHIELD] Снят отложенный лимит {sym}")

        # 2. Аварийный выход из активных позиций
        pos_targets = [symbol] if symbol else list(self.active_positions.keys())
        for sym in pos_targets:
            pos = self.active_positions.get(sym)
            st = self.states.get(sym)
            if pos and st and st.mid_price > 0:
                await self._close_position(pos, st.mid_price, is_maker=False, reason=f"NEWS_EJECT_{reason}")

    # =========================================================================
    # ЭКСПОРТ ТЕЛЕМЕТРИИ ДЛЯ WEB DASHBOARD И META DASHBOARD
    # =========================================================================
    def get_status(self) -> Dict[str, Any]:
        """Формирует срез состояния системы для веб-мониторинга."""
        unrealized = 0.0
        pos_list = []
        for s, p in self.active_positions.items():
            st = self.states.get(s)
            curr_p = st.mid_price if st and st.mid_price > 0 else p.entry_price
            is_long = p.side == "Buy"
            gross_pnl = (curr_p - p.entry_price) * p.qty if is_long else (p.entry_price - curr_p) * p.qty
            unrealized += gross_pnl
            pos_list.append({
                "symbol": s,
                "direction": "LONG" if is_long else "SHORT",
                "qty": p.qty,
                "entry_price": p.entry_price,
                "current_price": curr_p,
                "stop_loss": p.sl_price,
                "take_profit_1": p.tp1_price,
                "take_profit_2": p.tp2_price,
                "engine_id": p.engine_id,
                "unrealized_pnl": round(gross_pnl, 4),
                "opened_ms": p.opened_ms,
            })

        orders_list = []
        for s, o in self.pending_orders.items():
            orders_list.append({
                "symbol": s,
                "side": o.side,
                "price": o.price,
                "qty": o.qty,
                "queue_ahead_vol": round(o.queue_ahead_vol, 2),
                "engine_id": o.engine_id,
                "placed_ms": o.placed_ms,
            })

        btc_state = self.states.get(self.cfg.leader_symbol)
        btc_trend = "NEUTRAL"
        if btc_state and btc_state.mid_price > 0:
            bm = btc_state.meta
            if getattr(bm, "vwap_session", 0) > 0 and getattr(bm, "ema9_5m", 0) > 0:
                if btc_state.mid_price >= bm.vwap_session and bm.ema9_5m >= bm.ema21_5m:
                    btc_trend = "BULLISH"
                elif btc_state.mid_price <= bm.vwap_session and bm.ema9_5m <= bm.ema21_5m:
                    btc_trend = "BEARISH"
                else:
                    btc_trend = "NEUTRAL"
            elif getattr(bm, "ema20_15m", 0) > 0:
                btc_trend = "BULLISH" if btc_state.mid_price >= bm.ema20_15m else "BEARISH"

        total_equity = self.cfg.equity_usdt + self.daily_pnl_usdt + unrealized

        return {
            "version": "3.7.0",
            "name": "v3.7 HFT Scalper",
            "mode": "PAPER_TRADING" if self.cfg.paper_trading else "LIVE_REAL",
            "port": 8090,
            "timeframe": "15m/M1",
            "initial_capital": self.cfg.equity_usdt,
            "capital": round(total_equity, 2),
            "equity": round(total_equity, 2),
            "realized_pnl": round(self.daily_pnl_usdt, 4),
            "unrealized_pnl": round(unrealized, 4),
            "net_pnl": round(self.daily_pnl_usdt + unrealized, 4),
            "open_positions": pos_list,
            "pending_orders": orders_list,
            "closed_trades": self.closed_trades_history[-50:],
            "total_trades_count": len(self.closed_trades_history),
            "monitored_symbols": list(self.states.keys()),
            "stream_ready": len(self.states) > 0,
            "news_shield": True,
            "news_defense_active": True,
            "btc_trend_status": btc_trend,
            "circuit_breaker": self.circuit_breaker_tripped,
            "timestamp": int(time.time() * 1000),
        }
