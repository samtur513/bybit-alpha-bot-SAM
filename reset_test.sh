#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Сброс статистики и запуск чистого теста ($300) "
echo "=================================================="

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Остановка службы
echo "1. Остановка бота..."
systemctl stop bybit-bot 2>/dev/null || true

# 2. Инициализация чистого профиля
echo "2. Обнуление баланса до $300.00 и очистка сделок..."
mkdir -p "$APP_DIR/reports"
cat << 'EOF' > "$APP_DIR/reports/BOT_STATE_PERSISTENT.json"
{
  "initial_capital": "300.0",
  "realized_equity": "300.0",
  "peak_equity": "300.0",
  "max_drawdown_pct": "0.0",
  "open_positions": {},
  "closed_trades": [],
  "risk_manager": {
    "consecutive_losses": 0,
    "consecutive_wins": 0,
    "is_kill_switch_active": false
  }
}
EOF

rm -f "$APP_DIR/reports/PAPER_TRADING_LIVE.json" "$APP_DIR/reports/PAPER_TRADING_LIVE.md"

# 3. Запуск службы
echo "3. Запуск торгового бота..."
systemctl start bybit-bot

sleep 2

SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo "=================================================="
echo "✅ ТЕСТ УСПЕШНО ПЕРЕЗАПУЩЕН С $300.00 USDT!"
echo "=================================================="
echo "🌐 Дашборд: http://${SERVER_IP}:8080"
echo "=================================================="
