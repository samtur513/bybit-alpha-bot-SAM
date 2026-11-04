"""
v3.7 Scalp Universe Screener:
Отбор ликвидных инструментов Standard Crypto (исключение Innovation Zone),
расчет Беты и корреляции к BTCUSDT, показателя Херста, Kaufman ER и уровней свинг-ликвидности.
Использует стандартную библиотеку urllib.request (zero external dependency).
"""

import asyncio
import json
import logging
import urllib.request
from typing import Dict, Optional, Tuple

import numpy as np

try:
    import orjson
except ImportError:
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

from v3_7.src.config import ScalperConfig
from v3_7.src.microstructure import InstrumentMeta

logger = logging.getLogger("ScalpUniverseScreener")

NON_CRYPTO_SYMBOLS = {
    "CLUSDT", "XAUUSDT", "XAGUSDT", "SOXLUSDT", "SOXSUSDT", "SPCXUSDT", "MSTRUSDT",
    "SKHYNIXUSDT", "SNDKUSDT", "MUUSDT", "KORUUSDT", "NVDAUSDT", "TSLAUSDT", "AAPLUSDT",
    "AMZNUSDT", "GOOGLUSDT", "METAUSDT", "MSFTUSDT", "PLTRUSDT", "COINUSDT", "INTCUSDT",
    "SKHYUSDT", "AMDUSDT", "BABAUSDT", "BIDUUSDT", "PDDUSDT", "TCEHYUSDT"
}


def _http_get_json(url: str, timeout: float = 10.0) -> dict:
    """Безопасный HTTP GET запрос через стандартную библиотеку urllib."""
    req = urllib.request.Request(url, headers={"User-Agent": "BybitScalper/3.7"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return orjson.loads(resp.read())


class UniverseScreener:
    def __init__(self, cfg: ScalperConfig):
        self.cfg = cfg

    @staticmethod
    def _calc_hurst(closes: np.ndarray) -> float:
        if len(closes) < 40:
            return 0.5
        lags = range(2, 20)
        tau = [np.std(np.subtract(closes[lag:], closes[:-lag])) for lag in lags]
        poly = np.polyfit(np.log(list(lags)), np.log(tau), 1)
        return float(poly[0])

    def _build_active_universe_sync(self) -> Dict[str, InstrumentMeta]:
        metas: Dict[str, InstrumentMeta] = {}

        try:
            # 1. Спецификации инструментов
            info_url = f"{self.cfg.rest_base_url}/v5/market/instruments-info?category=linear&limit=1000"
            info_data = _http_get_json(info_url)
            spec_map: Dict[str, Tuple[float, float, float]] = {}
            for item in info_data.get("result", {}).get("list", []):
                if item.get("status") != "Trading" or item.get("quoteCoin") != "USDT":
                    continue
                if item.get("contractType") != "LinearPerpetual":
                    continue
                sym = item["symbol"]
                tick = float(item["priceFilter"]["tickSize"])
                q_step = float(item["lotSizeFilter"]["qtyStep"])
                min_q = float(item["lotSizeFilter"]["minOrderQty"])
                spec_map[sym] = (tick, q_step, min_q)

            # 2. Суточный оборот тикеров
            tick_url = f"{self.cfg.rest_base_url}/v5/market/tickers?category=linear"
            tick_data = _http_get_json(tick_url)
            candidates = []
            for t in tick_data.get("result", {}).get("list", []):
                sym = t["symbol"]
                if sym in NON_CRYPTO_SYMBOLS or sym not in spec_map:
                    continue
                turnover = float(t.get("turnover24h", 0.0))
                last_p = float(t.get("lastPrice", 0.0))
                if turnover >= 35_000_000 and last_p > 0:
                    tick_sz = spec_map[sym][0]
                    rel_tick = tick_sz / last_p
                    if 0.00004 <= rel_tick <= 0.00100 or sym == self.cfg.leader_symbol:
                        candidates.append((sym, turnover))

            candidates.sort(key=lambda x: x[1], reverse=True)
            selected_syms = [self.cfg.leader_symbol] + [
                s for s, _ in candidates if s != self.cfg.leader_symbol
            ][: self.cfg.max_active_symbols * 2]

            # 3. Загрузка 5-минутных свечей для расчета индикаторов и Беты
            btc_rets: Optional[np.ndarray] = None
            for sym in selected_syms:
                if len(metas) >= self.cfg.max_active_symbols + 1:
                    break
                url = f"{self.cfg.rest_base_url}/v5/market/kline?category=linear&symbol={sym}&interval=5&limit=120"
                try:
                    k_json = _http_get_json(url)
                    rows = k_json.get("result", {}).get("list", [])
                    if len(rows) < 60:
                        continue
                    rows.reverse()
                    highs = np.array([float(r[2]) for r in rows])
                    lows = np.array([float(r[3]) for r in rows])
                    closes = np.array([float(r[4]) for r in rows])
                    vols = np.array([float(r[5]) for r in rows])

                    log_rets = np.diff(np.log(closes))
                    if sym == self.cfg.leader_symbol:
                        btc_rets = log_rets

                    tr = np.maximum(
                        highs[1:] - lows[1:],
                        np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
                    )
                    atr_5m = float(np.mean(tr[-14:]))
                    natr_5m = atr_5m / closes[-1]

                    if sym != self.cfg.leader_symbol and natr_5m < 0.0035:
                        continue

                    corr_btc, beta_btc = 1.0, 1.0
                    if btc_rets is not None and sym != self.cfg.leader_symbol and len(log_rets) == len(btc_rets):
                        cov_m = np.cov(log_rets, btc_rets)
                        var_btc = cov_m[1, 1]
                        if var_btc > 0:
                            beta_btc = float(cov_m[0, 1] / var_btc)
                            corr_btc = float(cov_m[0, 1] / (np.std(log_rets) * np.std(btc_rets) + 1e-12))

                    net_change = abs(closes[-1] - closes[-18])
                    sum_changes = float(np.sum(np.abs(np.diff(closes[-18:]))))
                    er_15m = net_change / max(sum_changes, 1e-9)

                    tick_sz, q_step, min_q = spec_map[sym]
                    metas[sym] = InstrumentMeta(
                        symbol=sym,
                        tick_size=tick_sz,
                        qty_step=q_step,
                        min_qty=min_q,
                        natr_5m=natr_5m,
                        atr_5m=atr_5m,
                        atr_15m=atr_5m * 1.75,
                        hurst_15m=self._calc_hurst(closes),
                        er_15m=er_15m,
                        ema9_5m=float(np.mean(closes[-9:])),
                        ema21_5m=float(np.mean(closes[-21:])),
                        ema20_15m=float(np.mean(closes[-60:])),
                        ema50_4h=float(np.mean(closes)),
                        vwap_session=float(np.sum(closes[-48:] * vols[-48:]) / max(np.sum(vols[-48:]), 1e-9)),
                        swing_low_1h=float(np.min(lows[-36:-2])),
                        swing_high_1h=float(np.max(highs[-36:-2])),
                        beta_btc=beta_btc,
                        corr_btc=corr_btc,
                    )
                except Exception as ex:
                    logger.warning(f"Ошибка загрузки свечей для {sym}: {ex}")
                    continue

        except Exception as e:
            logger.error(f"Сбой скринера: {e}")

        return metas

    async def build_active_universe(self, session=None) -> Dict[str, InstrumentMeta]:
        """Асинхронная обертка для построения пула монет без блокировки event loop."""
        return await asyncio.to_thread(self._build_active_universe_sync)

    def _refresh_instrument_metrics_sync(self, metas: Dict[str, InstrumentMeta]) -> None:
        """Обновляет индикаторы, свинг-уровни и бету активных инструментов без перезапуска стрима."""
        btc_rets: Optional[np.ndarray] = None
        # Сначала BTC для беты
        if self.cfg.leader_symbol in metas:
            url_btc = f"{self.cfg.rest_base_url}/v5/market/kline?category=linear&symbol={self.cfg.leader_symbol}&interval=5&limit=120"
            try:
                k_json = _http_get_json(url_btc)
                rows = k_json.get("result", {}).get("list", [])
                if len(rows) >= 60:
                    rows.reverse()
                    closes = np.array([float(r[4]) for r in rows])
                    btc_rets = np.diff(np.log(closes))
                    m = metas[self.cfg.leader_symbol]
                    highs = np.array([float(r[2]) for r in rows])
                    lows = np.array([float(r[3]) for r in rows])
                    vols = np.array([float(r[5]) for r in rows])
                    m.swing_low_1h = float(np.min(lows[-36:-2]))
                    m.swing_high_1h = float(np.max(highs[-36:-2]))
                    m.vwap_session = float(np.sum(closes[-48:] * vols[-48:]) / max(np.sum(vols[-48:]), 1e-9))
                    m.ema20_15m = float(np.mean(closes[-60:]))
            except Exception as e:
                logger.debug(f"Refresh BTC kline error: {e}")

        for sym, m in metas.items():
            if sym == self.cfg.leader_symbol:
                continue
            url = f"{self.cfg.rest_base_url}/v5/market/kline?category=linear&symbol={sym}&interval=5&limit=120"
            try:
                k_json = _http_get_json(url)
                rows = k_json.get("result", {}).get("list", [])
                if len(rows) < 60:
                    continue
                rows.reverse()
                highs = np.array([float(r[2]) for r in rows])
                lows = np.array([float(r[3]) for r in rows])
                closes = np.array([float(r[4]) for r in rows])
                vols = np.array([float(r[5]) for r in rows])
                log_rets = np.diff(np.log(closes))

                tr = np.maximum(
                    highs[1:] - lows[1:],
                    np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
                )
                atr_5m = float(np.mean(tr[-14:]))
                m.atr_5m = atr_5m
                m.atr_15m = atr_5m * 1.75
                m.natr_5m = atr_5m / closes[-1]
                m.hurst_15m = self._calc_hurst(closes)

                net_change = abs(closes[-1] - closes[-18])
                sum_changes = float(np.sum(np.abs(np.diff(closes[-18:]))))
                m.er_15m = net_change / max(sum_changes, 1e-9)

                m.ema9_5m = float(np.mean(closes[-9:]))
                m.ema21_5m = float(np.mean(closes[-21:]))
                m.ema20_15m = float(np.mean(closes[-60:]))
                m.ema50_4h = float(np.mean(closes))
                m.vwap_session = float(np.sum(closes[-48:] * vols[-48:]) / max(np.sum(vols[-48:]), 1e-9))
                m.swing_low_1h = float(np.min(lows[-36:-2]))
                m.swing_high_1h = float(np.max(highs[-36:-2]))

                if btc_rets is not None and len(log_rets) == len(btc_rets):
                    cov_m = np.cov(log_rets, btc_rets)
                    var_btc = cov_m[1, 1]
                    if var_btc > 0:
                        m.beta_btc = float(cov_m[0, 1] / var_btc)
                        m.corr_btc = float(cov_m[0, 1] / (np.std(log_rets) * np.std(btc_rets) + 1e-12))
            except Exception as e:
                logger.debug(f"Refresh kline error for {sym}: {e}")

    async def refresh_instrument_metrics(self, metas: Dict[str, InstrumentMeta]) -> None:
        """Асинхронное обновление индикаторов и уровней без блокировки event loop."""
        await asyncio.to_thread(self._refresh_instrument_metrics_sync, metas)
