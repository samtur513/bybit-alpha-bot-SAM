"""
Official Bybit V5 Client for Market Data & Order Management.
Implements robust retry, error handling, retCode validation.
"""
from decimal import Decimal
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

class BybitAPIError(Exception):
    """Custom exception for Bybit API errors."""
    def __init__(self, ret_code: int, ret_msg: str, endpoint: str):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        self.endpoint = endpoint
        super().__init__(f"Bybit API Error [{endpoint}]: retCode={ret_code}, retMsg='{ret_msg}'")

class BybitClient:
    """Bybit V5 REST Client with retry logic and error tracking."""
    BASE_URL = "https://api.bybit.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: str = BASE_URL,
        timeout: int = 15,
        max_retries: int = 5,
        backoff_factor: float = 1.0,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.session = requests.Session()

    def _generate_signature(self, timestamp: int, payload: str) -> str:
        if not self.api_secret:
            return ""
        param_str = f"{timestamp}{self.api_key}5000{payload}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        auth: bool = False,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        
        for attempt in range(1, self.max_retries + 1):
            try:
                timestamp = int(time.time() * 1000)
                if auth and self.api_key:
                    headers["X-BAPI-API-KEY"] = self.api_key
                    headers["X-BAPI-TIMESTAMP"] = str(timestamp)
                    headers["X-BAPI-RECV-WINDOW"] = "5000"
                    payload = json.dumps(data) if data else (requests.compat.urlencode(params) if params else "")
                    headers["X-BAPI-SIGN"] = self._generate_signature(timestamp, payload)

                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data if data else None,
                    headers=headers,
                    timeout=self.timeout,
                )

                if response.status_code == 429:
                    sleep_time = self.backoff_factor * (2 ** attempt)
                    logger.warning(f"Rate limited on {endpoint}. Sleeping {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
                    continue

                response.raise_for_status()
                res_json = response.json()
                ret_code = res_json.get("retCode", -1)
                ret_msg = res_json.get("retMsg", "")

                if ret_code != 0:
                    # Specific transient Bybit errors can be retried
                    if ret_code in [10002, 10006, 10016, 10018]:  # server busy, rate limit, or svc error
                        sleep_time = self.backoff_factor * (2 ** attempt)
                        logger.warning(f"Bybit retCode {ret_code} on {endpoint}. Retrying in {sleep_time}s...")
                        time.sleep(sleep_time)
                        continue
                    raise BybitAPIError(ret_code, ret_msg, endpoint)

                return res_json

            except (requests.RequestException, BybitAPIError) as e:
                if isinstance(e, BybitAPIError) and e.ret_code not in [10002, 10006, 10016, 10018]:
                    raise
                if attempt == self.max_retries:
                    logger.error(f"Failed {method} {endpoint} after {self.max_retries} attempts: {e}")
                    raise
                sleep_time = self.backoff_factor * (2 ** attempt)
                logger.info(f"Retrying {endpoint} attempt {attempt+1}/{self.max_retries} after error: {e}. Sleep {sleep_time:.2f}s")
                time.sleep(sleep_time)

        raise RuntimeError(f"Unexpected loop exit in request for {endpoint}")

    def get_server_time(self) -> int:
        res = self.request("GET", "/v5/market/time")
        return int(res["result"]["timeNano"]) // 1_000_000

    def get_instruments_info(self, category: str = "linear", symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"category": category, "limit": 1000}
        if symbol:
            params["symbol"] = symbol
        items, seen = [], set()
        while True:
            result = self.request("GET", "/v5/market/instruments-info", params=params).get("result", {})
            items.extend(result.get("list", []))
            cursor = result.get("nextPageCursor")
            if not cursor:
                return items
            if cursor in seen:
                raise RuntimeError("Repeated instrument pagination cursor")
            seen.add(cursor)
            params["cursor"] = cursor

    def get_kline(
        self,
        symbol: str,
        interval: str = "1",
        start: Optional[int] = None,
        end: Optional[int] = None,
        limit: int = 1000,
        category: str = "linear",
    ) -> List[List[str]]:
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        res = self.request("GET", "/v5/market/kline", params=params)
        return res.get("result", {}).get("list", [])

    def get_mark_price_kline(
        self,
        symbol: str,
        interval: str = "1",
        start: Optional[int] = None,
        end: Optional[int] = None,
        limit: int = 1000,
        category: str = "linear",
    ) -> List[List[str]]:
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        res = self.request("GET", "/v5/market/mark-price-kline", params=params)
        return res.get("result", {}).get("list", [])

    def get_funding_history(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
        category: str = "linear",
    ) -> List[Dict[str, Any]]:
        params = {
            "category": category,
            "symbol": symbol,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        res = self.request("GET", "/v5/market/funding/history", params=params)
        return res.get("result", {}).get("list", [])

    def get_linear_tickers(self) -> Dict[str, Decimal]:
        """Fetches all linear perpetual tickers in a single call."""
        res = self.request("GET", "/v5/market/tickers", params={"category": "linear"})
        tickers_list = res.get("result", {}).get("list", [])
        prices = {}
        for item in tickers_list:
            sym = item.get("symbol")
            last_p = item.get("lastPrice")
            if sym and last_p:
                try:
                    prices[sym] = Decimal(str(last_p))
                except Exception:
                    pass
        return prices

