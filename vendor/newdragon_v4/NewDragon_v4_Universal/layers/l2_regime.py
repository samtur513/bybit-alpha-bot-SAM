"""
Layer 2: Causal Market Regime Detector (CausalRegimeDetector).
Specification from NewDragon v4.0 Universal.

Implements:
1. Strict Causal Log-Space Forward Algorithm: P(S_t | X_1:t) with ZERO Lookahead Bias.
2. Deterministic State Semantic Mapping: shock, bull, bear, sideways (resolves Label Switching).
3. 2D Classification Ensemble: Gaussian HMM probabilities + Hurst Exponent (R/S Analysis).
"""
from typing import Dict, Any, Optional
import numpy as np

try:
    from scipy.special import logsumexp
except ImportError:
    def logsumexp(a, axis=None, keepdims=False):
        max_a = np.max(a, axis=axis, keepdims=True)
        if not keepdims and axis is not None:
            max_a_squeezed = np.squeeze(max_a, axis=axis)
        else:
            max_a_squeezed = max_a
        out = np.log(np.sum(np.exp(a - max_a), axis=axis, keepdims=keepdims)) + max_a_squeezed
        return out

try:
    from hmmlearn.hmm import GaussianHMM
except ImportError:
    GaussianHMM = None


class CausalRegimeDetector:
    """
    Causal HMM filter (4 states) + Hurst exponent with zero lookahead bias.
    """

    def __init__(
        self,
        n_states: int = 4,
        covariance_type: str = "diag",
        min_covar: float = 1e-3,
        n_iter: int = 100,
        random_state: int = 42,
    ) -> None:
        self.n_states = n_states
        self.covariance_type = covariance_type
        self.min_covar = min_covar
        self.n_iter = n_iter
        self.random_state = random_state
        if GaussianHMM is not None:
            self.model = GaussianHMM(
                n_components=n_states,
                covariance_type=covariance_type,
                min_covar=min_covar,
                n_iter=n_iter,
                random_state=random_state,
            )
        else:
            self.model = None
        self.state_map: Dict[int, str] = {}
        self._log_alpha: Optional[np.ndarray] = None
        self.is_fitted: bool = False

    @staticmethod
    def hurst_exponent(prices: np.ndarray, max_lag: int = 20) -> float:
        """
        Calculates Hurst Exponent H via Rescaled Range (R/S) analysis:
        H > 0.52 -> Trending (persistent)
        H ~ 0.50 -> Random walk
        H < 0.47 -> Mean-reverting (anti-persistent)
        """
        if len(prices) < max_lag * 2:
            return 0.50

        lags = range(4, max_lag)
        log_rs = []
        log_lags = []
        log_p = np.log(np.maximum(prices, 1e-12))
        returns = np.diff(log_p)

        for lag in lags:
            n_chunks = len(returns) // lag
            if n_chunks < 1:
                continue
            rs_vals = []
            for i in range(n_chunks):
                chunk = returns[i * lag : (i + 1) * lag]
                mean_adj = chunk - np.mean(chunk)
                cum_dev = np.cumsum(mean_adj)
                r = np.max(cum_dev) - np.min(cum_dev)
                s = np.std(chunk, ddof=1)
                if s > 1e-12:
                    rs_vals.append(r / s)
            if rs_vals:
                log_rs.append(np.log(np.mean(rs_vals)))
                log_lags.append(np.log(lag))

        if len(log_lags) < 3:
            return 0.50

        hurst, _ = np.polyfit(log_lags, log_rs, 1)
        return float(np.clip(hurst, 0.0, 1.0))

    def fit_historical(self, features: np.ndarray) -> None:
        """
        Fits GaussianHMM on in-sample training window:
        features = [log_return, vol_14d, oi_change, funding_rate]
        """
        self.model.fit(features)
        self._sort_and_bind_states()
        self.reset_forward_state(features)
        self.is_fitted = True

    def _sort_and_bind_states(self) -> None:
        """
        Deterministic binding of hidden states to semantics:
        1. Shock: maximum covariance trace / volatility
        2. Bull: highest expected return among remaining
        3. Bear: lowest (negative) expected return among remaining
        4. Sideways: remaining state with near-zero return
        """
        means = self.model.means_      # shape: (n_states, n_features)
        covars = self.model.covars_    # shape: (n_states, n_features) or (n_states, n_features, n_features)
        remaining = set(range(self.n_states))

        # 1. Shock state: maximum trace/sum of covariance + volatility mean
        vol_col = 1 if means.shape[1] > 1 else 0
        if covars.ndim == 3:
            vol_scores = {
                k: float(np.trace(covars[k]) + means[k, vol_col] * 10.0)
                for k in remaining
            }
        else:
            vol_scores = {
                k: float(np.sum(covars[k]) + means[k, vol_col] * 10.0)
                for k in remaining
            }
        shock_idx = max(vol_scores, key=vol_scores.get)
        self.state_map[shock_idx] = "shock"
        remaining.remove(shock_idx)

        # 2. Bull state: maximum positive mean return
        ret_col = 0
        bull_idx = max(remaining, key=lambda k: means[k, ret_col])
        self.state_map[bull_idx] = "bull"
        remaining.remove(bull_idx)

        # 3. Bear state: minimum (negative) mean return
        bear_idx = min(remaining, key=lambda k: means[k, ret_col])
        self.state_map[bear_idx] = "bear"
        remaining.remove(bear_idx)

        # 4. Sideways state: remaining state
        sideways_idx = remaining.pop()
        self.state_map[sideways_idx] = "sideways"

    def reset_forward_state(self, history_features: np.ndarray) -> None:
        """
        Executes Forward algorithm over historical bars to set initial log_alpha.
        """
        log_start = np.log(np.maximum(self.model.startprob_, 1e-30))
        log_trans = np.log(np.maximum(self.model.transmat_, 1e-30))
        log_emiss = self.model._compute_log_likelihood(history_features)

        log_alpha = log_start + log_emiss[0]
        log_alpha -= logsumexp(log_alpha)

        for t in range(1, len(history_features)):
            log_alpha = log_emiss[t] + logsumexp(log_alpha[:, None] + log_trans, axis=0)
            log_alpha -= logsumexp(log_alpha)

        self._log_alpha = log_alpha

    def step_causal(self, x_t: np.ndarray, recent_closes: np.ndarray) -> Dict[str, Any]:
        """
        Causal step on new bar x_t (shape: (n_features,)).
        Strictly computes P(S_t | X_1:t) without observing X_t+1.
        """
        if not self.is_fitted or self._log_alpha is None:
            raise RuntimeError("Regime detector is not fitted yet.")

        log_trans = np.log(np.maximum(self.model.transmat_, 1e-30))
        log_emiss_t = self.model._compute_log_likelihood(x_t.reshape(1, -1))[0]

        # Forward recursive update:
        # log_alpha[k] = log_emiss[k] + logsumexp_j(log_alpha[j] + log_trans[j, k])
        self._log_alpha = log_emiss_t + logsumexp(self._log_alpha[:, None] + log_trans, axis=0)
        self._log_alpha -= logsumexp(self._log_alpha)
        probs_raw = np.exp(self._log_alpha)

        probs = {self.state_map[k]: float(probs_raw[k]) for k in range(self.n_states)}
        hurst = self.hurst_exponent(recent_closes[-100:])

        # 2D Classification Routing (HMM + Hurst)
        if probs.get("shock", 0.0) >= 0.35:
            regime = "shock"
        elif probs.get("bull", 0.0) >= 0.55 and hurst >= 0.52:
            regime = "bull"
        elif probs.get("bear", 0.0) >= 0.55 and hurst >= 0.52:
            regime = "bear"
        elif probs.get("sideways", 0.0) >= 0.45 or hurst <= 0.46:
            regime = "sideways"
        else:
            # Transitional zone: pick highest probability
            regime = max(probs, key=probs.get)

        return {
            "regime": regime,
            "probabilities": probs,
            "hurst": hurst,
        }
