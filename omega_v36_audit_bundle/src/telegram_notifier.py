"""Optional best-effort bounded asynchronous alerts; never blocks trading."""
import logging
import os
import queue
import threading
import requests

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token=None, chat_id=None):
        self.token = token or os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = chat_id or os.getenv('TELEGRAM_CHAT_ID', '')
        self.queue = queue.Queue(256)
        self.dropped = 0
        if self.token and self.chat_id:
            threading.Thread(target=self._run, daemon=True, name='telegram').start()

    def notify(self, event):
        if not self.token or not self.chat_id:
            return False
        text = 'PAPER v2.1 | ' + ' | '.join(f'{k}={v}' for k,v in event.items())
        try:
            self.queue.put_nowait(text[:4000])
            return True
        except queue.Full:
            self.dropped += 1
            log.warning('Telegram queue full; alert dropped')
            return False

    def _run(self):
        while True:
            text = self.queue.get()
            try:
                # One attempt avoids ambiguous duplicate sends on network timeout.
                response = requests.post(
                    f'https://api.telegram.org/bot{self.token}/sendMessage',
                    json={'chat_id': self.chat_id, 'text': text},
                    timeout=(3, 5)
                )
                if response.status_code != 200 or not response.json().get('ok'):
                    log.warning('Telegram rejected alert (HTTP %s)', response.status_code)
            except Exception:
                # Never log exception URLs: they contain the bot token.
                log.warning('Telegram delivery failed')
            finally:
                self.queue.task_done()
