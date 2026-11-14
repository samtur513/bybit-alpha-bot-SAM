#!/usr/bin/env python3
"""
v3_2/src/shadow/web_dashboard_shadow.py
Dedicated High-Fidelity Web Dashboard for Bybit Multi-Alpha v3.2 Legacy Shadow (Port 8084).
Protocol: V32-SHADOW-INTEGRITY-002 / V32-MASTER-001

Features:
- Telemetry for Native Paper vs Realistic Shadow Equity side-by-side
- Initial $300.00 base and common T0 synchronization
- Truthful WebSocket / Stream Status (Strictly OFFLINE when disconnected)
- Independent Position Tables (Native vs Shadow) with TP1/TP2 and Trailing Stops
- Fees and Funding Rate Accrual breakdown
- АКПП Gearbox status & News Core v1.2 Macro Embargo indicator
- SQLite Microsecond Event Stream inspection
- Missed Intent / Spread Rejection logs
- Strictly Read-Only Web API Endpoints
"""

from datetime import datetime, timezone
from decimal import Decimal
import http.server
import json
import logging
from pathlib import Path
import socketserver
import sqlite3
import sys
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from .paper_trader_v32_shadow import V32LegacyShadowEngine

logger = logging.getLogger("V32ShadowDashboard")
DEFAULT_PORT = 8084


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.2 Legacy Shadow | Port 8084</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #090d16;
            --card-bg: #111726;
            --border: #1e293b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --green: #10b981;
            --red: #f43f5e;
            --blue: #38bdf8;
            --amber: #f59e0b;
            --purple: #a855f7;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--bg);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            padding: 24px;
            line-height: 1.5;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }
        .title-block h1 {
            font-family: 'JetBrains Mono', monospace;
            font-size: 20px;
            font-weight: 700;
            color: var(--blue);
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .badge {
            font-size: 11px;
            padding: 4px 8px;
            border-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            text-transform: uppercase;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
        }
        .badge-live { background: rgba(16, 185, 129, 0.15); color: var(--green); border-color: rgba(16, 185, 129, 0.3); }
        .badge-offline { background: rgba(244, 63, 94, 0.15); color: var(--red); border-color: rgba(244, 63, 94, 0.3); }
        .badge-embargo { background: rgba(245, 158, 11, 0.15); color: var(--amber); border-color: rgba(245, 158, 11, 0.3); }
        .badge-info { background: rgba(56, 189, 248, 0.15); color: var(--blue); border-color: rgba(56, 189, 248, 0.3); }
        .grid-kpi {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .kpi-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }
        .kpi-label { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
        .kpi-val { font-family: 'JetBrains Mono', monospace; font-size: 22px; font-weight: 700; }
        .val-green { color: var(--green); }
        .val-red { color: var(--red); }
        .val-blue { color: var(--blue); }
        .val-amber { color: var(--amber); }
        .panel {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 24px;
        }
        .panel-title {
            font-family: 'JetBrains Mono', monospace;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 16px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: flex;
            justify-content: space-between;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
        }
        th, td {
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }
        th { color: var(--text-muted); font-size: 11px; text-transform: uppercase; }
        tr:hover { background: rgba(255, 255, 255, 0.02); }
    </style>
</head>
<body>
    <div class="header">
        <div class="title-block">
            <h1>Bybit Multi-Alpha v3.2 Legacy Shadow <span class="badge badge-info">Integrity Parity</span></h1>
            <p style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">
                Port 8084 | Donchian 1h (120 bars) | News Core v1.2 | АКПП 3-Stage | Realistic VIP0 Fees (0.100%) | T0: <span id="t0-val">Syncing...</span>
            </p>
        </div>
        <div id="sys-status" style="display: flex; gap: 8px;">
            <span class="badge badge-offline" id="stream-badge">WS: OFFLINE</span>
            <span class="badge" id="news-badge">News Shield: Checking...</span>
            <span class="badge badge-info" id="gear-badge">АКПП: G3</span>
        </div>
    </div>

    <div class="grid-kpi">
        <div class="kpi-card">
            <div class="kpi-label">Native Paper Equity (0.090%)</div>
            <div class="kpi-val val-blue" id="native-eq">$300.00</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Realistic Shadow Equity (0.100%)</div>
            <div class="kpi-val val-green" id="shadow-eq">$300.00</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Alpha Discrepancy (Δ Net)</div>
            <div class="kpi-val" id="delta-eq">$0.00</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Fees: Native vs Shadow</div>
            <div class="kpi-val val-amber" id="fee-comparison">$0.00 / $0.00</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Funding Accrued (N / S)</div>
            <div class="kpi-val" id="funding-comparison">$0.00 / $0.00</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">АКПП Ступень & Риск</div>
            <div class="kpi-val val-amber" id="akpp-status">G3 | 1.0%</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Rejected / Missed Intents</div>
            <div class="kpi-val val-red" id="reject-count">0</div>
        </div>
    </div>

    <div class="panel">
        <div class="panel-title">
            <span>Realistic Shadow Open Positions (Port 8084)</span>
            <span style="font-size: 11px; color: var(--green);">VIP0 Taker 0.100% | Spread Gate 0.08%</span>
        </div>
        <table id="shadow-positions-table">
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Direction</th>
                    <th>Remaining Qty</th>
                    <th>Entry Price</th>
                    <th>Current Stop</th>
                    <th>TP1 (30%)</th>
                    <th>TP2 (40%)</th>
                    <th>Fees Paid</th>
                    <th>Funding</th>
                </tr>
            </thead>
            <tbody>
                <tr><td colspan="9" style="text-align: center; color: var(--text-muted);">No open positions</td></tr>
            </tbody>
        </table>
    </div>

    <div class="panel">
        <div class="panel-title">
            <span>Native Paper Open Positions (Port 8083 Baseline Equivalence)</span>
            <span style="font-size: 11px; color: var(--blue);">MNT Discount 0.090% | Zero Spread Gate</span>
        </div>
        <table id="native-positions-table">
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Direction</th>
                    <th>Remaining Qty</th>
                    <th>Entry Price</th>
                    <th>Current Stop</th>
                    <th>TP1 (30%)</th>
                    <th>TP2 (40%)</th>
                    <th>Fees Paid</th>
                    <th>Funding</th>
                </tr>
            </thead>
            <tbody>
                <tr><td colspan="9" style="text-align: center; color: var(--text-muted);">No open positions</td></tr>
            </tbody>
        </table>
    </div>

    <div class="panel">
        <div class="panel-title">Microsecond Relational Event Ledger (SQLite Stream)</div>
        <table id="events-table">
            <thead>
                <tr>
                    <th>Time UTC</th>
                    <th>Portfolio</th>
                    <th>Trade ID</th>
                    <th>Symbol</th>
                    <th>Event Type</th>
                    <th>Fill Price</th>
                    <th>Notional</th>
                    <th>Fee Paid</th>
                    <th>Realized PnL</th>
                    <th>Portfolio Equity</th>
                    <th>Notes</th>
                </tr>
            </thead>
            <tbody>
                <tr><td colspan="11" style="text-align: center; color: var(--text-muted);">Awaiting market events...</td></tr>
            </tbody>
        </table>
    </div>

    <script>
        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const d = await res.json();

                document.getElementById('native-eq').innerText = '$' + d.native_realized_equity.toFixed(2);
                document.getElementById('shadow-eq').innerText = '$' + d.shadow_realized_equity.toFixed(2);
                document.getElementById('t0-val').innerText = d.t0_datetime_utc;
                
                const delta = d.alpha_discrepancy_usdt;
                const deltaEl = document.getElementById('delta-eq');
                deltaEl.innerText = (delta >= 0 ? '+$' : '-$') + Math.abs(delta).toFixed(2);
                deltaEl.className = 'kpi-val ' + (delta >= 0 ? 'val-green' : 'val-red');

                document.getElementById('fee-comparison').innerText = '$' + d.native_fees_paid.toFixed(2) + ' / $' + d.shadow_fees_paid.toFixed(2);
                document.getElementById('funding-comparison').innerText = '$' + d.native_funding_paid.toFixed(2) + ' / $' + d.shadow_funding_paid.toFixed(2);
                document.getElementById('akpp-status').innerText = 'G' + d.gearbox_gear + ' | ' + d.gearbox_risk_pct.toFixed(1) + '%';
                document.getElementById('reject-count').innerText = d.shadow_rejected_orders_count;

                const streamBadge = document.getElementById('stream-badge');
                if (d.stream_connected) {
                    streamBadge.className = 'badge badge-live';
                    streamBadge.innerText = 'WS: LIVE';
                } else {
                    streamBadge.className = 'badge badge-offline';
                    streamBadge.innerText = 'WS: OFFLINE (STANDALONE)';
                }

                const nb = document.getElementById('news-badge');
                if (d.macro_blackout_active) {
                    nb.className = 'badge badge-embargo';
                    nb.innerText = 'EMBARGO: ' + (d.macro_event_name || 'Active');
                } else {
                    nb.className = 'badge badge-live';
                    nb.innerText = 'News Shield: Clear';
                }

                document.getElementById('gear-badge').innerText = 'АКПП: G' + d.gearbox_gear;

                // Render Shadow Positions
                const shaTbody = document.querySelector('#shadow-positions-table tbody');
                if (d.shadow_positions && d.shadow_positions.length > 0) {
                    shaTbody.innerHTML = d.shadow_positions.map(p => `
                        <tr>
                            <td><strong>${p.symbol}</strong></td>
                            <td><span class="badge ${p.direction === 1 ? 'badge-live' : 'badge-offline'}">${p.direction === 1 ? 'LONG' : 'SHORT'}</span></td>
                            <td>${parseFloat(p.current_qty).toFixed(3)}</td>
                            <td>$${parseFloat(p.entry_price).toFixed(4)}</td>
                            <td>$${parseFloat(p.current_stop).toFixed(4)}</td>
                            <td>${p.tp1_taken ? 'HIT' : 'PENDING'}</td>
                            <td>${p.tp2_taken ? 'HIT' : 'PENDING'}</td>
                            <td>$${(parseFloat(p.entry_fees) + parseFloat(p.exit_fees)).toFixed(3)}</td>
                            <td>$${parseFloat(p.funding_pnl).toFixed(3)}</td>
                        </tr>
                    `).join('');
                } else {
                    shaTbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">No open positions</td></tr>';
                }

                // Render Native Positions
                const natTbody = document.querySelector('#native-positions-table tbody');
                if (d.native_positions && d.native_positions.length > 0) {
                    natTbody.innerHTML = d.native_positions.map(p => `
                        <tr>
                            <td><strong>${p.symbol}</strong></td>
                            <td><span class="badge ${p.direction === 1 ? 'badge-live' : 'badge-offline'}">${p.direction === 1 ? 'LONG' : 'SHORT'}</span></td>
                            <td>${parseFloat(p.current_qty).toFixed(3)}</td>
                            <td>$${parseFloat(p.entry_price).toFixed(4)}</td>
                            <td>$${parseFloat(p.current_stop).toFixed(4)}</td>
                            <td>${p.tp1_taken ? 'HIT' : 'PENDING'}</td>
                            <td>${p.tp2_taken ? 'HIT' : 'PENDING'}</td>
                            <td>$${(parseFloat(p.entry_fees) + parseFloat(p.exit_fees)).toFixed(3)}</td>
                            <td>$${parseFloat(p.funding_pnl).toFixed(3)}</td>
                        </tr>
                    `).join('');
                } else {
                    natTbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">No open positions</td></tr>';
                }

                // Load SQLite Events
                const evRes = await fetch('/api/events');
                if (evRes.ok) {
                    const evs = await evRes.json();
                    if (evs.length > 0) {
                        const tbody = document.querySelector('#events-table tbody');
                        tbody.innerHTML = evs.slice(0, 15).map(e => `
                            <tr>
                                <td>${(e.datetime_utc || '').split('T')[1] ? e.datetime_utc.split('T')[1].slice(0, 8) : ''}</td>
                                <td><span class="badge ${e.portfolio === 'SHADOW' ? 'badge-live' : 'badge-info'}">${e.portfolio}</span></td>
                                <td>${e.trade_id}</td>
                                <td><strong>${e.symbol}</strong></td>
                                <td><span class="badge">${e.event_type}</span></td>
                                <td>$${e.fill_price.toFixed(4)}</td>
                                <td>$${e.notional_usdt.toFixed(2)}</td>
                                <td>$${e.fee_paid.toFixed(3)}</td>
                                <td>$${e.realized_pnl.toFixed(2)}</td>
                                <td>$${e.portfolio_equity.toFixed(2)}</td>
                                <td>${e.notes}</td>
                            </tr>
                        `).join('');
                    }
                }
            } catch (err) {
                console.error(err);
            }
        }
        setInterval(updateDashboard, 3000);
        updateDashboard();
    </script>
</body>
</html>
"""


class ShadowDashboardHandler(http.server.BaseHTTPRequestHandler):
    engine: Optional[V32LegacyShadowEngine] = None

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress noisy stdout

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        elif path == "/api/status":
            if not self.engine:
                self.send_error(503, "Engine not initialized")
                return
            data = self.engine.get_status_dict()
            self._send_json(data)
            return

        elif path == "/api/events":
            if not self.engine:
                self.send_error(503, "Engine not initialized")
                return
            events = []
            try:
                with sqlite3.connect(self.engine.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT * FROM shadow_execution_events
                        ORDER BY event_id DESC LIMIT 50;
                    """)
                    rows = cursor.fetchall()
                    events = [dict(r) for r in rows]
            except Exception as e:
                logger.error(f"Error querying SQLite events: {e}")
            self._send_json(events)
            return

        elif path == "/api/divergence":
            if not self.engine:
                self.send_error(503, "Engine not initialized")
                return
            div_data = {
                "native_equity": float(self.engine.native_portfolio.realized_equity),
                "shadow_equity": float(self.engine.shadow_portfolio.realized_equity),
                "alpha_discrepancy_usdt": float(self.engine.shadow_portfolio.realized_equity - self.engine.native_portfolio.realized_equity),
                "native_fee_drag": float(self.engine.native_portfolio.total_fees),
                "shadow_fee_drag": float(self.engine.shadow_portfolio.total_fees),
                "fee_drag_diff": float(self.engine.shadow_portfolio.total_fees - self.engine.native_portfolio.total_fees),
                "native_positions": list(self.engine.native_portfolio.positions.keys()),
                "shadow_positions": list(self.engine.shadow_portfolio.positions.keys()),
                "divergent_symbols": list(
                    set(self.engine.native_portfolio.positions.keys()) ^ set(self.engine.shadow_portfolio.positions.keys())
                ),
            }
            self._send_json(div_data)
            return

        elif path == "/api/rejections":
            if not self.engine:
                self.send_error(503, "Engine not initialized")
                return
            self._send_json(self.engine.shadow_portfolio.rejected_orders)
            return

        else:
            self.send_error(404, "Not Found")

    def _send_json(self, data: Any) -> None:
        payload = json.dumps(data, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)


def run_shadow_dashboard(engine: V32LegacyShadowEngine, port: int = DEFAULT_PORT) -> threading.Thread:
    """Launches the Shadow Web Dashboard in a daemon thread."""
    handler_class = ShadowDashboardHandler
    handler_class.engine = engine

    class ReusableServer(socketserver.TCPServer):
        allow_reuse_address = True

    server = ReusableServer(("0.0.0.0", port), handler_class)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"V32 Shadow Dashboard online at http://0.0.0.0:{port}")
    return t
