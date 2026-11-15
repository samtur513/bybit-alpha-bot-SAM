"""Read-only current quote cache. Never writes positions, accounting or historical marks."""
import datetime, math
UTC=datetime.timezone.utc
SYMS=["BTCUSDT","ETHUSDT","SOLUSDT"]

def utc_time(value):
    t=datetime.datetime.fromisoformat(value.replace("Z","+00:00"))
    if t.utcoffset()!=datetime.timedelta(0):
        raise ValueError("QUOTE_TIME_NOT_UTC")
    return t

class QuoteCache:
    def __init__(self):
        self._batch=None

    def observe(self,markets,observed_at):
        # Called only after the frozen markets() has validated a complete three-symbol batch.
        quotes=[]
        if len(markets)!=3:raise ValueError("QUOTE_BATCH_INCOMPLETE")
        for symbol,m in zip(SYMS,markets):
            if m["book"]["s"]!=symbol or m["meta"]["symbol"]!=symbol:
                raise ValueError("QUOTE_SYMBOL_MISMATCH")
            event=datetime.datetime.fromtimestamp(m["book"]["ts"]/1000,UTC)
            available=utc_time(m["book_receipt"]["available_at"])
            mid=float(m["mid"])
            if not math.isfinite(mid) or mid<=0 or not 0<float(m["bids"][0][0])<float(m["asks"][0][0]):
                raise ValueError("QUOTE_PRICE_INVALID")
            if abs(mid-(float(m["bids"][0][0])+float(m["asks"][0][0]))/2)>1e-10*mid:
                raise ValueError("QUOTE_MID_MISMATCH")
            if not datetime.timedelta(0)<=observed_at-event<=datetime.timedelta(seconds=5):
                raise ValueError("QUOTE_NOT_FRESH_AT_OBSERVATION")
            if not event<=available<=observed_at:
                raise ValueError("QUOTE_AVAILABILITY_INVALID")
            quotes.append(dict(symbol=symbol,mid=mid,event_at=event,available_at=available))
        if max(q["event_at"] for q in quotes)-min(q["event_at"] for q in quotes)>datetime.timedelta(seconds=5):
            raise ValueError("QUOTE_BATCH_SKEW")
        self._batch=tuple(quotes)

    def current(self,now):
        batch=self._batch
        if batch is None:return None
        if any(not datetime.timedelta(0)<=now-q["event_at"]<=datetime.timedelta(seconds=120)
               or q["available_at"]>now for q in batch):
            return None
        return dict(prices=tuple(q["mid"] for q in batch),
                    at=min(q["event_at"] for q in batch).isoformat())

    def clear(self):
        self._batch=None
