"""
Live Web Dashboard & Paper Trading Server v3.6 Omega Dual-Regime ZF-8 (Port 8089).
Features:
- Dual-Regime Quant Architecture:
  * Trend: 8-Speed Intelligent ZF Quant Transmission (G1-G8, Limp Mode at 8% DD)
  * Chop: IRAS Range Mean-Reversion Maker (KER <= 0.15, Bandwidth >= 3.0%)
- News Core v1.2 Defensive Shield (Macro Embargo [-45m : +30m] + 30% Trim)
- REST API: /api/status, /api/state, /api/logs, /api/radar
"""

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

try:
    from src.paper_trader_v36 import PaperTraderV36
    from src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE
except (ImportError, ValueError):
    from v3_6.src.paper_trader_v36 import PaperTraderV36
    from v3_6.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE

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

logger = logging.getLogger("WebDashboardV36")
GLOBAL_TRADER: PaperTraderV36 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.6 Omega | Dual-Regime ZF-8</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
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
            --accent-purple: #a855f7;
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
            background: linear-gradient(135deg, #f59e0b 0%, #ec4899 50%, #8b5cf6 100%);
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
        .val-sub { font-size: 12px; margin-top: 4px; font-family: 'JetBrains Mono', monospace; }
        .val-green { color: var(--accent-green); }
        .val-red { color: var(--accent-red); }
        .val-cyan { color: var(--accent-cyan); }
        .val-gold { color: var(--accent-gold); }

        /* Transmission Track */
        .gears-container {
            display: flex;
            gap: 8px;
            margin-top: 12px;
            flex-wrap: wrap;
        }
        .gear-slot {
            flex: 1;
            min-width: 90px;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px;
            text-align: center;
            transition: all 0.2s;
        }
        .gear-slot.active {
            background: linear-gradient(180deg, rgba(245, 158, 11, 0.15) 0%, rgba(245, 158, 11, 0.02) 100%);
            border-color: var(--accent-gold);
            box-shadow: 0 0 15px rgba(245, 158, 11, 0.25);
        }
        .gear-slot-tag { font-family: 'JetBrains Mono', monospace; font-size: 12px; font-weight: 800; }
        .gear-slot.active .gear-slot-tag { color: var(--accent-gold); }
        .gear-slot-risk { font-size: 10px; color: var(--text-muted); margin-top: 2px; }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            text-align: left;
            padding: 10px 14px;
            font-size: 11px;
            text-transform: uppercase;
            color: var(--text-muted);
            font-weight: 600;
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            font-family: 'JetBrains Mono', monospace;
        }
        tbody tr:hover { background: var(--bg-card-hover); }

        .log-box {
            background: #04060a;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            max-height: 220px;
            overflow-y: auto;
            color: #cbd5e1;
            line-height: 1.6;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <div class="header-title">
                <span class="badge">V3.6 OMEGA</span>
                <div>
                    <h1>Bybit Multi-Alpha v3.6 «Omega Dual-Regime ZF-8» (Port 8089)</h1>
                    <div class="subtitle">8-Speed Intelligent ZF Transmission (Trend) + IRAS Range Maker (Chop) + News Core v1.2</div>
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted);" id="live-clock">--:--:-- UTC</div>
                <div style="font-size: 11px; margin-top: 4px;">
                    <span id="ws-status" style="color: var(--accent-green);">● Bybit WS: OK</span> | 
                    <span id="shield-status" style="color: var(--accent-cyan);">🛡 News Core: v1.2</span>
                </div>
            </div>
        </div>

        <!-- Top Metrics Cards -->
        <div class="grid-4">
            <div class="card">
                <div class="card-label">Эквити портфеля <span>$300 База</span></div>
                <div class="card-value" id="val-equity">$300.00</div>
                <div class="val-sub val-green" id="val-pnl">+0.00 (+0.0%)</div>
            </div>

            <div class="card">
                <div class="card-label">АКПП ZF-8 Передача <span id="val-limp" style="color: var(--accent-green);">Normal</span></div>
                <div class="card-value val-gold" id="val-gear">G1</div>
                <div class="val-sub" id="val-gear-info">Риск 1.0% | Пирамидинг OFF</div>
            </div>

            <div class="card">
                <div class="card-label">Макс. просадка <span>Лимит 35.0%</span></div>
                <div class="card-value" id="val-dd">0.00%</div>
                <div class="val-sub" style="color: var(--text-muted);">Limp Mode триггер: 8.0%</div>
            </div>

            <div class="card">
                <div class="card-label">Режим BTC / Макро <span>Новости</span></div>
                <div class="card-value val-cyan" id="val-btc">NEUTRAL</div>
                <div class="val-sub" id="val-macro" style="color: var(--text-muted);">Эмбарго: НЕТ</div>
            </div>
        </div>

        <!-- Transmission Visual Track -->
        <div class="card" style="margin-bottom: 24px;">
            <div class="card-label">Интеллектуальная АКПП ZF-8: Текущая ступень и параметры риска</div>
            <div class="gears-container" id="gears-track">
                <!-- Rendered dynamically -->
            </div>
        </div>

        <!-- Open Positions Table -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">Открытые позиции (Trend Breakout + IRAS Range Maker)</h3>
                <span id="pos-count" style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--accent-gold);">0 позиций</span>
            </div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Символ</th>
                            <th>Режим</th>
                            <th>Сторона</th>
                            <th>Объем</th>
                            <th>Вход</th>
                            <th>Текущая</th>
                            <th>Стоп</th>
                            <th>Цель TP</th>
                            <th>Плав. PnL</th>
                        </tr>
                    </thead>
                    <tbody id="positions-body">
                        <tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет открытых позиций</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Radar -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">Квантовый радар силы и режимов (120h Donchian + KER)</h3>
                <span id="radar-count" style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-muted);">18 активов</span>
            </div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Символ</th>
                            <th>Режим</th>
                            <th>Цена</th>
                            <th>До Long (%)</th>
                            <th>До Short (%)</th>
                            <th>ADX 14</th>
                            <th>KER 14</th>
                            <th>RSI 14</th>
                        </tr>
                    </thead>
                    <tbody id="radar-body">
                        <tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">Загрузка радара...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- System Logs -->
        <div class="card">
            <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin-bottom: 12px;">Системный журнал событий v3.6 Omega</h3>
            <div class="log-box" id="log-box">Загрузка системных логов...</div>
        </div>
    </div>

    <script>
        const GEARS_DATA = [
            { gear: 1, name: "LIMP", risk: "1.0%", rr: "1:2.8-3.6", pyr: "OFF" },
            { gear: 2, name: "CITY", risk: "1.3%", rr: "1:3.0-4.0", pyr: "+20%" },
            { gear: 3, name: "CRUISE", risk: "1.6%", rr: "1:3.3-4.4", pyr: "+35%" },
            { gear: 4, name: "DRIVE", risk: "2.0%", rr: "1:3.6-4.8", pyr: "+50%" },
            { gear: 5, name: "FAST", risk: "2.4%", rr: "1:4.0-5.2", pyr: "+65%" },
            { gear: 6, name: "SPORT", risk: "2.8%", rr: "1:4.4-5.6", pyr: "+75%" },
            { gear: 7, name: "SPORT+", risk: "3.2%", rr: "1:4.8-6.0", pyr: "+85%" },
            { gear: 8, name: "LAUNCH", risk: "3.6%", rr: "1:5.2-6.5", pyr: "+100%" }
        ];

        function renderGearsTrack(activeGear) {
            const track = document.getElementById('gears-track');
            track.innerHTML = '';
            GEARS_DATA.forEach(g => {
                const isActive = (g.gear === activeGear);
                const el = document.createElement('div');
                el.className = 'gear-slot' + (isActive ? ' active' : '');
                el.innerHTML = `
                    <div class="gear-slot-tag">G${g.gear} ${g.name}</div>
                    <div class="gear-slot-risk">${g.risk} Risk</div>
                    <div style="font-size: 9px; color: #64748b; margin-top: 2px;">Pyr: ${g.pyr}</div>
                `;
                track.appendChild(el);
            });
        }

        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();

                const eq = Number(data.equity || 300.0);
                const init = Number(data.initial_capital || 300.0);
                const pnl = eq - init;
                const pnlPct = (pnl / init) * 100.0;

                document.getElementById('val-equity').textContent = '$' + eq.toFixed(2);
                const pnlEl = document.getElementById('val-pnl');
                pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) + ' (' + (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%)';
                pnlEl.className = 'val-sub ' + (pnl >= 0 ? 'val-green' : 'val-red');

                const gear = data.gear || 1;
                document.getElementById('val-gear').textContent = 'G' + gear;
                document.getElementById('val-gear-info').textContent = 'Риск ' + (data.gear_risk_pct || 1.0) + '% | ' + (data.gear_name || 'Normal');
                renderGearsTrack(gear);

                if (data.limp_mode) {
                    const lEl = document.getElementById('val-limp');
                    lEl.textContent = 'LIMP MODE ACTIVE';
                    lEl.style.color = 'var(--accent-red)';
                } else {
                    const lEl = document.getElementById('val-limp');
                    lEl.textContent = 'Normal';
                    lEl.style.color = 'var(--accent-green)';
                }

                document.getElementById('val-dd').textContent = (data.max_drawdown_pct || 0.0).toFixed(2) + '%';
                document.getElementById('val-btc').textContent = data.btc_trend_status || 'NEUTRAL';
                
                const macroEl = document.getElementById('val-macro');
                if (data.macro_blackout_active) {
                    macroEl.textContent = 'ЭМБАРГО: ' + (data.macro_event_name || 'Active');
                    macroEl.style.color = 'var(--accent-red)';
                } else {
                    macroEl.textContent = 'Эмбарго: НЕТ';
                    macroEl.style.color = 'var(--text-muted)';
                }

                // Render Open Positions
                const posBody = document.getElementById('positions-body');
                const positions = Object.values(data.open_positions || {});
                document.getElementById('pos-count').textContent = positions.length + ' позиций';

                if (positions.length === 0) {
                    posBody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет открытых позиций</td></tr>';
                } else {
                    posBody.innerHTML = '';
                    positions.forEach(p => {
                        const tr = document.createElement('tr');
                        const pnlVal = Number(p.unrealized_pnl || 0.0);
                        const pnlClass = pnlVal >= 0 ? 'val-green' : 'val-red';
                        const dirClass = p.direction === 'LONG' ? 'val-green' : 'val-red';
                        const modeTag = p.mode === 'TREND_BREAKOUT' 
                            ? '<span class="badge" style="background: rgba(245, 158, 11, 0.2); color: #f59e0b;">TREND G' + gear + '</span>'
                            : '<span class="badge" style="background: rgba(16, 185, 129, 0.2); color: #10b981;">IRAS MAKER</span>';

                        tr.innerHTML = `
                            <td style="font-weight: 700;">${p.symbol}</td>
                            <td>${modeTag}</td>
                            <td class="${dirClass}">${p.direction}</td>
                            <td>${Number(p.current_qty).toFixed(4)}</td>
                            <td>$${Number(p.entry_price).toFixed(4)}</td>
                            <td>$${Number(p.current_price).toFixed(4)}</td>
                            <td>$${Number(p.current_stop).toFixed(4)}</td>
                            <td>${p.take_profit ? '$' + Number(p.take_profit).toFixed(4) : '--'}</td>
                            <td class="${pnlClass}">${pnlVal >= 0 ? '+' : ''}$${pnlVal.toFixed(2)}</td>
                        `;
                        posBody.appendChild(tr);
                    });
                }

                // Render Radar
                const radarBody = document.getElementById('radar-body');
                const radar = Object.entries(data.radar || {});
                document.getElementById('radar-count').textContent = radar.length + ' активов';
                if (radar.length > 0) {
                    radarBody.innerHTML = '';
                    radar.sort((a, b) => Math.min(a[1].dist_to_long_pct, a[1].dist_to_short_pct) - Math.min(b[1].dist_to_long_pct, b[1].dist_to_short_pct));
                    radar.forEach(([sym, r]) => {
                        const tr = document.createElement('tr');
                        const regBadge = r.regime === 'TREND_LONG' 
                            ? '<span class="badge" style="background: rgba(16, 185, 129, 0.2); color: #10b981;">TREND LONG</span>'
                            : r.regime === 'TREND_SHORT'
                            ? '<span class="badge" style="background: rgba(244, 63, 94, 0.2); color: #f43f5e;">TREND SHORT</span>'
                            : r.regime === 'CHOP'
                            ? '<span class="badge" style="background: rgba(245, 158, 11, 0.2); color: #f59e0b;">CHOP (IRAS)</span>'
                            : '<span style="color: #64748b;">NEUTRAL</span>';

                        tr.innerHTML = `
                            <td style="font-weight: 700;">${sym}</td>
                            <td>${regBadge}</td>
                            <td>$${Number(r.current_price).toFixed(4)}</td>
                            <td>${r.dist_to_long_pct}%</td>
                            <td>${r.dist_to_short_pct}%</td>
                            <td>${r.adx}</td>
                            <td style="color: ${r.ker <= 0.15 ? '#10b981' : '#f59e0b'};">${r.ker}</td>
                            <td>${r.rsi}</td>
                        `;
                        radarBody.appendChild(tr);
                    });
                }

            } catch (err) {
                console.error("Dashboard update error:", err);
            }
        }

        async function updateLogs() {
            try {
                const res = await fetch('/api/logs');
                if (res.ok) {
                    const logs = await res.json();
                    const box = document.getElementById('log-box');
                    box.innerHTML = logs.slice(-50).map(l => `<div>${l}</div>`).join('');
                    box.scrollTop = box.scrollHeight;
                }
            } catch (err) {}
        }

        setInterval(() => {
            const now = new Date();
            document.getElementById('live-clock').textContent = now.toUTCString().split(' ')[4] + ' UTC';
        }, 1000);

        renderGearsTrack(1);
        updateDashboard();
        updateLogs();
        setInterval(updateDashboard, 2000);
        setInterval(updateLogs, 4000);
    </script>
</body>
</html>
"""


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ["/", "/index.html"]:
            content = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        elif path in ["/api/status", "/api/state"]:
            if GLOBAL_TRADER and getattr(GLOBAL_TRADER, "last_status_cache", None):
                resp = json.dumps(GLOBAL_TRADER.last_status_cache, indent=2).encode("utf-8")
            elif LIVE_STATUS_FILE.exists():
                try:
                    resp = LIVE_STATUS_FILE.read_bytes()
                except Exception:
                    resp = b"{}"
            else:
                resp = b"{}"

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        elif path == "/api/logs":
            logs = list(LOG_BUFFER)
            resp = json.dumps(logs).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        else:
            self.send_error(404, "Endpoint not found")

    def log_message(self, format, *args):
        return


def start_server_and_bot():
    global GLOBAL_TRADER
    logger.info(f"Initializing PaperTraderV36 daemon on Port {PORT}...")
    GLOBAL_TRADER = PaperTraderV36()

    bot_thread = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    bot_thread.start()
    logger.info("Background PaperTraderV36 thread launched.")

    server_address = ("0.0.0.0", PORT)
    with socketserver.ThreadingTCPServer(server_address, DashboardHTTPRequestHandler) as httpd:
        logger.info(f"Multi-Alpha v3.6 Omega (Port {PORT}) Web Dashboard live on http://127.0.0.1:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server interrupted. Shutting down...")
            httpd.server_close()


if __name__ == "__main__":
    start_server_and_bot()
