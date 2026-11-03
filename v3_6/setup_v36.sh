#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v3.6 Omega     "
echo "   (Omega Dual-Regime ZF-8 + News Core v1.2)      "
echo "   (Порт 8089 // Trend + IRAS Maker + News Guard) "
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория проекта: $APP_DIR"
echo "Директория v3_6: $SCRIPT_DIR"

# 1. Системные пакеты
echo "[1/5] Проверка системных пакетов..."
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip curl ufw > /dev/null 2>&1 || true
fi

# 2. Виртуальное окружение Python
echo "[2/5] Проверка виртуального окружения Python..."
mkdir -p "$SCRIPT_DIR/reports"
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -qq
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -qq

# 3. Инициализация чистого состояния если отсутствует
echo "[3/5] Проверка файла состояния торговли v3.6 Omega..."
if [ ! -f "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V36.json" ]; then
    cat << 'EOF_STATE' > "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V36.json"
{
  "version": "3.6.0",
  "timeframe": "1h",
  "schema_version": 6,
  "mode": "OMEGA_DUAL_REGIME_ZF8",
  "initial_capital": "300.0",
  "trend_engine": {
    "version": "3.4.0",
    "initial_capital": "300.0",
    "realized_equity": "300.0",
    "peak_equity": "300.0",
    "max_drawdown_pct": "0.0",
    "open_positions": {},
    "closed_trades": [],
    "transmission": {
      "current_gear": 1,
      "streak_wins": 0,
      "streak_losses": 0,
      "total_trades_processed": 0,
      "is_circuit_breaker_active": false,
      "last_shift_reason": "INITIAL_DEPLOYMENT"
    }
  },
  "range_engine": {
    "initial_capital": "300.0",
    "realized_equity": "300.0",
    "open_positions": {},
    "pending_orders": {},
    "closed_trades": []
  },
  "runtime": {
    "last_processed_bar_ts": {}
  }
}
EOF_STATE
    echo "Создан начальный профиль капитала v3.6 Omega: \$300.00 USDT"
fi

# 4. Регистрация systemd службы bybit-bot-v36.service
echo "[4/5] Регистрация автозапуска systemd (bybit-bot-v36.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v36.service
[Unit]
Description=Bybit Multi-Alpha Omega Dual-Regime ZF-8 Engine v3.6 1h (Port 8089)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $SCRIPT_DIR/web_dashboard.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$APP_DIR

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-bot-v36.service
systemctl restart bybit-bot-v36.service

# Открытие порта 8089 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8089/tcp > /dev/null 2>&1 || true
fi

# 5. Проверка запуска
echo "[5/5] Проверка запуска демона v3.6..."
sleep 3

if systemctl is-active --quiet bybit-bot-v36; then
    echo "=================================================="
    echo "  [OK] Демон v3.6 Omega Dual-Regime ZF-8 успешно запущен!"
    echo "  Порт: 8089"
    echo "  Веб-дашборд: http://$(curl -s ifconfig.me || echo 'localhost'):8089"
    echo "=================================================="
else
    echo "[!] Ошибка: сервис bybit-bot-v36 не запустился. Логи:"
    journalctl -u bybit-bot-v36 -n 20 --no-pager
    exit 1
fi
