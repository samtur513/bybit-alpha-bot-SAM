"""
Dynamic Universe Screener Module for Multi-Alpha Growth Engine v3.
Implements institutional 4-pillar universe selection:
1. Liquidity & Execution Filter (24h Turnover >= $25M, Open Interest >= $5M).
2. Volatility Filter (Normalized ATR >= 2.0%).
3. Trend Quality & Cleanliness (Kaufman Efficiency Ratio >= 0.30 or ADX >= 16.0).
4. Relative Strength vs BTC Benchmark.
5. Funding Rate Hygiene (|rate| <= 0.05% / 8h).

Computes Multi-Factor Composite Momentum Score and maintains a smooth-rotating Top-N universe.
"""

from dataclasses import dataclass, field
from decimal import Decimal
import logging
from typing import Dict, List, Optional, Set, Tuple, Any
import numpy as np

logger = logging.getLogger("UniverseScreener")


@dataclass
class ScreenerConfig:
    min_turnover_24h: float = 25_000_000.0   # $25,000,000 USDT minimum 24h volume
    min_open_interest: float = 5_000_000.0   # $5,000,000 USDT minimum open interest
    max_funding_abs: float = 0.0005          # Max |funding| 0.05% per 8h
    min_norm_atr: float = 0.020              # 2.0% minimum normalized ATR (ATR / Price)
    min_adx: float = 16.0                    # Minimum ADX to confirm directional trending
    min_efficiency_ratio: float = 0.30       # Minimum Kaufman Efficiency Ratio
    top_n: int = 25                          # Number of coins in active trading universe
    benchmark_symbol: str = "BTCUSDT"        # Benchmark asset for relative strength

    # Factor weights for composite score
    weight_trend: float = 0.35
    weight_relative_strength: float = 0.30
    weight_volume_expansion: float = 0.20
    weight_volatility: float = 0.15


@dataclass
class CoinCandidateMetric:
    symbol: str
    price: float
    turnover_24h: float
    open_interest: float
    funding_rate: float
    norm_atr: float
    adx: float
    efficiency_ratio: float
    relative_strength: float
    volume_ratio: float
    composite_score: float
    passed_filters: bool
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "turnover_24h": self.turnover_24h,
            "open_interest": self.open_interest,
            "funding_rate": self.funding_rate,
            "norm_atr": self.norm_atr,
            "adx": self.adx,
            "efficiency_ratio": self.efficiency_ratio,
            "relative_strength": self.relative_strength,
            "volume_ratio": self.volume_ratio,
            "composite_score": self.composite_score,
            "passed_filters": self.passed_filters,
            "rejection_reason": self.rejection_reason,
        }


ScreenedAsset = CoinCandidateMetric


class UniverseScreener:
    """
    Scans derivatives universe, applies strict institutional risk/liquidity gates,
    and returns ranked Top-N coins for high-expectancy trend breakout trading.
    """

    def __init__(self, config: Optional[ScreenerConfig] = None):
        self.config = config or ScreenerConfig()

    @staticmethod
    def calculate_efficiency_ratio(prices: List[float]) -> float:
        """
        Kaufman Efficiency Ratio (KER):
        Ratio of net directional movement to gross path length.
        ER = |P_t - P_0| / Sum(|P_i - P_{i-1}|)
        Ranges from 0.0 (pure chop/noise) to 1.0 (pure straight line trend).
        """
        if len(prices) < 3:
            return 0.5
        net_change = abs(prices[-1] - prices[0])
        gross_path = sum(abs(prices[i] - prices[i - 1]) for i in range(1, len(prices)))
        if gross_path <= 1e-9:
            return 0.0
        return float(min(1.0, max(0.0, net_change / gross_path)))

    def evaluate_symbol(
        self,
        symbol: str,
        price: float,
        turnover_24h: float,
        open_interest: float,
        funding_rate: float,
        atr: float,
        adx: float,
        recent_closes: List[float],
        btc_return_24h: float = 0.0,
        coin_return_24h: float = 0.0,
        volume_24h: float = 0.0,
        volume_sma_20d: float = 0.0,
    ) -> CoinCandidateMetric:
        """Evaluates a single instrument against the 4-pillar quantitative framework."""
        if price <= 0:
            return CoinCandidateMetric(
                symbol=symbol, price=0.0, turnover_24h=0.0, open_interest=0.0,
                funding_rate=0.0, norm_atr=0.0, adx=0.0, efficiency_ratio=0.0,
                relative_strength=0.0, volume_ratio=0.0, composite_score=0.0,
                passed_filters=False, rejection_reason="Non-positive price"
            )

        norm_atr = (atr / price) if price > 0 else 0.0
        ker = self.calculate_efficiency_ratio(recent_closes) if recent_closes else 0.5
        rs = coin_return_24h - btc_return_24h
        vol_ratio = (volume_24h / volume_sma_20d) if volume_sma_20d > 0 else 1.0

        # Filter 1: Liquidity & Execution
        if turnover_24h < self.config.min_turnover_24h:
            return CoinCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, norm_atr=norm_atr, adx=adx, efficiency_ratio=ker,
                relative_strength=rs, volume_ratio=vol_ratio, composite_score=0.0,
                passed_filters=False, rejection_reason=f"Turnover ${turnover_24h/1e6:.1f}M < ${self.config.min_turnover_24h/1e6:.1f}M"
            )

        if open_interest > 0 and open_interest < self.config.min_open_interest:
            return CoinCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, norm_atr=norm_atr, adx=adx, efficiency_ratio=ker,
                relative_strength=rs, volume_ratio=vol_ratio, composite_score=0.0,
                passed_filters=False, rejection_reason=f"OI ${open_interest/1e6:.1f}M < ${self.config.min_open_interest/1e6:.1f}M"
            )

        # Filter 2: Funding Rate Sanitization
        if abs(funding_rate) > self.config.max_funding_abs:
            return CoinCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, norm_atr=norm_atr, adx=adx, efficiency_ratio=ker,
                relative_strength=rs, volume_ratio=vol_ratio, composite_score=0.0,
                passed_filters=False, rejection_reason=f"Funding {funding_rate*100:.3f}% > {self.config.max_funding_abs*100:.3f}%"
            )

        # Filter 3: Minimum Volatility
        if norm_atr < self.config.min_norm_atr:
            return CoinCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, norm_atr=norm_atr, adx=adx, efficiency_ratio=ker,
                relative_strength=rs, volume_ratio=vol_ratio, composite_score=0.0,
                passed_filters=False, rejection_reason=f"NormATR {norm_atr*100:.2f}% < {self.config.min_norm_atr*100:.2f}%"
            )

        # Filter 4: Trend Quality Gate
        if adx < self.config.min_adx and ker < self.config.min_efficiency_ratio:
            return CoinCandidateMetric(
                symbol=symbol, price=price, turnover_24h=turnover_24h, open_interest=open_interest,
                funding_rate=funding_rate, norm_atr=norm_atr, adx=adx, efficiency_ratio=ker,
                relative_strength=rs, volume_ratio=vol_ratio, composite_score=0.0,
                passed_filters=False, rejection_reason=f"Low trend strength (ADX={adx:.1f}, ER={ker:.2f})"
            )

        # Composite Scoring
        # Normalized score components [0.0 to 1.0 approx]
        score_trend = min(1.0, max(0.0, adx / 50.0))
        score_rs = min(1.0, max(0.0, (rs + 0.15) / 0.30))  # maps -15% to +15% RS into 0..1
        score_vol = min(1.0, max(0.0, vol_ratio / 3.0))     # maps 0..3x volume ratio
        score_atr = min(1.0, max(0.0, norm_atr / 0.08))     # maps 0..8% normalized ATR

        composite = (
            self.config.weight_trend * score_trend
            + self.config.weight_relative_strength * score_rs
            + self.config.weight_volume_expansion * score_vol
            + self.config.weight_volatility * score_atr
        )

        return CoinCandidateMetric(
            symbol=symbol,
            price=price,
            turnover_24h=turnover_24h,
            open_interest=open_interest,
            funding_rate=funding_rate,
            norm_atr=norm_atr,
            adx=adx,
            efficiency_ratio=ker,
            relative_strength=rs,
            volume_ratio=vol_ratio,
            composite_score=composite,
            passed_filters=True,
            rejection_reason=None,
        )

    def screen_bars(
        self,
        bars_dict: Dict[str, Dict[str, Any]],
        recent_history: Optional[Dict[str, List[float]]] = None,
        turnovers: Optional[Dict[str, float]] = None,
        open_interests: Optional[Dict[str, float]] = None,
        funding_rates: Optional[Dict[str, float]] = None,
    ) -> List[CoinCandidateMetric]:
        """
        Screens candidates from bar event dictionaries (used in 4h loop & backtests).
        """
        btc_bar = bars_dict.get(self.config.benchmark_symbol)
        btc_ret = 0.0
        if btc_bar:
            btc_open = float(btc_bar.get("open", btc_bar["close"]))
            btc_close = float(btc_bar["close"])
            if btc_open > 0:
                btc_ret = (btc_close - btc_open) / btc_open

        candidates = []
        for sym, b in bars_dict.items():
            price = float(b["close"])
            open_p = float(b.get("open", price))
            ret_coin = ((price - open_p) / open_p) if open_p > 0 else 0.0

            atr = float(b.get("atr", price * 0.03))
            adx = float(b.get("adx", 20.0))
            vol = float(b.get("volume", 1000.0))
            vol_sma = float(b.get("volume_sma", vol))

            # Default to sufficient turnover if not provided (e.g. liquid backtest pool)
            turnover = turnovers.get(sym, price * vol) if turnovers else (price * vol)
            oi = open_interests.get(sym, self.config.min_open_interest * 1.5) if open_interests else (self.config.min_open_interest * 1.5)
            funding = funding_rates.get(sym, 0.0001) if funding_rates else 0.0001
            history = recent_history.get(sym, [open_p, price]) if recent_history else [open_p, price]

            cand = self.evaluate_symbol(
                symbol=sym,
                price=price,
                turnover_24h=turnover,
                open_interest=oi,
                funding_rate=funding,
                atr=atr,
                adx=adx,
                recent_closes=history,
                btc_return_24h=btc_ret,
                coin_return_24h=ret_coin,
                volume_24h=vol,
                volume_sma_20d=vol_sma,
            )
            candidates.append(cand)

        # Sort descending by composite score
        candidates.sort(key=lambda c: (c.passed_filters, c.composite_score), reverse=True)
        return candidates

    def screen_universe(
        self,
        client: Optional[Any] = None,
        tickers_list: Optional[List[Dict[str, Any]]] = None,
    ) -> List[CoinCandidateMetric]:
        """
        Fetches live 24h market tickers from Bybit REST API, applies 4-pillar gates,
        and returns ranked candidate metrics.
        """
        if tickers_list is None:
            if client is not None and hasattr(client, "request"):
                try:
                    res = client.request("GET", "/v5/market/tickers", params={"category": "linear"})
                    tickers_list = res.get("result", {}).get("list", [])
                except Exception as e:
                    logger.error(f"Failed to fetch tickers for universe screening: {e}")
                    tickers_list = []
            else:
                tickers_list = []

        if not tickers_list:
            return []

        # Find BTC return benchmark
        btc_ret = 0.0
        for item in tickers_list:
            if item.get("symbol") == self.config.benchmark_symbol:
                try:
                    btc_ret = float(item.get("price24hPcnt", 0.0))
                except Exception:
                    btc_ret = 0.0
                break

        candidates = []
        for item in tickers_list:
            sym = item.get("symbol", "")
            # Only trade USDT linear perpetuals; exclude USDC-settled or TradFi pre-IPO
            if not sym.endswith("USDT") or sym.startswith("USDC") or "STOCK" in sym:
                continue

            try:
                price = float(item.get("lastPrice", 0.0))
                if price <= 0:
                    continue
                turnover = float(item.get("turnover24h", 0.0))
                oi = float(item.get("openInterestValue", item.get("openInterest", 0.0)))
                funding = float(item.get("fundingRate", 0.0))
                ret_24h = float(item.get("price24hPcnt", 0.0))
                high_24h = float(item.get("highPrice24h", price))
                low_24h = float(item.get("lowPrice24h", price))

                # Normalized ATR proxy from 24h high/low range
                norm_atr = ((high_24h - low_24h) / price) if price > 0 else 0.0
                atr = high_24h - low_24h

                # Efficiency proxy: directional move vs total range
                ker = min(1.0, max(0.0, abs(ret_24h) / (norm_atr + 1e-6)))

                # Trend strength proxy
                adx = 20.0 + (abs(ret_24h) * 100.0)

                cand = self.evaluate_symbol(
                    symbol=sym,
                    price=price,
                    turnover_24h=turnover,
                    open_interest=oi,
                    funding_rate=funding,
                    atr=atr,
                    adx=adx,
                    recent_closes=[low_24h, (high_24h + low_24h) / 2.0, price],
                    btc_return_24h=btc_ret,
                    coin_return_24h=ret_24h,
                    volume_24h=float(item.get("volume24h", 0.0)),
                    volume_sma_20d=float(item.get("volume24h", 1.0)),
                )
                candidates.append(cand)
            except Exception:
                continue

        candidates.sort(key=lambda c: (c.passed_filters, c.composite_score), reverse=True)
        return candidates

    def get_top_universe(
        self,
        candidates: Optional[List[CoinCandidateMetric]] = None,
        top_n: Optional[int] = None,
        keep_active_symbols: Optional[Any] = None,
        client: Optional[Any] = None,
    ) -> List[str]:
        """
        Extracts Top-N symbol strings.
        Can be called with precomputed candidates OR with client to fetch and screen live!
        Always preserves any symbol currently held in an open position.
        """
        if candidates is None:
            if client is not None:
                candidates = self.screen_universe(client=client)
            else:
                candidates = []

        n = top_n or self.config.top_n
        passed = [c.symbol for c in candidates if c.passed_filters]
        top_symbols = passed[:n]

        # Always include active positions so they continue receiving trailing stop updates
        if keep_active_symbols:
            for s in keep_active_symbols:
                if s not in top_symbols:
                    top_symbols.append(s)

        # Always ensure benchmark symbol (BTCUSDT) is tracked for market filter
        if self.config.benchmark_symbol not in top_symbols:
            top_symbols.append(self.config.benchmark_symbol)

        return sorted(list(set(top_symbols)))
