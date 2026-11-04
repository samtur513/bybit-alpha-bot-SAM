"""
v3.7 Microstructure Engine: L2 Orderbook State, Trade Flow Imbalance and Absorption Metrics.
Высокоскоростная обработка стакана L2 (orderbook.50) и ленты сделок (publicTrade).
"""

import collections
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass
class InstrumentMeta:
    symbol: str
    tick_size: float
    qty_step: float
    min_qty: float
    natr_5m: float = 0.005
    atr_5m: float = 0.0
    atr_15m: float = 0.0
    hurst_15m: float = 0.5
    er_15m: float = 0.4
    ema9_5m: float = 0.0
    ema21_5m: float = 0.0
    ema20_15m: float = 0.0
    ema50_4h: float = 0.0
    vwap_session: float = 0.0
    swing_low_1h: float = 0.0
    swing_high_1h: float = 0.0
    beta_btc: float = 1.0
    corr_btc: float = 0.0


class SymbolMicrostructureState:
    """
    Высокоскоростной локальный слепок стакана L2 и кольцевой буфер сделок.
    Поддерживает расчет OBI, TFI, скорости разъедания плотностей (DR) и трекинг свипов (Long/Short).
    """

    def __init__(self, symbol: str, meta: InstrumentMeta):
        self.symbol = symbol
        self.meta = meta
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.sorted_bids: List[Tuple[float, float]] = []
        self.sorted_asks: List[Tuple[float, float]] = []

        # Кольцевой буфер сделок: (timestamp_ms, is_buy, price, qty)
        self.trades: collections.deque = collections.deque(maxlen=4000)

        # Кольцевой буфер mid-price и плотностей: (timestamp_ms, mid_price, top3_ask_vol, top3_bid_vol)
        self.mid_history: collections.deque = collections.deque(maxlen=2000)

        # Предвычисленные экспоненциальные веса для OBI по первым 10 уровням
        self.obi_weights = np.exp(-0.25 * np.arange(10))

        # Текущие метрики
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.mid_price: float = 0.0
        self.spread_ticks: float = 999.0
        self.obi: float = 0.0
        self.last_update_ms: int = 0

        # Трекинг свипа снизу (Long Sweep: закол swing_low_1h)
        self.sweep_low_active: bool = False
        self.sweep_min_price: float = 0.0
        self.sweep_start_ms: int = 0
        self.sweep_sell_vol: float = 0.0

        # Трекинг свипа сверху (Short Sweep: закол swing_high_1h)
        self.sweep_high_active: bool = False
        self.sweep_max_price: float = 0.0
        self.sweep_high_start_ms: int = 0
        self.sweep_buy_vol: float = 0.0

    def apply_orderbook(self, msg_type: str, data: dict, ts_ms: int) -> None:
        """Применяет snapshot или delta обновление книги L2."""
        if msg_type == "snapshot":
            self.bids.clear()
            self.asks.clear()
            for p_str, v_str in data.get("b", []):
                self.bids[float(p_str)] = float(v_str)
            for p_str, v_str in data.get("a", []):
                self.asks[float(p_str)] = float(v_str)
        else:
            for p_str, v_str in data.get("b", []):
                p, v = float(p_str), float(v_str)
                if v == 0.0:
                    self.bids.pop(p, None)
                else:
                    self.bids[p] = v
            for p_str, v_str in data.get("a", []):
                p, v = float(p_str), float(v_str)
                if v == 0.0:
                    self.asks.pop(p, None)
                else:
                    self.asks[p] = v

        # Сортируем топ-15 уровней
        self.sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:15]
        self.sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:15]

        if not self.sorted_bids or not self.sorted_asks:
            return

        self.best_bid = self.sorted_bids[0][0]
        self.best_ask = self.sorted_asks[0][0]
        self.mid_price = 0.5 * (self.best_bid + self.best_ask)
        self.spread_ticks = (self.best_ask - self.best_bid) / max(self.meta.tick_size, 1e-9)
        self.last_update_ms = ts_ms

        # Вычисление экспоненциально взвешенного OBI по топ-10 уровням
        n_lvl = min(10, len(self.sorted_bids), len(self.sorted_asks))
        if n_lvl > 0:
            b_vols = np.array([v for _, v in self.sorted_bids[:n_lvl]])
            a_vols = np.array([v for _, v in self.sorted_asks[:n_lvl]])
            w = self.obi_weights[:n_lvl]
            w_bid = float(np.dot(w, b_vols))
            w_ask = float(np.dot(w, a_vols))
            denom = w_bid + w_ask
            self.obi = (w_bid - w_ask) / denom if denom > 0 else 0.0

        top3_ask_v = sum(v for _, v in self.sorted_asks[:3])
        top3_bid_v = sum(v for _, v in self.sorted_bids[:3])
        self.mid_history.append((ts_ms, self.mid_price, top3_ask_v, top3_bid_v))

    def apply_trades(self, trade_list: list) -> None:
        """Добавляет сделки в кольцевой буфер и обновляет состояние свипов."""
        for tr in trade_list:
            ts = int(tr.get("T", tr.get("time", 0)))
            is_buy = tr.get("S") == "Buy" or tr.get("side") == "Buy"
            price = float(tr.get("p", tr.get("price", 0.0)))
            qty = float(tr.get("v", tr.get("size", 0.0)))
            self.trades.append((ts, is_buy, price, qty))

            # 1. Трекинг свипа снизу (Long)
            swing_l = self.meta.swing_low_1h
            if swing_l > 0 and price < swing_l:
                if not self.sweep_low_active:
                    self.sweep_low_active = True
                    self.sweep_min_price = price
                    self.sweep_start_ms = ts
                    self.sweep_sell_vol = qty if not is_buy else 0.0
                else:
                    if price < self.sweep_min_price:
                        self.sweep_min_price = price
                    if not is_buy:
                        self.sweep_sell_vol += qty

            # 2. Трекинг свипа сверху (Short)
            swing_h = self.meta.swing_high_1h
            if swing_h > 0 and price > swing_h:
                if not self.sweep_high_active:
                    self.sweep_high_active = True
                    self.sweep_max_price = price
                    self.sweep_high_start_ms = ts
                    self.sweep_buy_vol = qty if is_buy else 0.0
                else:
                    if price > self.sweep_max_price:
                        self.sweep_max_price = price
                    if is_buy:
                        self.sweep_buy_vol += qty

    def get_trade_flow_metrics(
        self, now_ms: int, window_ms: int = 2000
    ) -> Tuple[float, float, float, float, float]:
        """
        Возвращает метрики потока:
        (TFI, buy_vol_window, sell_vol_window, avg_buy_vol_60s_norm, avg_sell_vol_60s_norm)
        """
        cutoff_win = now_ms - window_ms
        cutoff_60s = now_ms - 60000
        buy_win = 0.0
        sell_win = 0.0
        buy_60s = 0.0
        sell_60s = 0.0

        for ts, is_buy, _, qty in reversed(self.trades):
            if ts < cutoff_60s:
                break
            if is_buy:
                buy_60s += qty
                if ts >= cutoff_win:
                    buy_win += qty
            else:
                sell_60s += qty
                if ts >= cutoff_win:
                    sell_win += qty

        total_win = buy_win + sell_win
        tfi = (buy_win - sell_win) / (total_win + 1e-9) if total_win > 0 else 0.0
        scale_factor = 60000.0 / max(window_ms, 1)
        avg_buy_win = buy_60s / scale_factor
        avg_sell_win = sell_60s / scale_factor
        return tfi, buy_win, sell_win, avg_buy_win, avg_sell_win

    def get_depletion_and_return(
        self, now_ms: int, lookback_ms: int
    ) -> Tuple[float, float, float]:
        """
        Возвращает (лог-доходность, коэффициент разъедания Ask DR_ask, коэффициент разъедания Bid DR_bid)
        """
        if not self.mid_history:
            return 0.0, 0.0, 0.0
        target_ts = now_ms - lookback_ms
        past_mid = self.mid_price
        past_ask_v = sum(v for _, v in self.sorted_asks[:3]) if self.sorted_asks else 0.0
        past_bid_v = sum(v for _, v in self.sorted_bids[:3]) if self.sorted_bids else 0.0

        for ts, m_p, a_v, b_v in reversed(self.mid_history):
            if ts <= target_ts:
                past_mid = m_p
                past_ask_v = a_v
                past_bid_v = b_v
                break

        log_ret = math.log(self.mid_price / past_mid) if past_mid > 0 else 0.0
        curr_ask_v = sum(v for _, v in self.sorted_asks[:3]) if self.sorted_asks else 0.0
        curr_bid_v = sum(v for _, v in self.sorted_bids[:3]) if self.sorted_bids else 0.0

        dr_ask = (past_ask_v - curr_ask_v) / max(past_ask_v, 1e-9) if past_ask_v > 0 else 0.0
        dr_bid = (past_bid_v - curr_bid_v) / max(past_bid_v, 1e-9) if past_bid_v > 0 else 0.0
        return log_ret, dr_ask, dr_bid

    def top3_book_notional_usdt(self, side: str = "Buy") -> float:
        """Суммарный объем в USDT топ-3 уровней стакана для лимита размера позиции."""
        levels = self.sorted_bids[:3] if side == "Buy" else self.sorted_asks[:3]
        return sum(p * v for p, v in levels)

    def reset_sweep(self, side: str = "both") -> None:
        """Сброс состояния свипа после отработки или экспирации."""
        if side in ("long", "both"):
            self.sweep_low_active = False
            self.sweep_min_price = 0.0
            self.sweep_start_ms = 0
            self.sweep_sell_vol = 0.0
        if side in ("short", "both"):
            self.sweep_high_active = False
            self.sweep_max_price = 0.0
            self.sweep_high_start_ms = 0
            self.sweep_buy_vol = 0.0
