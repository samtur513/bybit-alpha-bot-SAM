#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Scalper v3.7 HFT Microstructure"
echo "   (M15/M1 L2 Orderbook + Sweep Absorption + Lead-Lag)"
echo "   (Порт 8090 // 25ms Queue Monitoring)"
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория: $APP_DIR"
echo "Директория v3.7: $SCRIPT_DIR"

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
"$APP_DIR/venv/bin/pip" install -q websockets orjson 2>/dev/null || true

# 3. Инициализация чистого профиля состояния торговли
echo "[3/5] Проверка профиля состояния торговли v3.7 Scalper..."
if [ ! -f "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V37.json" ]; then
    cat << 'EOF_STATE' > "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V37.json"
{
  "version": "3.7.0",
  "timeframe": "15m/M1",
  "schema_version": 7,
  "mode": "HFT_MICROSTRUCTURE_SCALPER",
  "initial_capital": "300.0",
  "realized_equity": "300.0",
  "peak_equity": "300.0",
  "daily_pnl_usdt": 0.0,
  "open_positions": {},
  "pending_orders": {},
  "closed_trades": []
}
EOF_STATE
    echo "Создан начальный профиль капитала v3.7 Scalper: \$300.00 USDT"
fi

# 4. Регистрация systemd службы bybit-bot-v37.service
echo "[4/5] Регистрация автозапуска systemd (bybit-bot-v37.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v37.service
[Unit]
Description=Bybit Multi-Alpha Scalper HFT Engine v3.7 15m/M1 (Port 8090)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $SCRIPT_DIR/web_dashboard.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$APP_DIR
Environment=PORT=8090
Environment=PAPER_TRADING=true
Environment=EQUITY_USDT=300.0

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-bot-v37.service
systemctl restart bybit-bot-v37.service

# Открытие порта 8090 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8090/tcp > /dev/null 2>&1 || true
fi

# 5. Проверка запуска
echo "[5/5] Проверка запуска демона v3.7..."
sleep 3

if systemctl is-active --quiet bybit-bot-v37; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo "=================================================="
    echo "  [OK] Демон v3.7 Bybit Scalper успешно запущен!"
    echo "  Порт: 8090"
    echo "  Веб-дашборд: http://${SERVER_IP}:8090"
    echo "  API статуса: http://${SERVER_IP}:8090/api/status"
    echo "=================================================="
else
    echo "[!] Ошибка: сервис bybit-bot-v37 не запустился. Логи:"
    journalctl -u bybit-bot-v37 -n 25 --no-pager
fi
