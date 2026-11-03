"""
Live Web Dashboard & Paper Trading Server v3.3 Turbo (Port 8086).
Features:
- Streak-Based Adaptive Risk & Dynamic R:R (1:3 -> 1:4 -> 1:5) Live Telemetry
- Dynamic Universe Radar (Top 25 screened Bybit linear perpetuals)
- News Core v1.2 Defensive Shield
- Anti-Martingale Pyramiding (+0% Gear 1, +50% Gear 2, +75% Gear 3)
- REST API: /api/status, /api/universe, /api/logs
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

from v3_3.src.paper_trader_v33 import PaperTraderV33
from v3_3.src.config import PORT, VERSION_NAME

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

logger = logging.getLogger("WebDashboardV33")
GLOBAL_TRADER: PaperTraderV33 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.3 Turbo | Streak-Based Dynamic Risk (1:3 -> 1:5)</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-cyan: #00f2fe;
            --accent-blue: #38bdf8;
            --accent-green: #10b981;
            --accent-red: #f43f5e;
            --accent-amber: #f59e0b;
            --accent-purple: #a855f7;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            padding: 24px;
        }
        .container { max-width: 1560px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 16px;
        }
        .logo-group {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .logo-badge {
            background: linear-gradient(135deg, #f59e0b 0%, #ef4444 100%);
            color: #080c14;
            font-weight: 800;
            font-size: 13px;
            padding: 6px 12px;
            border-radius: 6px;
            letter-spacing: 1px;
            font-family: 'JetBrains Mono', monospace;
            box-shadow: 0 0 15px rgba(245, 158, 11, 0.4);
        }
        .title { font-size: 20px; font-weight: 800; }
        .subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
        
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background-color: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px 20px;
            position: relative;
            overflow: hidden;
        }
        .card-label {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }
        .card-val {
            font-size: 24px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
        }
        .card-sub {
            font-size: 12px;
            margin-top: 6px;
            color: var(--text-muted);
        }

        .val-green { color: var(--accent-green); }
        .val-red { color: var(--accent-red); }
        .val-amber { color: var(--accent-amber); }
        .val-cyan { color: var(--accent-cyan); }

        /* Gearbox Banner */
        .gearbox-banner {
            background: linear-gradient(135deg, rgba(245, 158, 11, 0.1) 0%, rgba(15, 23, 42, 0.8) 100%);
            border: 1px solid rgba(245, 158, 11, 0.3);
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 16px;
        }
        .gear-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 8px;
            font-weight: 700;
            font-size: 13px;
            font-family: 'JetBrains Mono', monospace;
        }
        .gear-1 { background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); border: 1px solid rgba(56, 189, 248, 0.3); }
        .gear-2 { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.3); }
        .gear-3 { background: rgba(245, 158, 11, 0.2); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.5); box-shadow: 0 0 12px rgba(245, 158, 11, 0.3); }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            text-align: left;
            padding: 12px 14px;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(30, 41, 59, 0.5);
            font-family: 'JetBrains Mono', monospace;
        }
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
        }
        .badge-long { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); }
        .badge-short { background: rgba(244, 63, 94, 0.15); color: var(--accent-red); }

        .log-box {
            background-color: #050811;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            height: 220px;
            overflow-y: auto;
            color: #94a3b8;
            line-height: 1.5;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-group">
                <div class="logo-badge">V3.3 TURBO</div>
                <div>
                    <div class="title">Bybit Multi-Alpha v3.3 Turbo (Port 8086)</div>
                    <div class="subtitle">Streak-Based Adaptive Risk & Dynamic R:R (1:3 → 1:4 → 1:5)</div>
                </div>
            </div>
            <div style="font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;" id="clock-utc">
                --:--:-- UTC
            </div>
        </header>

        <!-- Gearbox Banner -->
        <div class="gearbox-banner">
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px;">Активный режим риска</div>
                <div id="gearbox-pill" class="gear-pill gear-1">⚙️ GEAR 1: PROTECTIVE (1:3)</div>
            </div>
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px;">Серия побед / поражений</div>
                <div style="font-size: 15px; font-weight: 700;" id="streak-counter">🔥 0 побед подряд</div>
            </div>
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px;">Целевой каскад R:R</div>
                <div style="font-size: 15px; font-weight: 700; color: var(--accent-amber);" id="rr-target-label">1:3.0 — 1:4.0</div>
            </div>
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px;">Пирамидинг в тренде</div>
                <div style="font-size: 15px; font-weight: 700;" id="pyramid-status">Выключен (0%)</div>
            </div>
        </div>

        <!-- Metrics Grid -->
        <div class="stats-grid">
            <div class="card">
                <div class="card-label">Эквити портфеля</div>
                <div class="card-val val-cyan" id="equity-val">$300.00</div>
                <div class="card-sub" id="pnl-sub">Чистый PnL: $0.00 (0.00%)</div>
            </div>
            <div class="card">
                <div class="card-label">Риск на сделку (1R)</div>
                <div class="card-val val-amber" id="risk-val">1.5%</div>
                <div class="card-sub" id="risk-sub">$4.50 макс. риск</div>
            </div>
            <div class="card">
                <div class="card-label">Макс. просадка</div>
                <div class="card-val" id="drawdown-val">0.00%</div>
                <div class="card-sub">Лимит: 30.0% (Cut at 10%)</div>
            </div>
            <div class="card">
                <div class="card-label">Винрейт / Сделки</div>
                <div class="card-val" id="winrate-val">--%</div>
                <div class="card-sub" id="trades-sub">0 сделок закрыто</div>
            </div>
        </div>

        <!-- Open Positions -->
        <div class="card" style="margin-bottom: 24px;">
            <div class="card-label" style="display: flex; justify-content: space-between;">
                <span>Открытые позиции</span>
                <span id="pos-count">0 позиций</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Символ</th>
                        <th>Сторона</th>
                        <th>Объем</th>
                        <th>Вход</th>
                        <th>Текущая</th>
                        <th>Стоп-лосс</th>
                        <th>Цель TP1 (BE)</th>
                        <th>Цель TP2</th>
                        <th>Плавающий PnL</th>
                        <th>Передача</th>
                    </tr>
                </thead>
                <tbody id="positions-body">
                    <tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет открытых позиций</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Logs -->
        <div class="card">
            <div class="card-label">Системный журнал событий v3.3</div>
            <div class="log-box" id="logs-box">Загрузка логов...</div>
        </div>
    </div>

    <script>
        async function updateStatus() {
            try {
                const resp = await fetch('/api/status');
                const data = await resp.json();

                document.getElementById('clock-utc').textContent = data.datetime_utc || new Date().toISOString();
                document.getElementById('equity-val').textContent = '$' + (data.capital || 300.0).toFixed(2);
                
                const pnl = data.total_net_pnl || 0.0;
                const pnlSign = pnl >= 0 ? '+' : '';
                const pnlEl = document.getElementById('pnl-sub');
                pnlEl.textContent = `Чистый PnL: ${pnlSign}$${pnl.toFixed(2)}`;
                pnlEl.className = pnl >= 0 ? 'card-sub val-green' : 'card-sub val-red';

                const dd = data.drawdown_pct || 0.0;
                document.getElementById('drawdown-val').textContent = dd.toFixed(2) + '%';
                document.getElementById('winrate-val').textContent = (data.win_rate_pct || 0.0).toFixed(1) + '%';
                document.getElementById('trades-sub').textContent = (data.closed_trades_count || 0) + ' сделок закрыто';

                // Gearbox
                const gear = data.gearbox_gear || 1;
                const riskPct = data.gearbox_risk_pct || 1.5;
                const riskUsd = ((data.capital || 300.0) * riskPct / 100).toFixed(2);
                document.getElementById('risk-val').textContent = riskPct.toFixed(1) + '%';
                document.getElementById('risk-sub').textContent = `~$${riskUsd} макс. риск`;

                const gearPill = document.getElementById('gearbox-pill');
                gearPill.className = `gear-pill gear-${gear}`;
                gearPill.textContent = `⚙️ GEAR ${gear}: ${data.gear_name || 'PROTECTIVE'}`;

                const wins = data.streak_wins || 0;
                const losses = data.streak_losses || 0;
                if (wins > 0) {
                    document.getElementById('streak-counter').textContent = `🔥 ${wins} побед подряд`;
                    document.getElementById('streak-counter').className = 'val-green';
                } else if (losses > 0) {
                    document.getElementById('streak-counter').textContent = `❄️ ${losses} убытков подряд`;
                    document.getElementById('streak-counter').className = 'val-red';
                } else {
                    document.getElementById('streak-counter').textContent = '🛡️ Нейтральная серия';
                    document.getElementById('streak-counter').className = '';
                }

                document.getElementById('rr-target-label').textContent = data.rr_target || '1:3.0 — 1:4.0';
                const pyrRatio = data.pyramiding_ratio ? `+${(data.pyramiding_ratio * 100).toFixed(0)}%` : 'Выключен (0%)';
                document.getElementById('pyramid-status').textContent = pyrRatio;

                // Positions
                const posBody = document.getElementById('positions-body');
                const positions = data.open_positions || {};
                const keys = Object.keys(positions);
                document.getElementById('pos-count').textContent = keys.length + ' позиций';

                if (keys.length === 0) {
                    posBody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет открытых позиций</td></tr>';
                } else {
                    posBody.innerHTML = '';
                    keys.forEach(sym => {
                        const p = positions[sym];
                        const row = document.createElement('tr');
                        const sideBadge = p.direction === 'LONG' || p.direction === 1 ? '<span class="badge badge-long">LONG</span>' : '<span class="badge badge-short">SHORT</span>';
                        const pnl = p.unrealized_pnl || 0.0;
                        const pnlSign = pnl >= 0 ? '+' : '';
                        const pnlClass = pnl >= 0 ? 'val-green' : 'val-red';
                        row.innerHTML = `
                            <td><strong>${sym}</strong></td>
                            <td>${sideBadge}</td>
                            <td>${p.qty}</td>
                            <td>$${Number(p.entry_price).toFixed(4)}</td>
                            <td>$${Number(p.current_price || p.entry_price).toFixed(4)}</td>
                            <td class="val-red">$${Number(p.current_stop).toFixed(4)}</td>
                            <td class="val-cyan">+${p.tp1_r_mult || 3.0}R</td>
                            <td class="val-green">+${p.tp2_r_mult || 4.0}R</td>
                            <td class="${pnlClass}"><strong>${pnlSign}$${pnl.toFixed(2)}</strong></td>
                            <td><span class="badge" style="background: rgba(245, 158, 11, 0.15); color: var(--accent-amber);">G${p.entry_gear || 1}</span></td>
                        `;
                        posBody.appendChild(row);
                    });
                }
            } catch (e) {
                console.error("Status fetch error:", e);
            }
        }

        async function updateLogs() {
            try {
                const resp = await fetch('/api/logs');
                const logs = await resp.json();
                const box = document.getElementById('logs-box');
                box.innerHTML = logs.slice(-50).map(l => `<div>${l}</div>`).join('');
                box.scrollTop = box.scrollHeight;
            } catch (e) {}
        }

        setInterval(updateStatus, 2000);
        setInterval(updateLogs, 4000);
        updateStatus();
        updateLogs();
    </script>
</body>
</html>
"""


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            content = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        elif path == "/api/status":
            status = {}
            if GLOBAL_TRADER:
                status = GLOBAL_TRADER.get_status_dict()
            resp = json.dumps(status).encode("utf-8")
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

        elif path == "/api/universe":
            univ = []
            if GLOBAL_TRADER and hasattr(GLOBAL_TRADER, "screened_universe"):
                univ = [
                    {
                        "symbol": c.symbol,
                        "turnover24h": float(c.turnover_24h),
                        "open_interest": float(c.open_interest),
                        "norm_atr": float(c.norm_atr),
                        "ker": float(c.ker),
                        "relative_strength": float(c.relative_strength),
                        "composite_score": float(c.composite_score),
                    }
                    for c in GLOBAL_TRADER.screened_universe
                ]
            resp = json.dumps(univ).encode("utf-8")
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
    logger.info("Initializing PaperTraderV33 daemon...")
    GLOBAL_TRADER = PaperTraderV33()

    bot_thread = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    bot_thread.start()
    logger.info("Background PaperTraderV33 thread launched.")

    server_address = ("0.0.0.0", PORT)
    with socketserver.ThreadingTCPServer(server_address, DashboardHTTPRequestHandler) as httpd:
        logger.info(f"Multi-Alpha v3.3 Turbo (Port {PORT}) Web Dashboard live on http://127.0.0.1:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server interrupted. Shutting down...")
            GLOBAL_TRADER.stop()
            httpd.server_close()


if __name__ == "__main__":
    start_server_and_bot()
