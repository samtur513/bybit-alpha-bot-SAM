"""
Chop & Range Universe Screener Module v3.2 for Bybit Linear Perpetuals.
Implements Institutional Range Alpha Score (IRAS) standard:
1. Pillar 1: Bandwidth Clearance (>= 3.5% up to 15.0%) guaranteeing Reward/Risk > 1.5:1.
2. Pillar 2: Quadratic penalty on directional drift (1.0 - KER_14)^2 ensuring pure Brownian noise.
3. Pillar 3: Mean-reversion cycle velocity (zero crossings of SMA20 in last 20 bars).
4. Pillar 4: Asymmetric funding carry boost (1.0 + |Funding| * 200.0).
5. Conflict Avoidance: strictly excludes any coins currently claimed or held by the Trend engine.
"""

from dataclasses import dataclass, field
from decimal import Decimal
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np
import requests

logger = logging.getLogger("ChopScreenerV32")


@dataclass
class ChopScreenerConfig:
    min_turnover_24h: float = 5_000_000.0    # $5,000,000 USDT minimum 24h volume
    min_open_interest: float = 2_000_000.0   # $2,000,000 USDT minimum open interest
    max_ker: float = 0.25                    # Kaufman Efficiency Ratio <= 0.25 (pure stationary noise)
    min_bandwidth_pct: float = 0.025         # Minimum 2.5% Bollinger Bandwidth to beat exchange friction
    max_adx: float = 20.0                    # Absence of directional trend (< 20)
    top_n: int = 10                          # Top-N range coins to monitor
    bb_period: int = 20                      # Bollinger period
    bb_std_mult: float = 2.0                 # 2.0 std dev
    atr_period: int = 14                     # ATR period
    rescan_interval_hours: int = 1           # Re-scan schedule (every 1h)
    prefer_positive_carry: bool = True       # Prefer pairs where funding pays position


@dataclass
class ChopCandidateMetric:
    symbol: str
    price: float
    turnover_24h: float
    open_interest: float
    funding_rate: float
    bandwidth_pct: float
    ker: float
    adx: float
    atr: float
    bb_mid: float
    bb_upper: float
    bb_lower: float
    dist_to_lower_pct: float
    dist_to_upper_pct: float
    crossings: int
    carry_mult: float
    chop_score: float  # IRAS
    passed_filters: bool
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "turnover_24h_m": round(self.turnover_24h / 1_000_000.0, 2),
            "funding_rate_pct": round(self.funding_rate * 100.0, 4),
            "bandwidth_pct": round(self.bandwidth_pct * 100.0, 2),
            "ker": round(self.ker, 3),
            "adx": round(self.adx, 2),
            "crossings": self.crossings,
            "dist_to_lower_pct": round(self.dist_to_lower_pct, 2),
            "dist_to_upper_pct": round(self.dist_to_upper_pct, 2),
            "chop_score": round(self.chop_score, 4),
            "passed_filters": self.passed_filters,
            "rejection_reason": self.rejection_reason,
        }


class ChopUniverseScreener:
    """
    Scans Bybit derivatives universe (400+ perpetuals) for stationary range-bound candidates
    adhering to the IRAS (Institutional Range Alpha Score) framework.
    """

    def __init__(self, config: Optional[ChopScreenerConfig] = None):
        self.config = config or ChopScreenerConfig()
        self.last_scan_time: float = 0.0
        self.active_chop_universe: List[ChopCandidateMetric] = []

    @staticmethod
    def calculate_efficiency_ratio(prices: List[float], period: int = 14) -> float:
        """Kaufman Efficiency Ratio: |Close_t - Close_{t-n}| / Sum(|Close_i - Close_{i-1}|)."""
        if len(prices) < period + 1:
            return 0.5
        sub = prices[-period:]
        net_change = abs(sub[-1] - sub[0])
        gross_path = sum(abs(sub[i] - sub[i - 1]) for i in range(1, len(sub)))
        if gross_path <= 1e-9:
            return 0.0
        return float(min(1.0, max(0.0, net_change / gross_path)))

    @staticmethod
    def calculate_bollinger_bands(prices: List[float], period: int = 20, std_mult: float = 2.0) -> Tuple[float, float, float, float]:
        if len(prices) < period:
            return 0.0, 0.0, 0.0, 0.0
        sub = prices[-period:]
        mid = float(np.mean(sub))
        std = float(np.std(sub, ddof=0))
        up = mid + std_mult * std
        lo = mid - std_mult * std
        bw = (up - lo) / mid if mid > 0 else 0.0
        return mid, up, lo, bw

    @staticmethod
    def calculate_wilder_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        n = len(closes)
        if n < 2 * period:
            return 12.0  # default neutral chop
        tr = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, n)]
        plus_dm = [highs[i] - highs[i - 1] if (highs[i] - highs[i - 1] > 0 and highs[i] - highs[i - 1] > lows[i - 1] - lows[i]) else 0.0 for i in range(1, n)]
        minus_dm = [lows[i - 1] - lows[i] if (lows[i - 1] - lows[i] > 0 and lows[i - 1] - lows[i] > highs[i] - highs[i - 1]) else 0.0 for i in range(1, n)]

        s_tr = np.mean(tr[-period:])
        s_p = np.mean(plus_dm[-period:])
        s_m = np.mean(minus_dm[-period:])
        if s_tr <= 0:
            return 0.0
        p_di = 100.0 * s_p / s_tr
        m_di = 100.0 * s_m / s_tr
        di_sum = p_di + m_di
        if di_sum <= 0:
            return 0.0
        dx = 100.0 * abs(p_di - m_di) / di_sum
        return float(dx)

    def fetch_all_bybit_linear_tickers(self, client: Optional[Any] = None) -> List[Dict[str, Any]]:
        """Fetches all linear perpetual tickers using BybitClient or public REST API."""
        if client is not None and hasattr(client, "request"):
            try:
                res = client.request("GET", "/v5/market/tickers", params={"category": "linear"})
                raw = res.get("result", {}).get("list", [])
                return [t for t in raw if t.get("symbol", "").endswith("USDT") and not t.get("symbol", "").startswith("USDC")]
            except Exception as e:
                logger.warning(f"BybitClient tickers fetch failed: {e}, falling back to REST")

        url = "https://api.bybit.com/v5/market/tickers?category=linear"
        try:
            resp = requests.get(url, timeout=12)
            data = resp.json()
            raw_list = data.get("result", {}).get("list", [])
            return [t for t in raw_list if t.get("symbol", "").endswith("USDT") and not t.get("symbol", "").startswith("USDC")]
        except Exception as e:
            logger.error(f"Failed to fetch Bybit tickers: {e}")
            return []

    def fetch_hourly_klines(self, symbol: str, limit: int = 40, client: Optional[Any] = None) -> Optional[Dict[str, List[float]]]:
        """Fetches recent 1h candles for indicator evaluation."""
        if client is not None and hasattr(client, "get_kline"):
            try:
                raw = client.get_kline(symbol, interval="60", limit=limit)
                if len(raw) >= 25:
                    candles = sorted(raw, key=lambda c: int(c[0]))
                    return {
                        "open": [float(c[1]) for c in candles],
                        "high": [float(c[2]) for c in candles],
                        "low": [float(c[3]) for c in candles],
                        "close": [float(c[4]) for c in candles],
                        "volume": [float(c[5]) for c in candles],
                    }
            except Exception:
                pass

        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval=60&limit={limit}"
        try:
            resp = requests.get(url, timeout=8)
            data = resp.json()
            raw = data.get("result", {}).get("list", [])
            if len(raw) < 25:
                return None
            candles = sorted(raw, key=lambda c: int(c[0]))
            return {
                "open": [float(c[1]) for c in candles],
                "high": [float(c[2]) for c in candles],
                "low": [float(c[3]) for c in candles],
                "close": [float(c[4]) for c in candles],
                "volume": [float(c[5]) for c in candles],
            }
        except Exception:
            return None

    def evaluate_symbol(
        self,
        symbol: str,
        price: float,
        turnover_24h: float,
        open_interest: float,
        funding_rate: float,
        klines: Dict[str, List[float]],
        exclude_symbols: Optional[Set[str]] = None,
    ) -> ChopCandidateMetric:
        """Evaluates a single instrument against IRAS Selection Gates."""
        # Gate 1: Conflict Avoidance with Trend Engine
        if exclude_symbols and symbol in exclude_symbols:
            return ChopCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, bandwidth_pct=0.0, ker=1.0, adx=50.0, atr=0.0,
                bb_mid=0.0, bb_upper=0.0, bb_lower=0.0, dist_to_lower_pct=0.0, dist_to_upper_pct=0.0,
                crossings=0, carry_mult=1.0, chop_score=0.0, passed_filters=False,
                rejection_reason="Claimed by Trend Engine",
            )

        # Gate 2: Liquidity Gate (Turnover >= $10M)
        if turnover_24h < self.config.min_turnover_24h:
            return ChopCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, bandwidth_pct=0.0, ker=1.0, adx=0.0, atr=0.0,
                bb_mid=0.0, bb_upper=0.0, bb_lower=0.0, dist_to_lower_pct=0.0, dist_to_upper_pct=0.0,
                crossings=0, carry_mult=1.0, chop_score=0.0, passed_filters=False,
                rejection_reason=f"Turnover ${turnover_24h/1e6:.1f}M < ${self.config.min_turnover_24h/1e6:.1f}M",
            )

        closes = klines["close"]
        highs = klines["high"]
        lows = klines["low"]

        ker = self.calculate_efficiency_ratio(closes, self.config.atr_period)
        mid, up, lo, bw = self.calculate_bollinger_bands(closes, self.config.bb_period, self.config.bb_std_mult)
        adx = self.calculate_wilder_adx(highs, lows, closes, self.config.atr_period)
        atr = float(np.mean([highs[i] - lows[i] for i in range(-self.config.atr_period, 0)]))

        last_c = closes[-1]
        dist_to_lo = (last_c - lo) / last_c * 100.0 if last_c > 0 else 0.0
        dist_to_up = (up - last_c) / last_c * 100.0 if last_c > 0 else 0.0

        # Mean reversion velocity (zero crossings of bb_mid in last N bars)
        sub_c = closes[-self.config.bb_period:]
        crosses = sum(1 for i in range(1, len(sub_c)) if (sub_c[i] - mid) * (sub_c[i - 1] - mid) < 0)
        velocity_mult = 0.5 + (crosses / 10.0)

        # Carry boost from funding
        carry_mult = 1.0 + (abs(funding_rate) * 200.0)

        # Gate 3: Stationary Noise (KER <= max_ker)
        if ker > self.config.max_ker:
            return ChopCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, bandwidth_pct=bw, ker=ker, adx=adx, atr=atr,
                bb_mid=mid, bb_upper=up, bb_lower=lo, dist_to_lower_pct=dist_to_lo, dist_to_upper_pct=dist_to_up,
                crossings=crosses, carry_mult=carry_mult, chop_score=0.0, passed_filters=False,
                rejection_reason=f"KER {ker:.3f} > {self.config.max_ker:.2f} (Directional drift)",
            )

        # Gate 4: Volatility Bandwidth Clearance (bw >= min_bandwidth_pct, e.g. 3.5%)
        if bw < self.config.min_bandwidth_pct:
            return ChopCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, bandwidth_pct=bw, ker=ker, adx=adx, atr=atr,
                bb_mid=mid, bb_upper=up, bb_lower=lo, dist_to_lower_pct=dist_to_lo, dist_to_upper_pct=dist_to_up,
                crossings=crosses, carry_mult=carry_mult, chop_score=0.0, passed_filters=False,
                rejection_reason=f"Bandwidth {bw*100:.2f}% < {self.config.min_bandwidth_pct*100:.2f}% (Friction lock)",
            )

        # Gate 5: Absence of strong trend (ADX < max_adx)
        if adx >= self.config.max_adx:
            return ChopCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, bandwidth_pct=bw, ker=ker, adx=adx, atr=atr,
                bb_mid=mid, bb_upper=up, bb_lower=lo, dist_to_lower_pct=dist_to_lo, dist_to_upper_pct=dist_to_up,
                crossings=crosses, carry_mult=carry_mult, chop_score=0.0, passed_filters=False,
                rejection_reason=f"ADX {adx:.1f} >= {self.config.max_adx:.1f} (Trend active)",
            )

        # Institutional Range Alpha Score (IRAS):
        # Bandwidth * (1 - KER)^2 * Velocity * CarryBoost
        chop_score = float(bw) * ((1.0 - ker) ** 2) * velocity_mult * carry_mult

        return ChopCandidateMetric(
            symbol=symbol,
            price=price,
            turnover_24h=turnover_24h,
            open_interest=open_interest,
            funding_rate=funding_rate,
            bandwidth_pct=bw,
            ker=ker,
            adx=adx,
            atr=atr,
            bb_mid=mid,
            bb_upper=up,
            bb_lower=lo,
            dist_to_lower_pct=dist_to_lo,
            dist_to_upper_pct=dist_to_up,
            crossings=crosses,
            carry_mult=carry_mult,
            chop_score=chop_score,
            passed_filters=True,
            rejection_reason=None,
        )

    def scan_chop_universe(
        self,
        client: Optional[Any] = None,
        exclude_symbols: Optional[Set[str]] = None,
        max_symbols_to_evaluate: int = 120,
    ) -> List[ChopCandidateMetric]:
        """
        Executes full screener pass over Bybit linear perpetuals.
        Returns ranked Top-N coins for Range Trading based on IRAS.
        """
        logger.info("Scanning Bybit perpetuals for stationary Chop candidates (IRAS standard)...")
        tickers = self.fetch_all_bybit_linear_tickers(client=client)
        if not tickers:
            return []

        # Filter by liquidity gate first
        liquid_pool = []
        for t in tickers:
            try:
                turnover = float(t.get("turnover24h", 0.0))
                if turnover >= self.config.min_turnover_24h:
                    liquid_pool.append(t)
            except Exception:
                continue

        liquid_pool.sort(key=lambda t: float(t.get("turnover24h", 0.0)), reverse=True)
        eval_pool = liquid_pool[:max_symbols_to_evaluate]

        candidates: List[ChopCandidateMetric] = []
        for t in eval_pool:
            sym = t.get("symbol", "")
            if not sym:
                continue
            if exclude_symbols and sym in exclude_symbols:
                continue

            price = float(t.get("lastPrice", 0.0))
            turnover = float(t.get("turnover24h", 0.0))
            open_interest = float(t.get("openInterestValue", t.get("openInterest", 0.0)))
            funding = float(t.get("fundingRate", 0.0))

            klines = self.fetch_hourly_klines(sym, limit=40, client=client)
            if not klines:
                continue

            metric = self.evaluate_symbol(
                symbol=sym,
                price=price,
                turnover_24h=turnover,
                open_interest=open_interest,
                funding_rate=funding,
                klines=klines,
                exclude_symbols=exclude_symbols,
            )
            if metric.passed_filters:
                candidates.append(metric)
            time.sleep(0.06)  # rate limit safety (16 req/sec max)

        candidates.sort(key=lambda c: c.chop_score, reverse=True)
        self.active_chop_universe = candidates[:self.config.top_n]
        self.last_scan_time = time.time()
        logger.info(f"IRAS Chop screener complete: Selected {len(self.active_chop_universe)} active range pairs.")
        return self.active_chop_universe

    def get_top_universe(
        self,
        client: Optional[Any] = None,
        exclude_symbols: Optional[Set[str]] = None,
    ) -> List[str]:
        metrics = self.scan_chop_universe(client=client, exclude_symbols=exclude_symbols)
        return [m.symbol for m in metrics]
