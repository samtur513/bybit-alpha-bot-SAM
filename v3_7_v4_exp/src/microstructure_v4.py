"""
v3.7-v4-exp Microstructure Engine with Order Flow Imbalance (OFI) and Z_OFI metrics.
Calculates continuous normalized order flow toxicity for NewDragon Fast Scratch.
"""

import collections
import math
from typing import Dict, List, Optional, Tuple
import numpy as np

from v3_7.src.microstructure import SymbolMicrostructureState, InstrumentMeta


class SymbolMicrostructureStateV4(SymbolMicrostructureState):
    """
    Microstructure state with continuous OFI, rolling Z_OFI, and Kaufman Efficiency Ratio (KER).
    """

    def __init__(self, symbol: str, meta: InstrumentMeta):
        super().__init__(symbol, meta)
        self.prev_best_bid: float = 0.0
        self.prev_best_bid_qty: float = 0.0
        self.prev_best_ask: float = 0.0
        self.prev_best_ask_qty: float = 0.0

        # Rolling OFI buffer: stores (timestamp_ms, ofi_value)
        self.ofi_history: collections.deque = collections.deque(maxlen=300)
        self.price_history: collections.deque = collections.deque(maxlen=60)
        self.current_ofi: float = 0.0
        self.current_z_ofi: float = 0.0
        self.current_ker: float = 0.50

    def apply_orderbook(self, msg_type: str, data: dict, ts_ms: int) -> None:
        """Applies orderbook update and computes OFI + Z_OFI."""
        super().apply_orderbook(msg_type, data, ts_ms)

        if not self.sorted_bids or not self.sorted_asks:
            return

        cur_bid, cur_bid_q = self.sorted_bids[0][0], self.sorted_bids[0][1]
        cur_ask, cur_ask_q = self.sorted_asks[0][0], self.sorted_asks[0][1]

        if self.prev_best_bid > 0 and self.prev_best_ask > 0:
            # Bid side OFI delta
            if cur_bid > self.prev_best_bid:
                delta_bid = cur_bid_q
            elif cur_bid == self.prev_best_bid:
                delta_bid = cur_bid_q - self.prev_best_bid_qty
            else:
                delta_bid = -self.prev_best_bid_qty

            # Ask side OFI delta
            if cur_ask < self.prev_best_ask:
                delta_ask = -cur_ask_q
            elif cur_ask == self.prev_best_ask:
                delta_ask = self.prev_best_ask_qty - cur_ask_q
            else:
                delta_ask = self.prev_best_ask_qty

            ofi = delta_bid + delta_ask
            self.current_ofi = ofi
            self.ofi_history.append((ts_ms, ofi))

            # Compute rolling Z_OFI over last 100 observations
            if len(self.ofi_history) >= 20:
                recent_vals = [v for _, v in list(self.ofi_history)[-100:]]
                mean_ofi = float(np.mean(recent_vals))
                std_ofi = float(np.std(recent_vals))
                if std_ofi > 1e-6:
                    self.current_z_ofi = float(np.clip((ofi - mean_ofi) / std_ofi, -5.0, 5.0))
                else:
                    self.current_z_ofi = 0.0

        self.prev_best_bid = cur_bid
        self.prev_best_bid_qty = cur_bid_q
        self.prev_best_ask = cur_ask
        self.prev_best_ask_qty = cur_ask_q

        # Track mid price for KER calculation
        if self.mid_price > 0:
            self.price_history.append((ts_ms, self.mid_price))
            if len(self.price_history) >= 20:
                pts = [p for _, p in self.price_history]
                direction = abs(pts[-1] - pts[0])
                volatility = sum(abs(pts[i] - pts[i - 1]) for i in range(1, len(pts)))
                self.current_ker = (direction / volatility) if volatility > 1e-9 else 0.0

    def get_z_ofi(self) -> float:
        """Returns normalized Order Flow Imbalance Z-score."""
        return self.current_z_ofi

    def get_ker(self) -> float:
        """Returns current Kaufman Efficiency Ratio."""
        return self.current_ker
