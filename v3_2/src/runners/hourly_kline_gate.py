"""Rate-safe 1h kline scheduling for isolated Native and Shadow paper runners.

This module does not make HTTP calls, compute signals, or modify persistence.
Only the caller decides what to do with received *closed* candles.

The clock is Bybit server time in milliseconds, not local wall time.
A per-symbol retry deadline avoids hammering unavailable Bybit data. The
retry deadline is advisory: process restarts can always resume from the
authoritative persisted last_processed_bar_ts state.
"""

from typing import Dict, Iterable, List


class HourlyKlineGate:
    """Gate repeated hourly candle fetches while live quotes continue normally."""

    def __init__(
        self,
        interval_ms: int = 3_600_000,
        after_close_delay_ms: int = 10_000,
        retry_delay_ms: int = 60_000,
    ):
        if interval_ms <= 0 or not 0 <= after_close_delay_ms < interval_ms:
            raise ValueError("invalid interval or after-close delay")
        if retry_delay_ms <= 0:
            raise ValueError("retry delay must be positive")
        self.interval_ms = int(interval_ms)
        self.after_close_delay_ms = int(after_close_delay_ms)
        self.retry_delay_ms = int(retry_delay_ms)
        self.next_attempt_ms: Dict[str, int] = {}

    def due_symbols(
        self,
        symbols: Iterable[str],
        last_processed_open_ms: Dict[str, int],
        bybit_now_ms: int,
    ) -> List[str]:
        """Return symbols missing at least one closed bar, in original order.

        Near an hourly boundary, wait briefly for Bybit to publish the bar,
        unless an earlier bar is *already* missing and needs catch-up.
        A startup/recovery can fetch several historical bars in one request.
        """
        now = int(bybit_now_ms)
        if now < self.interval_ms:
            return []
        latest_closed_open = (now // self.interval_ms - 1) * self.interval_ms
        close_boundary = (now // self.interval_ms) * self.interval_ms
        in_publish_grace = now < close_boundary + self.after_close_delay_ms
        due = []
        for symbol in symbols:
            processed_open = int(last_processed_open_ms.get(symbol) or 0)
            if processed_open >= latest_closed_open:
                continue
            if in_publish_grace and processed_open >= latest_closed_open - self.interval_ms:
                continue
            if now < self.next_attempt_ms.get(symbol, 0):
                continue
            due.append(symbol)
        return due

    def record_attempt(self, symbol: str, bybit_now_ms: int) -> None:
        """Enforce a retry delay if a request fails, is empty, or lacks a bar."""
        self.next_attempt_ms[symbol] = int(bybit_now_ms) + self.retry_delay_ms
