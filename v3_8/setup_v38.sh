#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha v3.8 NewDragon Trend Lab"
echo "   (1h Dynamic Universe 400+ // True 1R // 4-Stage FSM)"
echo "   (Порт 8091 // Stage 2 Maker TP 60%)"
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория: $APP_DIR"
echo "Директория v3.8: $SCRIPT_DIR"

mkdir -p "$SCRIPT_DIR/reports"

# Регистрация systemd службы bybit-bot-v38.service
echo "[1/3] Регистрация автозапуска systemd (bybit-bot-v38.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v38.service
[Unit]
Description=Bybit Multi-Alpha v3.8 NewDragon Trend Lab (Port 8091)
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
Environment=PORT_V38=8091
Environment=PAPER_TRADING=true
Environment=EQUITY_USDT=300.0

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-bot-v38.service
systemctl restart bybit-bot-v38.service

# Открытие порта 8091 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8091/tcp > /dev/null 2>&1 || true
fi

echo "[2/3] Проверка запуска демона v3.8..."
sleep 3

if systemctl is-active --quiet bybit-bot-v38; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo "=================================================="
    echo "  [OK] Демон v3.8 NewDragon Trend Lab успешно запущен!"
    echo "  Порт: 8091"
    echo "  Веб-дашборд: http://${SERVER_IP}:8091"
    echo "  API статуса: http://${SERVER_IP}:8091/api/status"
    echo "=================================================="
else
    echo "[!] Ошибка: сервис bybit-bot-v38 не запустился. Логи:"
    journalctl -u bybit-bot-v38 -n 25 --no-pager
fi
