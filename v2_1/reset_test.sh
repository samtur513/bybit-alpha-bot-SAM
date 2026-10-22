#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Сброс статистики v2.1 и чистый старт ($300.00) "
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Остановка службы v2.1
echo "1. Остановка бота v2.1..."
systemctl stop bybit-bot-v21 2>/dev/null || true

# 2. Инициализация чистого профиля
echo "2. Обнуление баланса до $300.00 и очистка сделок v2.1..."
mkdir -p "$SCRIPT_DIR/reports"
cat << 'EOF' > "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V21.json"
{
  "version": "2.1",
  "initial_capital": "300.0",
  "realized_equity": "300.0",
  "peak_equity": "300.0",
  "max_drawdown_pct": "0.0",
  "bar_counter": 0,
  "cooldown_tracker": {},
  "open_positions": {},
  "closed_trades": [],
  "risk_manager": {
    "consecutive_losses": 0,
    "consecutive_wins": 0,
    "is_kill_switch_active": false
  }
}
EOF

rm -f "$SCRIPT_DIR/reports/PAPER_TRADING_LIVE_V21.json" "$SCRIPT_DIR/reports/PAPER_TRADING_LIVE_V21.md"

# 3. Запуск службы v2.1
echo "3. Запуск торгового бота v2.1..."
systemctl start bybit-bot-v21

sleep 2

SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo "=================================================="
echo "✅ ТЕСТ v2.1 УСПЕШНО ПЕРЕЗАПУЩЕН С $300.00 USDT!"
echo "=================================================="
echo "🌐 Дашборд v2.1: http://${SERVER_IP}:8081"
echo "=================================================="
