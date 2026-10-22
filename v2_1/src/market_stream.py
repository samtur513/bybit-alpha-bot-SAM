"""Bybit public linear feed v2.1. One bounded ordered queue, no trading in callbacks."""
import json
import logging
import queue
import threading
import time

log = logging.getLogger(__name__)


class MarketStream:
    def __init__(self, symbols, capacity=20000):
        self.symbols = set(symbols)
        self.events = queue.Queue(capacity)
        self.stop = threading.Event()
        self.overflow = threading.Event()
        self.socket = None

    def _put(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.overflow.set()  # Lost path information: force supervised restart.
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass

    def ingest(self, message):
        obj = json.loads(message) if isinstance(message, str) else message
        if obj.get('op') == 'subscribe' and obj.get('success') is False:
            self.overflow.set()
            return
        topic = obj.get('topic', '')
        symbol = topic.split('.')[-1]
        if symbol not in self.symbols:
            return
        if topic.startswith('tickers.'):
            data = obj.get('data', {})
            # Delta fields absent => unchanged. Never replay a cached price as a new tick.
            if data.get('lastPrice'):
                self._put(('price', symbol, int(obj.get('ts', 0)), data['lastPrice'], time.monotonic()))
        elif topic.startswith('kline.60.'):
            for bar in obj.get('data', []):
                if bar.get('confirm') is True:
                    self._put(('bar', symbol, int(obj.get('ts', 0)), bar, time.monotonic()))

    def start(self):
        threading.Thread(target=self._run, daemon=True, name='bybit-feed-v21').start()

    def _run(self):
        import websocket
        delay = 1
        while not self.stop.is_set() and not self.overflow.is_set():
            started = time.monotonic()
            def opened(ws):
                self._put(('reset', '', 0, None, time.monotonic()))
                topics = [f'{kind}.{s}' for s in sorted(self.symbols)
                          for kind in ('tickers', 'kline.60')]
                for i in range(0, len(topics), 10):
                    ws.send(json.dumps({'op': 'subscribe', 'args': topics[i:i+10]}))
            self.socket = websocket.WebSocketApp(
                'wss://stream.bybit.com/v5/public/linear',
                on_open=opened,
                on_message=lambda ws, msg: self.ingest(msg),
                on_error=lambda ws, err: log.warning('WebSocket transport error')
            )
            # Bybit application heartbeat, in addition to transport ping/pong.
            heartbeat_done = threading.Event()
            def heartbeat():
                while not heartbeat_done.wait(20):
                    try:
                        self.socket.send('{"op":"ping"}')
                    except Exception:
                        return
            threading.Thread(target=heartbeat, daemon=True).start()
            try:
                self.socket.run_forever(ping_interval=20, ping_timeout=10)
            finally:
                heartbeat_done.set()
            if time.monotonic() - started > 60:
                delay = 1
            self.stop.wait(delay)
            delay = min(delay * 2, 30)

    def close(self):
        self.stop.set()
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
