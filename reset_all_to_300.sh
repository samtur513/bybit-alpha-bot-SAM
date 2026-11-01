#!/usr/bin/env bash
set -e

echo "================================================================="
echo "   СИНХРОННЫЙ ПЕРЕЗАПУСК A/B/C ТЕСТА С $300.00 НА ВСЕХ БОТАХ    "
echo "================================================================="

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# 1. Остановка всех 3 служб
echo "[1/6] Остановка работающих служб ботов..."
systemctl stop bybit-bot bybit-bot-v21 bybit-bot-v3 2>/dev/null || true

# 2. Обнуление профиля v2.0 (Порт 8080: Baseline 1h, 18 монет)
echo "[2/6] Сброс профиля v2.0 (Порт 8080) до $300.00..."
mkdir -p "$APP_DIR/reports"
cat << 'EOF_V20' > "$APP_DIR/reports/BOT_STATE_PERSISTENT.json"
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
EOF_V20
rm -f "$APP_DIR/reports/PAPER_TRADING_LIVE.json" "$APP_DIR/reports/PAPER_TRADING_LIVE.md"

# 3. Сброс профиля v3.1 Pure Quant (Порт 8081: 1h Dynamic Screener, Без News Core)
echo "[3/6] Сброс профиля v3.1 Pure Quant (Порт 8081) до $300.00..."
mkdir -p "$APP_DIR/v2_1/reports"
cat << 'EOF_V21' > "$APP_DIR/v2_1/reports/BOT_STATE_PERSISTENT_V21.json"
{
  "version": "3.1",
  "schema_version": 4,
  "trading_halted": false,
  "last_bar_timestamp": 0,
  "bar_counter": 0,
  "cooldown_tracker": {},
  "funding_receipts": {},
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
  },
  "runtime": {
    "last_processed_bar_ts": {}
  }
}
EOF_V21
rm -f "$APP_DIR/v2_1/reports/PAPER_TRADING_LIVE_V21.json" "$APP_DIR/v2_1/reports/PAPER_TRADING_LIVE_V21.md"

# 4. Сброс профиля v3.1 Full Quant (Порт 8082: 1h Dynamic Screener + News Core Shield + Live Streamer)
echo "[4/6] Сброс профиля v3.1 Full Quant (Порт 8082) до $300.00..."
mkdir -p "$APP_DIR/v3/reports"
cat << 'EOF_V3' > "$APP_DIR/v3/reports/BOT_STATE_PERSISTENT_V3.json"
{
  "version": "3.1",
  "schema_version": 4,
  "trading_halted": false,
  "last_bar_timestamp": 0,
  "bar_counter": 0,
  "cooldown_tracker": {},
  "funding_receipts": {},
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
  },
  "runtime": {
    "last_processed_bar_ts": {}
  }
}
EOF_V3
rm -f "$APP_DIR/v3/reports/PAPER_TRADING_LIVE_V3.json" "$APP_DIR/v3/reports/PAPER_TRADING_LIVE_V3.md"

# 5. Проверка systemd служб и зависимостей
echo "[5/6] Регистрация и проверка systemd служб..."
if [ ! -f "/etc/systemd/system/bybit-bot.service" ] && [ -f "$APP_DIR/setup.sh" ]; then
    bash "$APP_DIR/setup.sh" > /dev/null 2>&1 || true
fi
if [ -f "$APP_DIR/v2_1/setup_v21.sh" ]; then
    bash "$APP_DIR/v2_1/setup_v21.sh" > /dev/null 2>&1 || true
fi
if [ -f "$APP_DIR/v3/setup_v3.sh" ]; then
    bash "$APP_DIR/v3/setup_v3.sh" > /dev/null 2>&1 || true
fi

# Убедимся, что порты 8080, 8081, 8082 открыты
if command -v ufw &> /dev/null; then
    ufw allow 8080/tcp > /dev/null 2>&1 || true
    ufw allow 8081/tcp > /dev/null 2>&1 || true
    ufw allow 8082/tcp > /dev/null 2>&1 || true
fi

systemctl daemon-reload

# 6. Одновременный запуск всех трёх ботов
echo "[6/6] Синхронный запуск всех трёх ботов..."
systemctl restart bybit-bot bybit-bot-v21 bybit-bot-v3

sleep 3

SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo "================================================================="
echo "✅ ВСЕ 3 ВЕРСИИ УСПЕШНО И СИНХРОННО ЗАПУЩЕНЫ С $300.00 USDT!"
echo "================================================================="
echo ""
echo "🌐 1. БАЗОВАЯ ВЕРСИЯ (v2.0, 1h, 18 фиксированных монет):"
echo "   • Панель мониторинга: http://${SERVER_IP}:8080"
echo "   • Баланс:             $300.00"
echo ""
echo "🌐 2. QUANT ЧИСТЫЙ (v3.1, 1h, Dynamic Universe 400+, Без Новостей):"
echo "   • Панель мониторинга: http://${SERVER_IP}:8081"
echo "   • Баланс:             $300.00"
echo ""
echo "🌐 3. QUANT + НОВОСТНОЙ ЩИТ (v3.1, 1h, Dynamic Universe + News Core Live Streamer):"
echo "   • Панель мониторинга: http://${SERVER_IP}:8082"
echo "   • Баланс:             $300.00"
echo "================================================================="
