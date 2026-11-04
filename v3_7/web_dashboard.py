"""
Live Web Dashboard & Paper Trading Server v3.7 HFT Scalper (Port 8090).
Features:
- Microstructure L2 Orderbook (50 levels depth) & Public Trade Stream.
- 3 HFT Alpha Engines:
  * E1: OBI + Flow Toxicity Breakout (Maker In / Chased Limit Out)
  * E2: Liquidity Sweep & Absorption Reversion (Deep Book Sweep Reversal)
  * E3: Lead-Lag Beta-Catchup (High-Beta Alt vs BTCUSDT Micro-Impulse)
- 25ms Position & Queue Monitor loop with Penny-Perfect Accounting.
- REST API: /api/status, /api/state, /api/logs
"""

import asyncio
import collections
from datetime import datetime, timezone
from decimal import Decimal
import http.server
import json
import logging
from pathlib import Path
import socketserver
import sys
import threading
import time
import urllib.parse

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import v3_7.src.core_scalper as core_scalper
from v3_7.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE

LOG_BUFFER = collections.deque(maxlen=300)


class BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            LOG_BUFFER.append(msg)
        except Exception:
            pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
root_logger = logging.getLogger()
buf_handler = BufferHandler()
buf_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
root_logger.addHandler(buf_handler)

logger = logging.getLogger("WebDashboardV37")

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.7 Scalper | HFT Microstructure</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>⚡</text></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet" media="print" onload="this.media='all'">
    <noscript><link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet"></noscript>
    <style>
        :root {
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-cyan: #00f2fe;
            --accent-blue: #38bdf8;
            --accent-green: #10b981;
            --accent-red: #f43f5e;
            --accent-gold: #f59e0b;
            --accent-pink: #ec4899;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
            --border-highlight: #334155;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: 'Inter', -apple-system, sans-serif;
            padding: 24px;
            font-size: 14px;
        }

        .container { max-width: 1440px; margin: 0 auto; }
        
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 20px;
            margin-bottom: 24px;
        }
        .header-title { display: flex; align-items: center; gap: 14px; }
        .badge {
            background: linear-gradient(135deg, #ec4899 0%, #a855f7 100%);
            color: #fff;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
            letter-spacing: 0.5px;
        }
        h1 { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
        .subtitle { font-size: 12px; color: var(--text-muted); margin-top: 3px; }

        .grid-4 {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            position: relative;
        }
        .card-label {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
        }
        .card-value {
            font-size: 24px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
        }
        .card-sub {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 6px;
            display: flex;
            justify-content: space-between;
        }

        .color-green { color: var(--accent-green); }
        .color-red { color: var(--accent-red); }
        .color-blue { color: var(--accent-blue); }
        .color-pink { color: var(--accent-pink); }
        .color-gold { color: var(--accent-gold); }

        .section-title {
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
        }
        th {
            text-align: left;
            padding: 10px 14px;
            background: rgba(30, 41, 59, 0.4);
            color: var(--text-muted);
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
            color: var(--text-main);
        }
        tr:hover td {
            background: rgba(30, 41, 59, 0.25);
        }

        .pos-badge {
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 10px;
        }
        .pos-long { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.4); }
        .pos-short { background: rgba(244, 63, 94, 0.2); color: var(--accent-red); border: 1px solid rgba(244, 63, 94, 0.4); }

        .log-box {
            background: #040711;
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px;
            height: 240px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            line-height: 1.5;
            color: #cbd5e1;
        }
        .log-entry { margin-bottom: 4px; border-bottom: 1px solid rgba(255, 255, 255, 0.03); padding-bottom: 2px; }
        .log-time { color: var(--text-muted); margin-right: 8px; }
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <div class="header-title">
                <span class="badge">V3.7 HFT SCALPER</span>
                <div>
                    <h1>Bybit Multi-Alpha v3.7 Microstructure Scalper</h1>
                    <div class="subtitle">15m / M1 Orderbook L2 (50 levels) • OBI Breakout • Liquidity Sweep • Lead-Lag Beta</div>
                </div>
            </div>
            <div style="text-align: right;">
                <div id="connection-status" style="font-size: 12px; font-weight: 700; color: var(--accent-green);">● WEBSOCKET L2 ONLINE</div>
                <div id="last-updated" class="subtitle" style="font-family: 'JetBrains Mono', monospace;">--:--:-- UTC</div>
            </div>
        </header>

        <div class="grid-4">
            <div class="card">
                <div class="card-label">Капитал портфеля <span>USDT</span></div>
                <div class="card-value color-pink" id="total-equity">$300.00</div>
                <div class="card-sub">
                    <span>Старт: <strong id="initial-capital">$300.00</strong></span>
                    <span id="pnl-percent">0.00%</span>
                </div>
            </div>

            <div class="card">
                <div class="card-label">Реализованный PnL <span>USDT</span></div>
                <div class="card-value" id="realized-pnl">$0.00</div>
                <div class="card-sub">
                    <span>Нереализ: <strong id="unrealized-pnl">$0.00</strong></span>
                    <span>Сделок: <strong id="trades-count">0</strong></span>
                </div>
            </div>

            <div class="card">
                <div class="card-label">Активность <span>L2 Queue</span></div>
                <div class="card-value color-blue" id="active-positions-count">0</div>
                <div class="card-sub">
                    <span>Лимиты в стакане: <strong id="pending-orders-count">0</strong></span>
                    <span>Режим: <strong id="trading-mode" class="color-gold">PAPER</strong></span>
                </div>
            </div>

            <div class="card">
                <div class="card-label">Защитный щит <span>News Shield</span></div>
                <div class="card-value color-green" id="news-status">V1.2 ACTIVE</div>
                <div class="card-sub">
                    <span>Circuit Breaker: <strong id="circuit-status">OK</strong></span>
                    <span>Монет в пуле: <strong id="coins-count">8</strong></span>
                </div>
            </div>
        </div>

        <div style="margin-bottom: 24px;">
            <div class="section-title">⚡ Активные позиции в рынке</div>
            <div class="card" style="padding: 0; overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Символ</th>
                            <th>Движок</th>
                            <th>Сторона</th>
                            <th>Объем</th>
                            <th>Вход</th>
                            <th>Текущая</th>
                            <th>Stop Loss</th>
                            <th>Take Profit 1</th>
                            <th>Take Profit 2</th>
                            <th>Нереализ. PnL</th>
                        </tr>
                    </thead>
                    <tbody id="positions-table-body">
                        <tr>
                            <td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">
                                Нет активных позиций. Ожидание сигналов OBI, Sweep Absorption или Lead-Lag...
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div style="margin-bottom: 24px;">
            <div class="section-title">⏱ Отложенные лимитные ордера в очереди L2</div>
            <div class="card" style="padding: 0; overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Символ</th>
                            <th>Движок</th>
                            <th>Сторона</th>
                            <th>Объем</th>
                            <th>Цена лимита</th>
                            <th>Объем перед нами в очереди</th>
                        </tr>
                    </thead>
                    <tbody id="orders-table-body">
                        <tr>
                            <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 18px;">
                                Очередь пуста.
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div>
            <div class="section-title">📜 Журнал событий и исполнения (25 мс)</div>
            <div class="log-box" id="log-console">
                <div class="log-entry"><span class="log-time">[SYSTEM]</span> Инициализация BybitScalper v3.7...</div>
            </div>
        </div>
    </div>

    <script>
        function escapeHtml(text) {
            return String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) throw new Error("HTTP " + res.status);
                const data = await res.json();

                document.getElementById('total-equity').innerText = '$' + (data.equity || 300.0).toFixed(2);
                document.getElementById('initial-capital').innerText = '$' + (data.initial_capital || 300.0).toFixed(2);
                
                const netPnl = data.net_pnl || 0.0;
                const pnlPct = ((netPnl / (data.initial_capital || 300.0)) * 100);
                const pnlEl = document.getElementById('pnl-percent');
                pnlEl.innerText = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%';
                pnlEl.className = pnlPct >= 0 ? 'color-green' : 'color-red';

                const rPnlEl = document.getElementById('realized-pnl');
                rPnlEl.innerText = (data.realized_pnl >= 0 ? '+$' : '-$') + Math.abs(data.realized_pnl || 0).toFixed(2);
                rPnlEl.className = 'card-value ' + (data.realized_pnl >= 0 ? 'color-green' : 'color-red');

                document.getElementById('unrealized-pnl').innerText = '$' + (data.unrealized_pnl || 0).toFixed(2);
                document.getElementById('trades-count').innerText = data.total_trades_count || 0;
                document.getElementById('active-positions-count').innerText = (data.open_positions || []).length;
                document.getElementById('pending-orders-count').innerText = (data.pending_orders || []).length;
                document.getElementById('trading-mode').innerText = data.mode || "PAPER";
                document.getElementById('coins-count').innerText = (data.monitored_symbols || []).length;
                document.getElementById('circuit-status').innerText = data.circuit_breaker ? "TRIPPED" : "HEALTHY";
                
                const isOnline = data.stream_ready !== false;
                document.getElementById('connection-status').innerText = isOnline ? '● WEBSOCKET L2 ONLINE' : '● WEBSOCKET RECONNECTING...';
                document.getElementById('connection-status').style.color = isOnline ? 'var(--accent-green)' : 'var(--accent-gold)';
                document.getElementById('last-updated').innerText = new Date().toISOString().substring(11, 19) + ' UTC';

                // Render positions
                const posBody = document.getElementById('positions-table-body');
                if (data.open_positions && data.open_positions.length > 0) {
                    posBody.innerHTML = data.open_positions.map(p => `
                        <tr>
                            <td><strong>${escapeHtml(p.symbol)}</strong></td>
                            <td><span style="color: var(--accent-pink); font-weight:700;">${escapeHtml(p.engine_id || 'E1_OBI')}</span></td>
                            <td><span class="pos-badge ${p.direction === 'LONG' || p.direction === 1 ? 'pos-long' : 'pos-short'}">${p.direction === 1 ? 'LONG' : (p.direction === -1 ? 'SHORT' : escapeHtml(p.direction))}</span></td>
                            <td>${p.qty}</td>
                            <td>${Number(p.entry_price || 0).toFixed(4)}</td>
                            <td>${Number(p.current_price || 0).toFixed(4)}</td>
                            <td class="color-red">${p.stop_loss ? Number(p.stop_loss).toFixed(4) : '-'}</td>
                            <td class="color-green">${p.take_profit_1 ? Number(p.take_profit_1).toFixed(4) : '-'}</td>
                            <td class="color-green">${p.take_profit_2 ? Number(p.take_profit_2).toFixed(4) : '-'}</td>
                            <td class="${(p.unrealized_pnl || 0) >= 0 ? 'color-green' : 'color-red'}"><strong>${((p.unrealized_pnl || 0) >= 0 ? '+' : '') + Number(p.unrealized_pnl || 0).toFixed(4)} USDT</strong></td>
                        </tr>
                    `).join('');
                } else {
                    posBody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет активных позиций. Ожидание сигналов OBI, Sweep Absorption или Lead-Lag...</td></tr>`;
                }

                // Render orders
                const ordBody = document.getElementById('orders-table-body');
                if (data.pending_orders && data.pending_orders.length > 0) {
                    ordBody.innerHTML = data.pending_orders.map(o => `
                        <tr>
                            <td><strong>${escapeHtml(o.symbol)}</strong></td>
                            <td><span style="color: var(--accent-blue); font-weight:700;">${escapeHtml(o.engine_id || 'OMS')}</span></td>
                            <td><span class="pos-badge ${o.side === 'Buy' ? 'pos-long' : 'pos-short'}">${escapeHtml((o.side || 'BUY').toUpperCase())}</span></td>
                            <td>${o.qty}</td>
                            <td>${Number(o.price || 0).toFixed(4)}</td>
                            <td style="color: var(--accent-gold); font-weight: 700;">${o.queue_ahead_vol || 0}</td>
                        </tr>
                    `).join('');
                } else {
                    ordBody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 18px;">Очередь пуста.</td></tr>`;
                }
            } catch (e) {
                console.error("updateDashboard error:", e);
                document.getElementById('connection-status').innerText = '● DISCONNECTED';
                document.getElementById('connection-status').style.color = 'var(--accent-red)';
            }
        }

        async function fetchLogs() {
            try {
                const res = await fetch('/api/logs');
                if (res.ok) {
                    const logs = await res.json();
                    const consoleEl = document.getElementById('log-console');
                    if (Array.isArray(logs) && logs.length > 0) {
                        consoleEl.innerHTML = logs.map(l => `<div class="log-entry">${escapeHtml(l)}</div>`).join('');
                        consoleEl.scrollTop = consoleEl.scrollHeight;
                    }
                }
            } catch (e) {
                console.error("fetchLogs error:", e);
            }
        }

        window.addEventListener('DOMContentLoaded', () => {
            updateDashboard();
            fetchLogs();
            setInterval(updateDashboard, 1500);
            setInterval(fetchLogs, 2500);
        });
    </script>
</body>
</html>
"""


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def _send_payload(self, payload: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_payload(HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")

        elif path == "/favicon.ico":
            self._send_payload(b"", "image/x-icon", 204)

        elif path == "/api/status":
            if core_scalper.GLOBAL_OMS is not None:
                status = core_scalper.GLOBAL_OMS.get_status()
            elif LIVE_STATUS_FILE.exists():
                try:
                    with open(LIVE_STATUS_FILE, "r", encoding="utf-8") as f:
                        status = json.load(f)
                except Exception:
                    status = {"version": "3.7.0", "equity": 300.0, "online": True}
            else:
                status = {
                    "version": "3.7.0",
                    "name": "v3.7 HFT Scalper",
                    "mode": "PAPER_TRADING",
                    "port": PORT,
                    "initial_capital": 300.0,
                    "capital": 300.0,
                    "equity": 300.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "open_positions": [],
                    "pending_orders": [],
                    "closed_trades": [],
                    "monitored_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                    "stream_ready": True,
                    "news_shield": True,
                    "news_defense_active": True,
                }
            payload = json.dumps(status, default=str).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")

        elif path == "/api/state":
            if core_scalper.GLOBAL_OMS is not None:
                st = core_scalper.GLOBAL_OMS.get_status()
            else:
                st = {}
            payload = json.dumps(st, default=str).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")

        elif path == "/api/logs":
            logs = list(LOG_BUFFER)
            payload = json.dumps(logs).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")

        else:
            self._send_payload(b"Not Found", "text/plain", 404)

    def log_message(self, format, *args):
        return


def run_scalper_in_thread():
    """Runs the asyncio scalper main loop in a background daemon thread."""
    logger.info("Launching Bybit Scalper v3.7 async engine...")
    try:
        asyncio.run(core_scalper.main())
    except Exception as e:
        logger.error(f"Scalper daemon terminated with error: {e}")


def start_server_and_bot():
    logger.info(f"Initializing {VERSION_NAME} Web Dashboard on Port {PORT}...")

    # Start scalper thread
    t = threading.Thread(target=run_scalper_in_thread, daemon=True)
    t.start()

    server_address = ("0.0.0.0", PORT)
    with socketserver.ThreadingTCPServer(server_address, DashboardHTTPRequestHandler) as httpd:
        httpd.allow_reuse_address = True
        logger.info(f"{VERSION_NAME} live on http://127.0.0.1:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Dashboard interrupted. Shutting down...")


if __name__ == "__main__":
    start_server_and_bot()
