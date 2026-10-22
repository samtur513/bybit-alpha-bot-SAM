"""
Live Web Dashboard & Paper Trading Server v2.1 (Port 8081).
Features:
- Port 8081 for parallel side-by-side A/B testing with v2.0
- Live BTC Delta Market Status badge
- Cooldown indicators in radar table
- REST API: /api/status, /api/state, /api/logs
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
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from v2_1.src.paper_trader_v2 import PaperTraderV21
from v2_1.src.config import LIVE_STATUS_FILE, PERSISTENT_STATE_FILE, PORT

LOG_BUFFER = collections.deque(maxlen=250)

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

logger = logging.getLogger("WebDashboardV21")
GLOBAL_TRADER: PaperTraderV21 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v2.1 | A/B Testing Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #0a0e17;
            --bg-card: #121826;
            --bg-card-hover: #1a2235;
            --accent-cyan: #00f2fe;
            --accent-blue: #4facfe;
            --accent-green: #00e676;
            --accent-red: #ff3366;
            --accent-yellow: #ffd600;
            --accent-purple: #b388ff;
            --text-main: #f0f4fc;
            --text-muted: #8b9bb4;
            --border: #1f293d;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            padding: 24px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }
        .title-block h1 {
            font-size: 26px;
            font-weight: 800;
            background: linear-gradient(135deg, var(--accent-purple), var(--accent-cyan));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .tag-ab {
            background: rgba(179, 136, 255, 0.15);
            border: 1px solid var(--accent-purple);
            color: var(--accent-purple);
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 6px;
            text-transform: uppercase;
        }
        .header-meta {
            display: flex;
            gap: 16px;
            align-items: center;
        }
        .market-filter-badge {
            background: #151d2f;
            border: 1px solid var(--border);
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-main);
        }
        .countdown-box {
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 8px 16px;
            border-radius: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            color: var(--accent-yellow);
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .metric-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
            transition: transform 0.2s;
        }
        .metric-card:hover { transform: translateY(-2px); border-color: var(--border-hover); }
        .metric-label {
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 6px;
        }
        .metric-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 24px;
            font-weight: 700;
        }
        .val-green { color: var(--accent-green); }
        .val-red { color: var(--accent-red); }
        .val-cyan { color: var(--accent-cyan); }
        .val-yellow { color: var(--accent-yellow); }
        
        .section-title {
            font-size: 18px;
            font-weight: 700;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
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
            background: #161e31;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }
        tr:hover { background: var(--bg-card-hover); }
        .pos-badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 11px;
        }
        .badge-long { background: rgba(0, 230, 118, 0.15); color: var(--accent-green); }
        .badge-short { background: rgba(255, 51, 102, 0.15); color: var(--accent-red); }
        .badge-cooldown { background: rgba(255, 214, 0, 0.15); color: var(--accent-yellow); font-size: 10px; padding: 2px 6px; border-radius: 4px; }
        .footer-note {
            text-align: center;
            color: var(--text-muted);
            font-size: 12px;
            margin-top: 30px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-block">
                <h1>Bybit Multi-Alpha v2.1 <span class="tag-ab">A/B Эксперимент</span></h1>
                <p style="color: var(--text-muted); font-size: 13px; margin-top: 4px;">Фильтр дельты BTC • Новостной щит v1.1 • 4h Таймфрейм • Комиссия MNT 0.09% • Порт 8081</p>
            </div>
            <div class="header-meta">
                <div class="market-filter-badge" id="news-shield-status" style="background: rgba(0, 229, 255, 0.12); color: var(--accent-cyan); border-color: rgba(0, 229, 255, 0.3);">🛡️ Новостной Щит: Активен</div>
                <div class="market-filter-badge" id="btc-filter-status">Загрузка тренда BTC...</div>
                <div class="countdown-box">До 4h свечи: <span id="candle-timer">--:--</span></div>
            </div>
        </header>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Текущий Капитал</div>
                <div class="metric-value val-cyan" id="metric-capital">$300.00</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Чистый PnL</div>
                <div class="metric-value val-green" id="metric-pnl">+$0.00</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Макс. Просадка</div>
                <div class="metric-value val-yellow" id="metric-dd">0.00%</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Винрейт</div>
                <div class="metric-value" id="metric-wr">0.0%</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Открытых Позиций</div>
                <div class="metric-value val-yellow" id="metric-open-count">0 / 4</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Закрытых Сделок</div>
                <div class="metric-value" id="metric-trades-count">0</div>
            </div>
        </div>

        <div class="section-title">📊 Открытые позиции (v2.1)</div>
        <table id="positions-table">
            <thead>
                <tr>
                    <th>Монета</th>
                    <th>Сторона</th>
                    <th>Объём</th>
                    <th>Вход ($)</th>
                    <th>Текущая ($)</th>
                    <th>Стоп ($)</th>
                    <th>Unr. PnL ($)</th>
                    <th>Статус</th>
                </tr>
            </thead>
            <tbody id="positions-body">
                <tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Нет открытых позиций. Сканер активен.</td></tr>
            </tbody>
        </table>

        <div class="section-title">🎯 Радар рынка (18 монет Bybit)</div>
        <table id="radar-table">
            <thead>
                <tr>
                    <th>Символ</th>
                    <th>Цена ($)</th>
                    <th>До Лонга (%)</th>
                    <th>До Шорта (%)</th>
                    <th>ATR</th>
                    <th>Объём к SMA20</th>
                    <th>Тренд</th>
                    <th>Фильтр</th>
                </tr>
            </thead>
            <tbody id="radar-body">
                <tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Загрузка рыночных котировок...</td></tr>
            </tbody>
        </table>

        <div class="footer-note">
            Multi-Alpha Growth Engine v2.1 • Запущено параллельно с базовой версией v2.0 (порт 8080) для сравнительного теста.
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

                // Metrics
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
                    document.getElementById('news-shield-status').innerText = '🛡️ Новостной Щит: ' + (data.news_profile === 'defensive_only' ? 'Защитный Профиль' : 'Активен');
                } else {
                    document.getElementById('news-shield-status').innerText = '⚪ Новостной Щит: Отключен';
                }

                if (data.btc_trend_status) {
                    document.getElementById('btc-filter-status').innerText = 'BTC Тренд: ' + data.btc_trend_status;
                } else if (data.btc_market_filter) {
                    document.getElementById('btc-filter-status').innerText = data.btc_market_filter;
                }

                // Timer
                const sec = data.next_candle_seconds || 0;
                const m = Math.floor(sec / 60);
                const s = sec % 60;
                document.getElementById('candle-timer').innerText = `${m}м ${s < 10 ? '0' : ''}${s}с`;

                // Positions
                const pBody = document.getElementById('positions-body');
                const posKeys = Object.keys(data.open_positions || {});
                if (posKeys.length === 0) {
                    pBody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Нет открытых позиций. Сканер активен.</td></tr>';
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

                // Radar
                const rBody = document.getElementById('radar-body');
                const rKeys = Object.keys(data.radar || {});
                if (rKeys.length > 0) {
                    let html = '';
                    for (const sym of rKeys) {
                        const r = data.radar[sym];
                        const coolBadge = r.in_cooldown ? ' <span class="badge-cooldown">Cooldown 3h</span>' : '';
                        html += `<tr>
                            <td style="font-weight:700;">${sym}</td>
                            <td>$${Number(r.current_price).toFixed(4)}</td>
                            <td style="color:${r.dist_to_long_pct < 2 ? 'var(--accent-green)' : 'inherit'}; font-weight:${r.dist_to_long_pct < 2 ? '700' : 'normal'}">${r.dist_to_long_pct}%</td>
                            <td style="color:${r.dist_to_short_pct < 2 ? 'var(--accent-red)' : 'inherit'}; font-weight:${r.dist_to_short_pct < 2 ? '700' : 'normal'}">${r.dist_to_short_pct}%</td>
                            <td>${r.atr}</td>
                            <td style="color:${r.volume_ratio >= 1.2 ? 'var(--accent-cyan)' : 'inherit'}">${r.volume_ratio}x</td>
                            <td style="font-size:11px; color:var(--text-muted);">${r.trend}</td>
                            <td>${coolBadge || '<span style="color:var(--text-muted);">Готов</span>'}</td>
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
            payload = LIVE_STATUS_FILE.read_bytes() if LIVE_STATUS_FILE.exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/state":
            payload = PERSISTENT_STATE_FILE.read_bytes() if PERSISTENT_STATE_FILE.exists() else b"{}"
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
    logger.info("Initializing PaperTraderV21 daemon...")
    GLOBAL_TRADER = PaperTraderV21(poll_interval_seconds=5)

    bot_thread = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    bot_thread.start()
    logger.info("Background PaperTraderV21 thread launched.")

    class SupervisedServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

        def service_actions(self):
            if not bot_thread.is_alive():
                raise SystemExit("Trading worker stopped; restarting service")

    with SupervisedServer(("0.0.0.0", PORT), DashboardHTTPRequestHandler) as server:
        logger.info(f"Serving v2.1 web dashboard at http://0.0.0.0:{PORT}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    start_server_and_bot()
