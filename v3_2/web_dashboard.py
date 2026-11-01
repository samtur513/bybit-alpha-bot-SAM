"""
Live Web Dashboard & Paper Trading Server v3.2 Dual-Regime (Port 8083).
Architecture:
- Dynamic Universe Selection (400+ Bybit Linear Perpetuals)
- Synergistic Dual-Regime:
  * TREND Module: 1h Donchian Breakout + Gear 3 Half-Kelly + Pyramiding + Cascade TP
  * RANGE Module: Mean-Reversion Maker Engine (Limit Maker orders in Chop, TP at SMA20, SL at 1.0 ATR)
  * Seamless Regime Handoff: Closes Range position on momentum breakout
- NEWS CORE DEFENSIVE SHIELD: DISABLED (Pure Dual-Regime Quant Benchmark)
- Dedicated Web Monitor on Port 8083
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

from v3_2.src.paper_trader_v32 import PaperTraderV32
from v3_2.src.config import (
    PORT,
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
    REPORTS_DIR,
)

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

logger = logging.getLogger("WebDashboardV32DualRegime")
GLOBAL_TRADER: PaperTraderV32 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.2 (Dual-Regime) | Port 8083</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #0b0e14;
            --bg-card: #151922;
            --bg-card-hover: #1c2230;
            --border: #262c3b;
            --text-main: #e2e8f0;
            --text-muted: #8b9bb4;
            --accent: #38bdf8;
            --accent-glow: rgba(56, 189, 248, 0.15);
            --green: #10b981;
            --green-glow: rgba(16, 185, 129, 0.15);
            --red: #f43f5e;
            --red-glow: rgba(244, 63, 94, 0.15);
            --gold: #f59e0b;
            --gold-glow: rgba(245, 158, 11, 0.15);
            --purple: #a855f7;
            --purple-glow: rgba(168, 85, 247, 0.15);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-base);
            color: var(--text-main);
            padding: 24px;
            line-height: 1.5;
        }
        .container { max-width: 1440px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
        }
        .brand { display: flex; align-items: center; gap: 12px; }
        .brand h1 { font-size: 24px; font-weight: 800; }
        .tag {
            background: rgba(168, 85, 247, 0.15);
            color: var(--purple);
            border: 1px solid rgba(168, 85, 247, 0.3);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 700;
        }
        .sub-header { color: var(--text-muted); font-size: 13px; margin-top: 4px; }
        .badges { display: flex; gap: 8px; align-items: center; }
        .badge {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            background: var(--bg-card);
            border: 1px solid var(--border);
        }
        .badge.active { background: var(--green-glow); color: var(--green); border-color: rgba(16,185,129,0.3); }
        .badge.dual { background: var(--purple-glow); color: var(--purple); border-color: rgba(168,85,247,0.3); }
        .badge.pure { background: var(--gold-glow); color: var(--gold); border-color: rgba(245,158,11,0.3); }
        .pulse-dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.85); } }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .metric-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px 20px;
        }
        .metric-title { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px; }
        .metric-val { font-size: 26px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }
        .metric-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
        .positive { color: var(--green); }
        .negative { color: var(--red); }

        .panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            margin-bottom: 24px;
            overflow: hidden;
        }
        .panel-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .panel-title { font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
        
        table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
        th {
            background: rgba(0,0,0,0.2);
            color: var(--text-muted);
            font-weight: 600;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }
        td { padding: 12px 16px; border-bottom: 1px solid rgba(38,44,59,0.5); font-family: 'JetBrains Mono', monospace; }
        tr:hover { background: var(--bg-card-hover); }

        .tag-regime {
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            display: inline-block;
        }
        .regime-trend { background: var(--accent-glow); color: var(--accent); border: 1px solid rgba(56,189,248,0.3); }
        .regime-range { background: var(--purple-glow); color: var(--purple); border: 1px solid rgba(168,85,247,0.3); }
        .regime-neutral { background: rgba(255,255,255,0.05); color: var(--text-muted); border: 1px solid var(--border); }

        .log-box {
            background: #07090e;
            padding: 14px 18px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: #94a3b8;
            max-height: 240px;
            overflow-y: auto;
            white-space: pre-wrap;
            line-height: 1.6;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <div>
                    <h1>Bybit Multi-Alpha v3.2 <span class="tag">Dual-Regime (Port 8083)</span></h1>
                    <div class="sub-header">Synergistic Alpha: 1h Donchian Breakout (Trend) + Mean-Reversion Maker Engine (Chop) | Dynamic 400+ Universe</div>
                </div>
            </div>
            <div class="badges">
                <div class="badge dual">
                    <span class="pulse-dot"></span>
                    <span>DUAL-REGIME HYBRID</span>
                </div>
                <div class="badge active" id="badge-stream">
                    <span class="pulse-dot"></span>
                    <span>WEBSOCKET LIVE</span>
                </div>
                <div class="badge pure">
                    <span>NEWS SHIELD: OFF (PURE QUANT)</span>
                </div>
            </div>
        </header>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-title">Общий капитал / Эквити</div>
                <div class="metric-val" id="metric-equity">$300.00</div>
                <div class="metric-sub" id="metric-capital-sub">Базовый депозит: $300.00</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Чистый PnL (Net PnL)</div>
                <div class="metric-val positive" id="metric-pnl">+$0.00 (0.00%)</div>
                <div class="metric-sub">Trend + Range Maker сделки</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Винрейт (Win Rate)</div>
                <div class="metric-val" id="metric-wr">0.0%</div>
                <div class="metric-sub" id="metric-trades-sub">Всего закрыто: 0 сделок</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Макс. просадка (Drawdown)</div>
                <div class="metric-val" id="metric-dd">0.00%</div>
                <div class="metric-sub" id="metric-peak-sub">Пик депозита: $300.00</div>
            </div>
        </div>

        <!-- Open Positions Panel -->
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">
                    <span>⚡ Открытые позиции (Trend & Range Maker)</span>
                </div>
                <div style="font-size: 12px; color: var(--text-muted);" id="pos-counter">Позиций: 0</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Модуль</th>
                        <th>Монета</th>
                        <th>Сторона</th>
                        <th>Объём</th>
                        <th>Вход</th>
                        <th>Текущая</th>
                        <th>Нереализованный PnL</th>
                        <th>Stop Loss / TP</th>
                    </tr>
                </thead>
                <tbody id="positions-table-body">
                    <tr><td colspan="8" style="text-align: center; color: var(--text-muted);">Нет открытых позиций. Сканирование рынка...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Pending Limit Orders (Range Maker) -->
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">
                    <span>🎯 Лимитные Maker-ордера в боковике (Ожидают исполнения на границах)</span>
                </div>
                <div style="font-size: 12px; color: var(--text-muted);" id="orders-counter">Ордеров: 0</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Монета</th>
                        <th>Тип Maker</th>
                        <th>Лимитная цена</th>
                        <th>Объём</th>
                        <th>Целевой тейк (SMA20)</th>
                        <th>Защитный стоп (1.0 ATR)</th>
                    </tr>
                </thead>
                <tbody id="orders-table-body">
                    <tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Нет активных лимитных ордеров. Мониторинг фазы CHOP/RANGE...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Market Radar with Regimes -->
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">
                    <span>🌐 Радар рынка (Top Bybit Perpetuals) — Классификатор режимов и уровни</span>
                </div>
                <div style="font-size: 12px; color: var(--text-muted);" id="radar-counter">Монет в мониторинге: 0</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Монета</th>
                        <th>Режим рынка</th>
                        <th>Цена</th>
                        <th>До Long (+30B)</th>
                        <th>До Short (-30B)</th>
                        <th>До Maker Buy (BB Low)</th>
                        <th>До Maker Sell (BB High)</th>
                        <th>RSI</th>
                        <th>ADX</th>
                        <th>Статус</th>
                    </tr>
                </thead>
                <tbody id="radar-table-body">
                    <tr><td colspan="10" style="text-align: center; color: var(--text-muted);">Загрузка рыночных данных...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Closed Trades -->
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">
                    <span>📜 История закрытых сделок (Trend + Range)</span>
                </div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Модуль</th>
                        <th>Монета</th>
                        <th>Сторона</th>
                        <th>Вход</th>
                        <th>Выход</th>
                        <th>Net PnL ($)</th>
                        <th>Причина закрытия</th>
                    </tr>
                </thead>
                <tbody id="closed-table-body">
                    <tr><td colspan="7" style="text-align: center; color: var(--text-muted);">Сделок пока нет. Ожидание сигналов...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Logs -->
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">
                    <span>🖥️ Системный лог Dual-Regime Daemon v3.2</span>
                </div>
            </div>
            <div class="log-box" id="log-box">Подключение к серверу логов...</div>
        </div>
    </div>

    <script>
        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();

                // Metrics
                const equity = data.portfolio_equity || data.capital || 300.0;
                const capital = data.capital || 300.0;
                const pnl = equity - 300.0;
                const pnlPct = (pnl / 300.0) * 100.0;

                document.getElementById('metric-equity').innerText = `$${equity.toFixed(2)}`;
                document.getElementById('metric-capital-sub').innerText = `Капитал: $${capital.toFixed(2)}`;

                const pnlEl = document.getElementById('metric-pnl');
                pnlEl.innerText = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct.toFixed(2)}%)`;
                pnlEl.className = `metric-val ${pnl >= 0 ? 'positive' : 'negative'}`;

                document.getElementById('metric-wr').innerText = `${(data.win_rate_pct || 0).toFixed(1)}%`;
                document.getElementById('metric-trades-sub').innerText = `Всего закрыто: ${data.total_trades || 0} сделок`;

                document.getElementById('metric-dd').innerText = `${(data.drawdown_pct || 0).toFixed(2)}%`;
                document.getElementById('metric-peak-sub').innerText = `Пик: $${(data.peak_equity || 300.0).toFixed(2)}`;

                // Positions
                const tPos = data.trend_open_positions || [];
                const rPos = data.range_open_positions || [];
                const allPos = [...tPos, ...rPos];
                document.getElementById('pos-counter').innerText = `Позиций: ${allPos.length}`;

                const posBody = document.getElementById('positions-table-body');
                if (allPos.length === 0) {
                    posBody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-muted);">Нет открытых позиций. Сканирование рынка...</td></tr>';
                } else {
                    posBody.innerHTML = allPos.map(p => `
                        <tr>
                            <td><span class="tag" style="background:${p.module==='TREND'?'rgba(56,189,248,0.15)':'rgba(168,85,247,0.15)'};color:${p.module==='TREND'?'var(--accent)':'var(--purple)'};">${p.module}</span></td>
                            <td><strong>${p.symbol}</strong></td>
                            <td class="${p.direction==='LONG'||p.direction==='BUY'?'positive':'negative'}">${p.direction}</td>
                            <td>${p.qty}</td>
                            <td>$${p.entry_price.toFixed(4)}</td>
                            <td>$${p.current_price.toFixed(4)}</td>
                            <td class="${p.unrealized_pnl>=0?'positive':'negative'}">${p.unrealized_pnl>=0?'+':''}$${p.unrealized_pnl.toFixed(2)}</td>
                            <td>SL: $${(p.stop_loss||0).toFixed(4)} ${p.take_profit ? '| TP: $'+p.take_profit.toFixed(4) : ''}</td>
                        </tr>
                    `).join('');
                }

                // Range Orders
                const rOrders = data.range_pending_orders || [];
                document.getElementById('orders-counter').innerText = `Ордеров: ${rOrders.length}`;
                const ordBody = document.getElementById('orders-table-body');
                if (rOrders.length === 0) {
                    ordBody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Нет активных лимитных ордеров. Мониторинг фазы CHOP/RANGE...</td></tr>';
                } else {
                    ordBody.innerHTML = rOrders.map(o => `
                        <tr>
                            <td><strong>${o.symbol}</strong></td>
                            <td class="${o.direction==='BUY'?'positive':'negative'}">${o.direction} LIMIT</td>
                            <td class="positive">$${o.price.toFixed(4)}</td>
                            <td>${o.qty}</td>
                            <td>$${o.take_profit.toFixed(4)}</td>
                            <td>$${o.stop_loss.toFixed(4)}</td>
                        </tr>
                    `).join('');
                }

                // Market Radar
                const radar = data.market_radar || {};
                const rKeys = Object.keys(radar);
                document.getElementById('radar-counter').innerText = `Монет в мониторинге: ${rKeys.length}`;
                const radBody = document.getElementById('radar-table-body');
                if (rKeys.length === 0) {
                    radBody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: var(--text-muted);">Загрузка рыночных данных...</td></tr>';
                } else {
                    radBody.innerHTML = rKeys.map(sym => {
                        const it = radar[sym];
                        const regClass = it.regime === 'TREND' ? 'regime-trend' : (it.regime === 'RANGE' ? 'regime-range' : 'regime-neutral');
                        return `
                            <tr>
                                <td><strong>${sym}</strong></td>
                                <td><span class="tag-regime ${regClass}">${it.regime}</span></td>
                                <td>$${it.current_price.toFixed(4)}</td>
                                <td class="${it.dist_to_long_pct===0?'positive':''}">${it.dist_to_long_pct}%</td>
                                <td class="${it.dist_to_short_pct===0?'negative':''}">${it.dist_to_short_pct}%</td>
                                <td style="color:var(--purple);">${it.dist_to_maker_buy_pct}%</td>
                                <td style="color:var(--purple);">${it.dist_to_maker_sell_pct}%</td>
                                <td>${it.rsi}</td>
                                <td>${it.adx}</td>
                                <td>${it.status}</td>
                            </tr>
                        `;
                    }).join('');
                }

                // Closed trades
                const closed = data.closed_trades || [];
                const clBody = document.getElementById('closed-table-body');
                if (closed.length === 0) {
                    clBody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">Сделок пока нет. Ожидание сигналов...</td></tr>';
                } else {
                    clBody.innerHTML = closed.slice(0, 15).map(t => `
                        <tr>
                            <td><span class="tag" style="background:${t.module==='TREND'?'rgba(56,189,248,0.15)':'rgba(168,85,247,0.15)'};color:${t.module==='TREND'?'var(--accent)':'var(--purple)'};">${t.module}</span></td>
                            <td><strong>${t.symbol}</strong></td>
                            <td class="${t.direction==='LONG'||t.direction==='BUY'?'positive':'negative'}">${t.direction}</td>
                            <td>$${t.entry_price.toFixed(4)}</td>
                            <td>$${t.exit_price.toFixed(4)}</td>
                            <td class="${t.net_pnl>=0?'positive':'negative'}">${t.net_pnl>=0?'+':''}$${t.net_pnl.toFixed(2)}</td>
                            <td>${t.exit_reason}</td>
                        </tr>
                    `).join('');
                }

                // Logs
                const logRes = await fetch('/api/logs');
                if (logRes.ok) {
                    const logs = await logRes.json();
                    const logBox = document.getElementById('log-box');
                    logBox.innerText = logs.join('\\n');
                    logBox.scrollTop = logBox.scrollHeight;
                }
            } catch (err) {
                console.error("Dashboard update error:", err);
            }
        }

        setInterval(updateDashboard, 3000);
        updateDashboard();
    </script>
</body>
</html>
"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        elif path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if GLOBAL_TRADER and GLOBAL_TRADER.last_status_cache:
                self.wfile.write(json.dumps(GLOBAL_TRADER.last_status_cache, default=str).encode("utf-8"))
            else:
                self.wfile.write(json.dumps({"status": "starting", "capital": 300.0, "portfolio_equity": 300.0}).encode("utf-8"))

        elif path == "/api/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if PERSISTENT_STATE_FILE.exists():
                self.wfile.write(PERSISTENT_STATE_FILE.read_bytes())
            else:
                self.wfile.write(b"{}")

        elif path == "/api/logs":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(list(LOG_BUFFER)).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_server_and_bot():
    global GLOBAL_TRADER

    logger.info("Initializing PaperTraderV32 Dual-Regime daemon...")
    GLOBAL_TRADER = PaperTraderV32(
        initial_capital=Decimal("300.0"),
        state_file=PERSISTENT_STATE_FILE,
        live_status_file=LIVE_STATUS_FILE,
        live_md_file=LIVE_MD_FILE,
    )

    t = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    t.start()
    logger.info("Background PaperTraderV32 thread launched.")

    class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True

    with ThreadedHTTPServer(("0.0.0.0", PORT), DashboardHandler) as httpd:
        logger.info(f"Multi-Alpha v3.2 Dual-Regime Web Dashboard live on http://127.0.0.1:{PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    start_server_and_bot()
