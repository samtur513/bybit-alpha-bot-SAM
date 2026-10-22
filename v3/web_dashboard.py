"""
Live Web Dashboard & Paper Trading Server v3.0 (Port 8082).
Features:
- Port 8082 for parallel side-by-side A/B/C testing with v2.0 (8080) and v2.1 (8081)
- Dynamic Universe Radar (Top Bybit Linear Perpetuals ranked by Multi-Factor Momentum)
- Live BTC Delta Market Status badge
- News Core v1.1 Defensive Shield status badge & webhook (/api/news_event)
- REST API: /api/status, /api/state, /api/universe, /api/logs
"""

import collections
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
PARENT_DIR = BASE_DIR.parent
for p in [str(PARENT_DIR), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v3.src.paper_trader_v3 import PaperTraderV3
from v3.src.config import LIVE_STATUS_FILE, PERSISTENT_STATE_FILE, PORT

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

logger = logging.getLogger("WebDashboardV3")
GLOBAL_TRADER: PaperTraderV3 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.0 | Dynamic Universe & News Shield</title>
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
            --accent-yellow: #f59e0b;
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
        .container { max-width: 1440px; margin: 0 auto; }
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
        .title-block h1 {
            font-size: 26px;
            font-weight: 800;
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-purple));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .tag-v3 {
            background: rgba(0, 242, 254, 0.15);
            border: 1px solid var(--accent-cyan);
            color: var(--accent-cyan);
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 6px;
            text-transform: uppercase;
        }
        .header-meta {
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
        }
        .badge {
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-main);
        }
        .countdown-box {
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 8px 14px;
            border-radius: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            color: var(--accent-yellow);
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .metric-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            transition: all 0.2s ease;
        }
        .metric-card:hover {
            border-color: var(--accent-blue);
            transform: translateY(-2px);
        }
        .metric-title {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 8px;
        }
        .metric-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 22px;
            font-weight: 700;
            color: var(--text-main);
        }
        .val-green { color: var(--accent-green); }
        .val-red { color: var(--accent-red); }
        .section-header {
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-card);
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--border);
            margin-bottom: 24px;
        }
        th, td {
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
            font-size: 13px;
        }
        th {
            background: #0b1120;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }
        tbody tr:hover { background: var(--bg-card-hover); }
        .pos-badge {
            font-size: 11px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 4px;
            text-transform: uppercase;
        }
        .badge-long { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); }
        .badge-short { background: rgba(244, 63, 94, 0.2); color: var(--accent-red); }
        .badge-cooldown { background: rgba(245, 158, 11, 0.2); color: var(--accent-yellow); font-size: 10px; padding: 2px 6px; border-radius: 4px; }
        .footer-note {
            text-align: center;
            color: var(--text-muted);
            font-size: 12px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-block">
                <h1>Bybit Multi-Alpha v3.0 <span class="tag-v3">Port 8082</span></h1>
            </div>
            <div class="header-meta">
                <div class="badge" id="news-shield-status">🛡️ Новостной Щит: Инициализация...</div>
                <div class="badge" id="btc-filter-status">BTC Тренд: --</div>
                <div class="badge" id="universe-badge">🌐 Dynamic Top 25 Universe</div>
                <div class="countdown-box">4H Бар: <span id="candle-timer">--</span></div>
            </div>
        </header>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-title">Капитал (USDT)</div>
                <div class="metric-value" id="metric-capital">$300.00</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Чистый PnL</div>
                <div class="metric-value" id="metric-pnl">$0.00</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Просадка (Max DD)</div>
                <div class="metric-value" id="metric-dd">0.00%</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Винрейт</div>
                <div class="metric-value" id="metric-wr">0.0%</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Открытых позиций</div>
                <div class="metric-value" id="metric-open-count">0 / 4</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Сделок завершено</div>
                <div class="metric-value" id="metric-trades-count">0</div>
            </div>
        </div>

        <div class="section-header">
            <span>Открытые позиции (4h Trend Breakout)</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Монета</th>
                    <th>Направление</th>
                    <th>Объём</th>
                    <th>Вход ($)</th>
                    <th>Текущая ($)</th>
                    <th>Стоп ($)</th>
                    <th>Unr. PnL ($)</th>
                    <th>Пирамидинг</th>
                </tr>
            </thead>
            <tbody id="positions-body">
                <tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Загрузка открытых позиций...</td></tr>
            </tbody>
        </table>

        <div class="section-header">
            <span>Радар рынка и Динамический скринер монет (Top Bybit Linear Perpetuals)</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Монета</th>
                    <th>Текущая цена</th>
                    <th>До Long (+30b)</th>
                    <th>До Short (-30b)</th>
                    <th>ATR (4h)</th>
                    <th>Объём к SMA</th>
                    <th>Тренд EMA</th>
                    <th>Статус</th>
                </tr>
            </thead>
            <tbody id="radar-body">
                <tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Загрузка радара рынка...</td></tr>
            </tbody>
        </table>

        <div class="footer-note">
            Multi-Alpha Growth Engine v3.0 • Dynamic Universe Selection • 4h Breakout • News Core v1.1 Shield • Penny-Perfect Accounting
        </div>
    </div>

    <script>
        function formatMoney(num) {
            return '$' + Number(num).toFixed(2);
        }

        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();

                document.getElementById('metric-capital').innerText = formatMoney(data.capital);
                const pnlEl = document.getElementById('metric-pnl');
                const pnl = Number(data.total_net_pnl);
                pnlEl.innerText = (pnl >= 0 ? '+' : '') + formatMoney(pnl);
                pnlEl.className = 'metric-value ' + (pnl >= 0 ? 'val-green' : 'val-red');

                document.getElementById('metric-dd').innerText = Number(data.drawdown_pct).toFixed(2) + '%';
                document.getElementById('metric-wr').innerText = Number(data.win_rate_pct).toFixed(1) + '%';
                document.getElementById('metric-open-count').innerText = data.open_positions_count + ' / 4';
                document.getElementById('metric-trades-count').innerText = data.closed_trades_count;

                if (data.news_defense_active) {
                    document.getElementById('news-shield-status').innerText = '🛡️ Новостной Щит: Defensive Only';
                } else {
                    document.getElementById('news-shield-status').innerText = '⚪ Новостной Щит: Отключен';
                }

                if (data.btc_trend_status) {
                    document.getElementById('btc-filter-status').innerText = 'BTC Тренд: ' + data.btc_trend_status;
                }

                if (data.monitored_symbols_count) {
                    document.getElementById('universe-badge').innerText = '🌐 Dynamic Universe: ' + data.monitored_symbols_count + ' монет';
                }

                const sec = data.next_candle_seconds || 0;
                const h = Math.floor(sec / 3600);
                const m = Math.floor((sec % 3600) / 60);
                const s = sec % 60;
                document.getElementById('candle-timer').innerText = `${h}ч ${m}м ${s < 10 ? '0' : ''}${s}с`;

                const pBody = document.getElementById('positions-body');
                const posKeys = Object.keys(data.open_positions || {});
                if (posKeys.length === 0) {
                    pBody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Нет открытых позиций. Динамический сканер активен.</td></tr>';
                } else {
                    let html = '';
                    for (const sym of posKeys) {
                        const p = data.open_positions[sym];
                        const unr = Number(p.unrealized_pnl);
                        const unrClass = unr >= 0 ? 'val-green' : 'val-red';
                        const badgeClass = p.direction === 'LONG' ? 'badge-long' : 'badge-short';
                        html += `<tr>
                            <td style="font-weight:700;">${sym}</td>
                            <td><span class="pos-badge ${badgeClass}">${p.direction}</span></td>
                            <td>${p.qty}</td>
                            <td>$${Number(p.entry_price).toFixed(4)}</td>
                            <td>$${Number(p.current_price).toFixed(4)}</td>
                            <td>$${Number(p.current_stop).toFixed(4)}</td>
                            <td class="${unrClass}" style="font-weight:700;">${unr >= 0 ? '+' : ''}$${unr.toFixed(2)}</td>
                            <td>${p.is_pyramided ? '🚀 Доливка +50%' : 'Ожидание +2 ATR'}</td>
                        </tr>`;
                    }
                    pBody.innerHTML = html;
                }

                const rBody = document.getElementById('radar-body');
                const rKeys = Object.keys(data.radar || {});
                if (rKeys.length > 0) {
                    let html = '';
                    for (const sym of rKeys) {
                        const r = data.radar[sym];
                        const coolBadge = r.is_cooldown ? ' <span class="badge-cooldown">Cooldown 3b</span>' : '';
                        html += `<tr>
                            <td style="font-weight:700;">${sym}</td>
                            <td>$${Number(r.current_price).toFixed(4)}</td>
                            <td style="color:${r.dist_to_long_pct < 2 ? 'var(--accent-green)' : 'inherit'}; font-weight:${r.dist_to_long_pct < 2 ? '700' : 'normal'}">${r.dist_to_long_pct}%</td>
                            <td style="color:${r.dist_to_short_pct < 2 ? 'var(--accent-red)' : 'inherit'}; font-weight:${r.dist_to_short_pct < 2 ? '700' : 'normal'}">${r.dist_to_short_pct}%</td>
                            <td>${r.atr}</td>
                            <td style="color:${r.volume_ratio >= 1.2 ? 'var(--accent-cyan)' : 'inherit'}">${r.volume_ratio}x</td>
                            <td style="font-size:11px; color:var(--text-muted);">${r.trend}</td>
                            <td>${r.is_in_position ? '<span class="pos-badge badge-long">В позиции</span>' : (coolBadge || '<span style="color:var(--text-muted);">Готов</span>')}</td>
                        </tr>`;
                    }
                    rBody.innerHTML = html;
                }

            } catch (e) {
                console.error("Dashboard update failed:", e);
            }
        }

        setInterval(updateDashboard, 3000);
        updateDashboard();
    </script>
</body>
</html>
"""


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            payload = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/status":
            if GLOBAL_TRADER and getattr(GLOBAL_TRADER, "last_status_cache", None):
                payload = json.dumps(GLOBAL_TRADER.last_status_cache, indent=2).encode("utf-8")
            elif LIVE_STATUS_FILE.exists():
                try:
                    payload = LIVE_STATUS_FILE.read_bytes()
                except Exception:
                    payload = b"{}"
            else:
                payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/state":
            if GLOBAL_TRADER and getattr(GLOBAL_TRADER, "engine", None):
                try:
                    data = GLOBAL_TRADER.engine.to_dict()
                    payload = json.dumps(data, indent=2).encode("utf-8")
                except Exception:
                    payload = b"{}"
            elif PERSISTENT_STATE_FILE.exists():
                try:
                    payload = PERSISTENT_STATE_FILE.read_bytes()
                except Exception:
                    payload = b"{}"
            else:
                payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/universe":
            universe_data = []
            if GLOBAL_TRADER and GLOBAL_TRADER.screened_universe:
                universe_data = [
                    {
                        "symbol": a.symbol,
                        "score": round(a.composite_score, 4),
                        "rank": idx,
                        "turnover_24h": round(a.turnover_24h, 2),
                        "open_interest": round(a.open_interest, 2),
                        "norm_atr": round(a.norm_atr, 4),
                        "adx": round(a.adx, 2),
                        "rel_strength": round(a.relative_strength, 4),
                        "funding_rate": round(a.funding_rate, 6),
                    }
                    for idx, a in enumerate(GLOBAL_TRADER.screened_universe, 1)
                ]
            payload = json.dumps(universe_data, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/logs":
            payload = json.dumps(list(LOG_BUFFER), indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/news_event":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                headline = data.get("headline", "")
                sym = data.get("symbol")
                tier = int(data.get("source_tier", 3))
                ret_1m = float(data.get("ret_1m", 0.0))
                vol_1m = float(data.get("vol_1m", 0.0))
                vol_med = float(data.get("vol_median_60m", 1.0))

                actions = []
                if GLOBAL_TRADER:
                    actions = GLOBAL_TRADER.handle_news_event(
                        headline=headline,
                        symbol=sym,
                        source_tier=tier,
                        ret_1m=ret_1m,
                        vol_1m=vol_1m,
                        vol_median_60m=vol_med,
                    )
                resp = json.dumps({
                    "status": "ok",
                    "actions_count": len(actions),
                    "actions": [
                        {
                            "action_type": a.action_type.value,
                            "symbol": a.symbol,
                            "side": a.side.value,
                            "reason": a.reason,
                            "stop_price": a.stop_price,
                        }
                        for a in actions
                    ]
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                logger.error(f"Failed to process /api/news_event: {e}")
                self.send_error(400, f"Error processing news event: {e}")
        else:
            self.send_error(403, "Mutation endpoints disabled on public dashboard")

    def log_message(self, format, *args):
        return


def start_server_and_bot():
    global GLOBAL_TRADER
    logger.info("Initializing PaperTraderV3 daemon...")
    GLOBAL_TRADER = PaperTraderV3(poll_interval_seconds=5)

    bot_thread = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    bot_thread.start()
    logger.info("Background PaperTraderV3 thread launched.")

    class SupervisedServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

        def service_actions(self):
            if not bot_thread.is_alive():
                raise SystemExit("Trading worker stopped; restarting service")

    with SupervisedServer(("0.0.0.0", PORT), DashboardHTTPRequestHandler) as server:
        logger.info(f"Serving v3.0 web dashboard at http://0.0.0.0:{PORT}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    start_server_and_bot()
