"""
Web Dashboard for v3.9 NewDragon Scalp Lab (Port 8092).
Displays HFT Orderbook Microstructure, live Z_OFI flow imbalance, and Micro-Router telemetry.
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
import http.server
import json
import logging
from pathlib import Path
import socketserver
import sys
import threading
import time

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

import v3_9.src.core_scalper_v39 as core_scalper_v39
from v3_9.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE

LOG_BUFFER = deque(maxlen=300)


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

logger = logging.getLogger("WebDashboardV39")

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.9 NewDragon Scalp Lab | Port 8092</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>⚡</text></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-violet: #8b5cf6;
            --accent-cyan: #00f2fe;
            --accent-green: #10b981;
            --accent-red: #f43f5e;
            --accent-gold: #f59e0b;
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
            min-height: 100vh;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }
        .title-block { display: flex; align-items: center; gap: 14px; }
        .logo-icon {
            font-size: 32px;
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, rgba(139,92,246,0.2), rgba(0,242,254,0.1));
            border: 1px solid var(--accent-violet);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .title h1 { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
        .title p { font-size: 13px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }
        .badges { display: flex; gap: 10px; flex-wrap: wrap; }
        .badge {
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
            border: 1px solid transparent;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .badge-violet { background: rgba(139,92,246,0.15); border-color: rgba(139,92,246,0.4); color: #c4b5fd; }
        .badge-cyan { background: rgba(0,242,254,0.15); border-color: rgba(0,242,254,0.4); color: var(--accent-cyan); }
        .badge-green { background: rgba(16,185,129,0.15); border-color: rgba(16,185,129,0.4); color: var(--accent-green); }

        .grid-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
        }
        .stat-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-bottom: 6px; }
        .stat-val { font-size: 24px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }

        .table-panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }
        .panel-header { font-size: 14px; font-weight: 700; color: #c4b5fd; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
        table { width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 13px; }
        th { text-align: left; padding: 10px 14px; color: var(--text-muted); font-size: 11px; text-transform: uppercase; border-bottom: 1px solid var(--border); }
        td { padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,0.04); }
        tr:hover { background: rgba(255,255,255,0.02); }

        .log-panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
        }
        .log-box {
            background: #05080e;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            height: 180px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: #94a3b8;
            line-height: 1.5;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="title-block">
            <div class="logo-icon">⚡</div>
            <div class="title">
                <h1>Bybit Multi-Alpha v3.9 NewDragon Scalp Lab</h1>
                <p>15m/M1 L2 Microstructure | Micro-Router (minNotional) | Z_OFI Fast Scratch</p>
            </div>
        </div>
        <div class="badges">
            <div class="badge badge-violet">⚡ Port :8092</div>
            <div class="badge badge-cyan">🐉 NewDragon v4 Scalp Router</div>
            <div class="badge badge-green">🛡️ Z_OFI Flow Scratch</div>
            <div class="badge badge-violet" id="btcStatus">BTC: OK</div>
        </div>
    </div>

    <div class="grid-stats">
        <div class="stat-card">
            <div class="stat-label">Капитал (Equity)</div>
            <div class="stat-val" id="equityVal" style="color: var(--accent-cyan);">$300.00</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Чистый PnL</div>
            <div class="stat-val" id="pnlVal">$0.00</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">В рынке / Очередь</div>
            <div class="stat-val" id="posCount" style="color: var(--accent-violet);">0 / 0</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Z_OFI Fast Scratches</div>
            <div class="stat-val" id="scratchCount" style="color: var(--accent-gold);">0</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Win Rate / Сделок</div>
            <div class="stat-val" id="winRateVal" style="color: var(--accent-green);">0% (0)</div>
        </div>
    </div>

    <div class="table-panel">
        <div class="panel-header">Радар микроструктуры и Z_OFI Order Flow</div>
        <table>
            <thead>
                <tr>
                    <th>Тикер</th>
                    <th>Mid Price</th>
                    <th>Спрэд (Ticks)</th>
                    <th>OBI (Imbalance)</th>
                    <th>Z_OFI (Normalized Flow)</th>
                    <th>Статус потока</th>
                </tr>
            </thead>
            <tbody id="radarTableBody">
                <tr><td colspan="6" style="text-align:center; color: var(--text-muted);">Инициализация микроструктуры...</td></tr>
            </tbody>
        </table>
    </div>

    <div class="table-panel">
        <div class="panel-header">Активные позиции скальпинга</div>
        <table>
            <thead>
                <tr>
                    <th>Тикер</th>
                    <th>Движок</th>
                    <th>Сторона</th>
                    <th>Объем</th>
                    <th>Вход</th>
                    <th>Текущая</th>
                    <th>Стоп-Лосс</th>
                    <th>TP1 Maker</th>
                    <th>Удержание (с)</th>
                </tr>
            </thead>
            <tbody id="posTableBody">
                <tr><td colspan="9" style="text-align:center; color: var(--text-muted);">Нет открытых позиций</td></tr>
            </tbody>
        </table>
    </div>

    <div class="table-panel">
        <div class="panel-header">Закрытые скальп-сделки</div>
        <table>
            <thead>
                <tr>
                    <th>Тикер</th>
                    <th>Движок</th>
                    <th>Сторона</th>
                    <th>Вход</th>
                    <th>Выход</th>
                    <th>Объем</th>
                    <th>Причина</th>
                    <th>Net PnL</th>
                </tr>
            </thead>
            <tbody id="tradeTableBody">
                <tr><td colspan="8" style="text-align:center; color: var(--text-muted);">История сделок пуста</td></tr>
            </tbody>
        </table>
    </div>

    <div class="log-panel">
        <div class="panel-header">Системный журнал событий v3.9</div>
        <div class="log-box" id="logBox">Ожидание событий...</div>
    </div>

    <script>
        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();

                document.getElementById('equityVal').textContent = '$' + (data.capital || data.equity || 300).toFixed(2);
                const pnl = data.realized_pnl !== undefined ? data.realized_pnl : (data.daily_pnl_usdt || 0);
                const pnlEl = document.getElementById('pnlVal');
                pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
                pnlEl.style.color = pnl > 0 ? 'var(--accent-green)' : (pnl < 0 ? 'var(--accent-red)' : 'var(--text-muted)');

                const posCnt = (data.open_positions && Array.isArray(data.open_positions)) ? data.open_positions.length : (data.active_positions_count || 0);
                const ordCnt = (data.pending_orders && Array.isArray(data.pending_orders)) ? data.pending_orders.length : (data.pending_orders_count || 0);
                document.getElementById('posCount').textContent = posCnt + ' / ' + ordCnt;
                document.getElementById('scratchCount').textContent = data.scratch_count || 0;
                
                const closedTrades = data.closed_trades || [];
                const winCount = closedTrades.filter(t => (t.net_pnl_usdt !== undefined ? t.net_pnl_usdt : (t.net_pnl || 0)) > 0).length;
                const winRate = closedTrades.length > 0 ? (winCount / closedTrades.length * 100) : 0;
                document.getElementById('winRateVal').textContent = winRate.toFixed(0) + '% (' + closedTrades.length + ')';

                // Radar Z_OFI
                const radarData = data.radar || {};
                const radarBody = document.getElementById('radarTableBody');
                const symbols = Object.keys(radarData);
                if (symbols.length > 0) {
                    radarBody.innerHTML = symbols.map(sym => {
                        const item = radarData[sym] || {};
                        const z = item.z_ofi !== undefined ? item.z_ofi : 0.0;
                        const zColor = z > 1.5 ? 'var(--accent-green)' : (z < -1.5 ? 'var(--accent-red)' : 'var(--text-muted)');
                        const flowDesc = z > 2.0 ? 'Агрессивные покупки (+Flow)' : (z < -2.0 ? 'Агрессивные продажи (-Flow)' : 'Нейтральный баланс');
                        return `<tr>
                            <td style="font-weight:700;">${sym}</td>
                            <td>$${Number(item.mid_price || 0).toFixed(4)}</td>
                            <td>${item.spread_ticks || 1.0}</td>
                            <td>${(item.obi > 0 ? '+' : '')}${Number(item.obi || 0).toFixed(3)}</td>
                            <td style="color:${zColor}; font-weight:700;">${z > 0 ? '+' : ''}${z.toFixed(2)}</td>
                            <td><span style="font-size:11px; color:#cbd5e1;">${flowDesc}</span></td>
                        </tr>`;
                    }).join('');
                }

                // Positions
                const posBody = document.getElementById('posTableBody');
                const positions = Array.isArray(data.open_positions) ? data.open_positions : Object.values(data.open_positions || {});
                if (positions.length === 0) {
                    posBody.innerHTML = '<tr><td colspan="9" style="text-align:center; color: var(--text-muted);">Нет открытых позиций</td></tr>';
                } else {
                    posBody.innerHTML = positions.map(p => {
                        const isLong = p.direction === 'LONG' || p.side === 'Buy';
                        return `<tr>
                            <td style="font-weight:700;">${p.symbol}</td>
                            <td><span style="background:rgba(139,92,246,0.2); padding:2px 6px; border-radius:4px; font-size:11px;">${p.engine_id || 'OMS'}</span></td>
                            <td style="color:${isLong ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:700;">${isLong ? 'LONG' : 'SHORT'}</td>
                            <td>${p.qty}</td>
                            <td>$${Number(p.entry_price || 0).toFixed(4)}</td>
                            <td>$${Number(p.current_price || p.entry_price || 0).toFixed(4)}</td>
                            <td style="color:var(--accent-red);">$${Number(p.stop_loss || p.sl_price || 0).toFixed(4)}</td>
                            <td style="color:var(--accent-cyan);">$${Number(p.take_profit_1 || p.tp1_price || 0).toFixed(4)}</td>
                            <td>${p.opened_ms ? ((Date.now() - p.opened_ms)/1000).toFixed(1) : '0'}с</td>
                        </tr>`;
                    }).join('');
                }

                // Closed trades
                const tradeBody = document.getElementById('tradeTableBody');
                const trades = data.closed_trades || [];
                if (trades.length === 0) {
                    tradeBody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--text-muted);">История сделок пуста</td></tr>';
                } else {
                    tradeBody.innerHTML = trades.slice(0, 15).reverse().map(t => {
                        const pnl = Number(t.net_pnl_usdt !== undefined ? t.net_pnl_usdt : (t.net_pnl || 0.0));
                        const isProfit = pnl >= 0;
                        const isLong = t.side === 'Buy' || t.direction === 'LONG';
                        return `<tr>
                            <td style="font-weight:700;">${t.symbol}</td>
                            <td><span style="font-size:11px; color:#cbd5e1;">${t.engine_id || 'OMS'}</span></td>
                            <td style="color:${isLong ? 'var(--accent-green)' : 'var(--accent-red)'};">${isLong ? 'LONG' : 'SHORT'}</td>
                            <td>$${Number(t.entry_price || 0).toFixed(4)}</td>
                            <td>$${Number(t.exit_price || 0).toFixed(4)}</td>
                            <td>${Number(t.qty || t.initial_qty || 0).toFixed(2)}</td>
                            <td><span style="font-size:11px; color:#cbd5e1;">${t.exit_reason || t.reason || 'CLOSE'}</span></td>
                            <td style="color:${isProfit ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:700;">${isProfit ? '+' : ''}$${pnl.toFixed(3)}</td>
                        </tr>`;
                    }).join('');
                }

            } catch (err) {}
        }

        async function updateLogs() {
            try {
                const res = await fetch('/api/logs');
                if (!res.ok) return;
                const logs = await res.json();
                const logBox = document.getElementById('logBox');
                logBox.innerHTML = logs.map(l => `<div>${l}</div>`).join('');
                logBox.scrollTop = logBox.scrollHeight;
            } catch (err) {}
        }

        setInterval(updateDashboard, 2000);
        setInterval(updateLogs, 3000);
        updateDashboard();
        updateLogs();
    </script>
</body>
</html>
"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def _send_payload(self, payload: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path in ("/", "/index.html") or self.path.startswith("/?"):
            self._send_payload(HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/favicon.ico":
            self._send_payload(b"", "image/x-icon", 204)
        elif self.path in ("/api/status", "/api/state"):
            if core_scalper_v39.GLOBAL_OMS is not None:
                st = core_scalper_v39.GLOBAL_OMS.get_status()
            elif LIVE_STATUS_FILE.exists():
                try:
                    with open(LIVE_STATUS_FILE, "r", encoding="utf-8") as f:
                        st = json.load(f)
                except Exception:
                    st = {"status": "INITIALIZING", "equity": 300.0, "capital": 300.0}
            else:
                st = {"status": "INITIALIZING", "equity": 300.0, "capital": 300.0}
            payload = json.dumps(st, default=str).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")
        elif self.path == "/api/logs":
            payload = json.dumps(list(LOG_BUFFER)).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")
        else:
            self._send_payload(b"Not Found", "text/plain", 404)

    def log_message(self, format, *args):
        pass


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_scalper_async():
    asyncio.run(core_scalper_v39.main())


def run_server():
    t = threading.Thread(target=run_scalper_async, daemon=True)
    t.start()
    logger.info("Launched v3.9 Bybit Scalper async loop...")

    socketserver.TCPServer.allow_reuse_address = True
    server = ReusableThreadingTCPServer(("0.0.0.0", PORT), DashboardHandler)
    logger.info(f"{VERSION_NAME} live on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping dashboard...")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
