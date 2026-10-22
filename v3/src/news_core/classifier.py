"""
Layer 1: Headline Classification, Entity Extraction, and Deduplication.
Classifies headlines via strict regex rules (without lookahead bias) into:
- CRASH_SHOCK (-3)
- MAJOR_CATALYST (+3)
- GEOPOLITICAL_SHOCK (-2)
- NEUTRAL (0)

Enforces 45-minute cooldown per (symbol, event_type) pair to eliminate multi-channel spam.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional, Set

try:
    from .models import EventType
    from .news_config import NewsConfig
except (ImportError, ValueError):
    from models import EventType
    from news_config import NewsConfig


class NewsClassifier:
    """Regex-based deterministic news classifier with cooldown and ticker extraction."""

    # Pre-compiled regex patterns for speed and zero-leakage
    _CRASH_PATTERNS = re.compile(
        r"\b("
        r"hack|hacked|hacker|exploit|exploited|vulnerability|drained|drain|"
        r"delist|delisting|delisted|removes? trading pairs?|"
        r"bankrupt|bankruptcy|chapter 11|insolvent|insolvency|illiquid|"
        r"depeg|depegged|lost peg|"
        r"sec sues|sec lawsuit|sec charges|charges filed|subpoena|doj charges|indictment|"
        r"rug pull|exit scam|smart contract bug|unauthorized withdrawal|compromised private key|"
        r"freeze deposits|trading suspended|halts? trading|withdrawals? halted|cease and desist"
        r")\b",
        re.IGNORECASE,
    )

    _MAJOR_CATALYST_PATTERNS = re.compile(
        r"\b("
        r"will list|lists|listing|listed on binance|listed on upbit|listed on coinbase|"
        r"launch(?:es)?\s+(?:\S+\s+){0,5}perpetual|will\s+launch\s+(?:\S+\s+){0,5}perpetual|"
        r"perpetual contract launch|"
        r"etf approval|etf approved|sec approves|"
        r"buyback|token burn|burning|mainnet launch|airdrop announcement|"
        r"major partnership|tier 1 listing|strategic investment by|"
        r"staking rewards live|governance approved"
        r")\b",
        re.IGNORECASE,
    )

    _GEOPOLITICAL_PATTERNS = re.compile(
        r"\b("
        r"missile|missiles|air strike|airstrike|war declared|invaded|invasion|"
        r"martial law|military strike|nuclear threat|strait of hormuz|retaliatory strike|"
        r"drone strike|state of emergency declared"
        r")\b",
        re.IGNORECASE,
    )

    # Common coin tickers for symbol extraction
    _COMMON_TICKERS = {
        "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC", "POL",
        "LINK", "NEAR", "UNI", "LTC", "APT", "ARB", "OP", "SUI", "TIA", "PEPE", "WIF",
        "BONK", "SHIB", "FET", "RENDER", "INJ", "RUNE", "SEI", "TAO", "AAVE", "KAS",
        "ICP", "FIL", "FTM", "ATOM", "STX", "ORDI", "PENDLE", "ONDO", "TON", "JUP",
        "RAY", "JTO", "ENA", "WLD", "ZRO", "MKR", "LDO", "CRV", "BLUR", "DYDX",
        "GLM", "WAVES", "OMG", "RDNT", "AXIS", "ZKF"
    }

    def __init__(self, config: Optional[NewsConfig] = None):
        self.config = config or NewsConfig()
        # Cooldown state: (symbol, event_type) -> datetime of last action
        self._last_event_time: Dict[Tuple[str, EventType], datetime] = {}

    def extract_symbol(self, headline: str, default: str = "BTCUSDT") -> str:
        """
        Extract primary ticker from headline (e.g. 'Binance Will List Dogwifhat (WIF)').
        Falls back to default if no coin identified.
        """
        if not headline:
            return default

        # 1. Search for ticker in parentheses: e.g. "Dogwifhat (WIF)" -> "WIF"
        paren_match = re.search(r"\(([A-Z0-9]{2,10})\)", headline)
        if paren_match:
            sym = paren_match.group(1).upper()
            if not sym.endswith("USDT"):
                return f"{sym}USDT"
            return sym

        # 2. Search for explicit USDT pairs: e.g. "SOLUSDT", "BTCUSDT"
        usdt_match = re.search(r"\b([A-Z0-9]{2,10})USDT\b", headline, re.IGNORECASE)
        if usdt_match:
            return usdt_match.group(1).upper() + "USDT"

        # 3. Search for cashtags: e.g. "$SOL", "$WIF"
        cashtag_match = re.search(r"\$([A-Z0-9]{2,10})\b", headline)
        if cashtag_match:
            sym = cashtag_match.group(1).upper()
            return f"{sym}USDT"

        # 4. Search for known crypto tickers
        words = re.findall(r"\b[A-Za-z0-9]+\b", headline)
        for w in words:
            w_upper = w.upper()
            if w_upper in self._COMMON_TICKERS:
                return f"{w_upper}USDT"

        return default

    def classify(
        self,
        headline: str,
        symbol: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        check_cooldown: bool = True,
    ) -> Tuple[EventType, int]:
        """
        Classify headline into (EventType, score).
        Applies min_score_to_act threshold and 45-minute deduplication cooldown.
        
        Returns:
            (event_type, score)
        """
        if not headline or not isinstance(headline, str):
            return EventType.NEUTRAL, 0

        now = timestamp or datetime.now(timezone.utc)
        sym = symbol or self.extract_symbol(headline)

        event_type = EventType.NEUTRAL
        score = 0

        # Layer 1 Regex Matching: Crash vs Major Catalyst vs Geopolitical
        if self._CRASH_PATTERNS.search(headline):
            event_type = EventType.CRASH_SHOCK
            score = -3
        elif self._MAJOR_CATALYST_PATTERNS.search(headline):
            event_type = EventType.MAJOR_CATALYST
            score = 3
        elif self._GEOPOLITICAL_PATTERNS.search(headline):
            event_type = EventType.GEOPOLITICAL_SHOCK
            score = -2
        else:
            return EventType.NEUTRAL, 0

        # Check threshold
        if abs(score) < self.config.min_score_to_act:
            return EventType.NEUTRAL, 0

        # Deduplication check: 45 min cooldown per (symbol, event_type)
        if check_cooldown:
            key = (sym, event_type)
            if key in self._last_event_time:
                last_time = self._last_event_time[key]
                elapsed_minutes = (now - last_time).total_seconds() / 60.0
                if elapsed_minutes < self.config.cooldown_minutes:
                    # Event is on cooldown! Suppress duplicate alert.
                    return EventType.NEUTRAL, 0

            # Register event in cooldown cache
            self._last_event_time[key] = now

        return event_type, score

    def reset_cooldowns(self) -> None:
        """Clear the cooldown cache (useful between backtest runs)."""
        self._last_event_time.clear()
